from __future__ import annotations
import argparse,json,sqlite3,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_local_pending_reservation_v1 as lp

HEAD_FEATS=['seconds_left_norm','paircov','absnet_ratio','floor_ratio','weak_dom_avg_cost_diff','prev_delta_absnet','prev_delta_paircov','prev_delta_floor_ratio','gap_norm']

def norm_abs(u,d):
 g=u+d;return abs(u-d)/g if g>v1.EPS else 0.

def train_target_head(dbpath):
 c=sqlite3.connect(dbpath);c.row_factory=sqlite3.Row
 mend={(r['market_id']):r['window_end_ms'] for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH'")}
 rows=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
 X=[];y=[];ends=[];cur=None;u=d=uc=dc=0.;prev=None
 for r in rows:
  if r['market_id']!=cur:cur=r['market_id'];u=d=uc=dc=0.;prev=None
  total=u+d;cost=uc+dc;pair=2*min(u,d)/total if total>v1.EPS else 1.;ab=norm_abs(u,d);floor=min(u,d)-cost;weak='UP' if u<d-v1.EPS else 'DOWN' if d<u-v1.EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  avu=uc/u if u>v1.EPS else 0.;avd=dc/d if d>v1.EPS else 0.;wav=avu if weak=='UP' else avd if weak=='DOWN' else 0.;dav=avd if weak=='UP' else avu if weak=='DOWN' else 0.;end=mend.get(r['market_id']);sl=(end-int(r['first_event_ms']))/1000. if end else 0.
  if prev and prev['role']=='MAKER' and r['role']=='MAKER' and prev['dabs']>1e-6 and weak is not None:
   X.append([max(-30,min(330,sl))/300.,pair,ab,max(-5,min(5,floor/max(cost,1.))),wav-dav,max(-1,min(1,prev['dabs'])),max(-1,min(1,prev['dpair'])),max(-2,min(2,prev['dfloor'])),max(0,min(10,(int(r['first_event_ms'])-prev['t'])/10000.))]);y.append(1 if r['side']==weak else 0);ends.append(end or 0)
  preab=ab;prepair=pair;prefloor=floor;sh=float(r['shares']);px=float(r['average_price'])
  if r['side']=='UP':u+=sh;uc+=sh*px
  else:d+=sh;dc+=sh*px
  postcost=uc+dc;postpair=2*min(u,d)/(u+d) if u+d>v1.EPS else 1.;postfloor=min(u,d)-postcost;prev={'t':int(r['first_event_ms']),'role':r['role'],'dabs':norm_abs(u,d)-preab,'dpair':postpair-prepair,'dfloor':(postfloor-prefloor)/max(postcost,1.)}
 X=np.asarray(X,np.float32);y=np.asarray(y,int);ends=np.asarray(ends,np.int64);uniq=sorted(set(int(x) for x in ends));cut=uniq[int(len(uniq)*.7)];tr=np.where(ends<cut)[0];va=np.where(ends>=cut)[0]
 m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.,class_weight='balanced',random_state=29).fit(X[tr],y[tr]);p=m.predict_proba(X[va])[:,1]
 offline={'rows':len(y),'positiveRate':float(y.mean()),'trainN':len(tr),'validationN':len(va),'cutoffWindowEndMs':int(cut),'validationAuc':float(roc_auc_score(y[va],p)),'validationAP':float(average_precision_score(y[va],p))}
 final=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=3.,class_weight='balanced',random_state=29).fit(X,y)
 return final,offline

class PhaseSim(lp.LocalReservedBootSim):
 def __init__(self,tape,models,head,mode):
  super().__init__(tape,'BOOK_IMBALANCE',models);self.head=head;self.phase=None;self.lastTransition=None;self.phaseMode=mode;self.phaseOverrides=0;self.phaseWeak=0;self.phaseDom=0;self.phaseTriggers=0
 def process(self,t):
  preu,pred=float(self.inv['UP']),float(self.inv['DOWN']);prepair=2*min(preu,pred)/(preu+pred) if preu+pred>v1.EPS else 1.;preab=norm_abs(preu,pred);prefloor=min(preu,pred)-self.cost
  super().process(t)
  postu,postd=float(self.inv['UP']),float(self.inv['DOWN']);postpair=2*min(postu,postd)/(postu+postd) if postu+postd>v1.EPS else 1.;postab=norm_abs(postu,postd);postfloor=min(postu,postd)-self.cost
  if abs((postu+postd)-(preu+pred))>v1.EPS:
   dabs=postab-preab;self.lastTransition={'t':int(t),'dabs':dabs,'dpair':postpair-prepair,'dfloor':(postfloor-prefloor)/max(self.cost,1.)}
   if dabs>1e-6:self.phase='POST_EXPAND';self.phaseTriggers+=1
   elif dabs<-1e-6:self.phase='POST_REPAIR'
 def head_features(self,t,qv,end):
  u,d=float(self.inv['UP']),float(self.inv['DOWN']);g=u+d;pair=2*min(u,d)/g if g>v1.EPS else 1.;ab=norm_abs(u,d);floor=min(u,d)-self.cost;weak='UP' if u<d-v1.EPS else 'DOWN' if d<u-v1.EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
  avu=self.sideCost['UP']/u if u>v1.EPS else 0.;avd=self.sideCost['DOWN']/d if d>v1.EPS else 0.;wav=avu if weak=='UP' else avd if weak=='DOWN' else 0.;dav=avd if weak=='UP' else avu if weak=='DOWN' else 0.;tr=self.lastTransition or {'t':t,'dabs':0.,'dpair':0.,'dfloor':0.}
  return np.asarray([max(-30,min(330,(end-t)/1000.))/300.,pair,ab,max(-5,min(5,floor/max(self.cost,1.))),wav-dav,max(-1,min(1,tr['dabs'])),max(-1,min(1,tr['dpair'])),max(-2,min(2,tr['dfloor'])),max(0,min(10,(t-tr['t'])/10000.))],np.float32),weak,dom
 def run_phase(self,winner):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);ca=v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.firstValid is None:self.firstValid=t
   if (end-t)/1000.<=180:continue
   self.seed_if_needed(t,qv)
   if not self.seeded:continue
   x=self.features(t,qv,ca,end).reshape(1,-1);pa=float(self.models['action'].predict_proba(x)[0,1])
   if pa<self.models['actionTh']:continue
   ps=float(self.models['side'].predict_proba(x)[0,1]);side='UP' if ps>=self.models['sideTh'] else 'DOWN'
   h,weak,dom=self.head_features(t,qv,end)
   if self.phase=='POST_EXPAND' and weak is not None:
    if self.phaseMode=='ALWAYS_WEAK':chosen=weak
    else:chosen=weak if float(self.head.predict_proba(h.reshape(1,-1))[0,1])>=.5 else dom
    if chosen!=side:self.phaseOverrides+=1
    side=chosen;self.phaseWeak+=int(side==weak);self.phaseDom+=int(side==dom)
   qty=max(.01,float(np.expm1(np.clip(self.models['qty'].predict(x)[0],0,5))));p=float(qv[side]['bid']);qty=max(qty,1/p);qty=min(qty,12.);accepted=bool(self.submit(t,side,p,qty))
   if accepted and self.phase=='POST_EXPAND':self.phase='POST_EXPAND_CONSUMED'
  end2=int(self.meta['lastReceivedMs']);v1.ex.advance_to(self.bt,end2);self.process(end2);pnl=self.inv[str(winner).upper()]-self.cost;opp='UP' if str(winner).upper()=='DOWN' else 'DOWN';return {'pnl':pnl,'oppositePnl':self.inv[opp]-self.cost,'buyNotional':self.cost,'pairCoverage':2*min(self.inv.values())/sum(self.inv.values()) if sum(self.inv.values())>v1.EPS else 0.,'floor':min(self.inv.values())-self.cost,'absNet':abs(self.inv['UP']-self.inv['DOWN']),'submits':self.submits,'fills':self.fills,'phaseTriggers':self.phaseTriggers,'phaseOverrides':self.phaseOverrides,'phaseWeak':self.phaseWeak,'phaseDom':self.phaseDom}

def agg(rs):
 p=sum(r['pnl'] for r in rs);b=sum(r['buyNotional'] for r in rs);return {'markets':len(rs),'activeMarkets':sum(r['buyNotional']>v1.EPS for r in rs),'pnl':p,'buyNotional':b,'roi':p/b if b else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanPhaseTriggers':statistics.mean(r['phaseTriggers'] for r in rs),'meanPhaseOverrides':statistics.mean(r['phaseOverrides'] for r in rs),'weakFracWhenPhase':sum(r['phaseWeak'] for r in rs)/max(1,sum(r['phaseWeak']+r['phaseDom'] for r in rs))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_post_expand_head_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);head,headOff=train_target_head(a.target_db);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
  for mode in ('TARGET_HEAD','ALWAYS_WEAK'):
   rr=[]
   for i,cr in enumerate(test,1):
    sim=PhaseSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",models,head,mode)
    try:r=sim.run_phase(cr['winner'])
    finally:sim.close()
    r.update({'marketId':int(cr['marketId']),'mode':mode,'winner':cr['winner']});rr.append(r);rows.append(r)
    if i%20==0:print(json.dumps({'mode':mode,'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rr),'winsSoFar':sum(x['pnl']>0 for x in rr)}),flush=True)
  sums=[]
  for mode in ('TARGET_HEAD','ALWAYS_WEAK'):
   s=agg([r for r in rows if r['mode']==mode]);s['mode']=mode;sums.append(s)
  out={'version':'ETH_POST_EXPAND_TARGET_RESPONSIBILITY_HEAD_V1','researchOnly':True,'liveMutation':False,'targetHeadFeatures':HEAD_FEATS,'targetHeadOffline':headOff,'boundary':['Target head trained only on frozen ETH Maker->Maker transitions where previous Target event increased normalized abs-net','frozen Target snapshot ends before Fresh101 begins','base action timing/qty unchanged two-round DAgger + Local Pending Reservation','head only selects relative weak-vs-dominant side for the first accepted responsibility after a material fill increases normalized abs-net','ALWAYS_WEAK is structural control, not threshold sweep','no Fresh101 Target action/winner enters runtime; <=180s no new exposure'],'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'targetHeadOffline':headOff,'summaries':sums},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
