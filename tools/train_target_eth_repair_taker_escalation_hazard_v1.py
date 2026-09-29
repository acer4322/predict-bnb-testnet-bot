from __future__ import annotations
import argparse,bisect,importlib.util,json,math,sqlite3,statistics
from pathlib import Path
from collections import defaultdict
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('core',HERE/'train_target_maker_taker_coordination_big_v1.py');core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
EPS=1e-9
EXTRA=['maker_fills_3s','maker_shares_3s','maker_repair_parents_1s','maker_repair_parents_3s','maker_repair_parents_5s','maker_repair_parents_10s','maker_repair_shares_1s','maker_repair_shares_3s','maker_repair_shares_5s','maker_repair_shares_10s','latest_maker_expansion_age_ms','latest_maker_expansion_repaid_frac']
FEATURES=core.CORE+core.BOOK+core.LIFE+EXTRA

def parents(c,mid):
 rows=[dict(r) for r in c.execute("select role,side,coalesce(nullif(order_hash,''),source_leg_id) order_hash,event_ms,price,shares from maker_book_inference_wallet_events where market_id=? and role in ('MAKER','TAKER') and quote_type='BID' and side in ('UP','DOWN') order by event_ms,rowid",(mid,))]
 by={}
 for e in rows:
  k=(e['role'],str(e['order_hash']),e['side']);z=by.setdefault(k,{'role':e['role'],'side':e['side'],'orderHash':str(e['order_hash']),'firstEventMs':int(e['event_ms']),'lastEventMs':int(e['event_ms']),'shares':0.0,'notional':0.0})
  z['firstEventMs']=min(z['firstEventMs'],int(e['event_ms']));z['lastEventMs']=max(z['lastEventMs'],int(e['event_ms']));z['shares']+=float(e['shares']);z['notional']+=float(e['shares'])*float(e['price'])
 pp=[]
 for z in by.values():
  pre=[e for e in rows if int(e['event_ms'])<z['firstEventMs']]; up=sum(float(e['shares']) for e in pre if e['side']=='UP');down=sum(float(e['shares']) for e in pre if e['side']=='DOWN');mup=sum(float(e['shares']) for e in pre if e['role']=='MAKER' and e['side']=='UP');mdown=sum(float(e['shares']) for e in pre if e['role']=='MAKER' and e['side']=='DOWN')
  preabs=abs(up-down);postabs=abs((up+z['shares']*(z['side']=='UP'))-(down+z['shares']*(z['side']=='DOWN')));mpre=abs(mup-mdown);mpost=abs((mup+z['shares']*(z['role']=='MAKER' and z['side']=='UP'))-(mdown+z['shares']*(z['role']=='MAKER' and z['side']=='DOWN')))
  if z['role']=='TAKER':
   if up+down<=EPS: eff='BUILD_FROM_FLAT'
   elif postabs<preabs-EPS: eff='REPAIR_EFFECT'
   elif postabs>preabs+EPS: eff='ADD_EFFECT'
   else: eff='FLAT_EFFECT'
  else: eff=None
  z.update({'avgPrice':z['notional']/z['shares'] if z['shares']>EPS else None,'preAbsNet':preabs,'postAbsNet':postabs,'deltaAbsNet':postabs-preabs,'preMakerAbsNet':mpre,'postMakerAbsNet':mpost,'deltaMakerAbsNet':mpost-mpre,'effect':eff})
  pp.append(z)
 return rows,sorted(pp,key=lambda x:(x['firstEventMs'],x['role'],x['orderHash']))

def rparents(pp,now,w):return [p for p in pp if p['role']=='MAKER' and p['deltaMakerAbsNet']<-EPS and p['lastEventMs']<=now and p['lastEventMs']>now-w]
def latest_expansion(pp,now):
 z=[p for p in pp if p['role']=='MAKER' and p['deltaMakerAbsNet']>EPS and p['firstEventMs']<=now]
 return max(z,key=lambda x:x['firstEventMs']) if z else None

def build(db):
 c=sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row;rows=[]
 try:
  metas=[dict(r) for r in c.execute('select * from maker_book_inference_markets where window_end_ms is not null order by market_id')]
  for mi,meta in enumerate(metas,1):
   mid=int(meta['market_id']); mend=int(meta['window_end_ms']); mstart=mend-300000; ev,pp=parents(c,mid); repair_t=[p for p in pp if p['role']=='TAKER' and p['effect']=='REPAIR_EFFECT']; rtimes=sorted(int(p['firstEventMs']) for p in repair_t)
   cps=list(range(mstart+500,mend-500,1000)); inv=core.Inventory();ei=0;ci=0;state={'bids':{},'asks':{}};last_update=None
   for u in c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)):
    ut=int(u['source_timestamp_ms'])
    while ci<len(cps) and cps[ci]<ut:
     cp=cps[ci]
     while ei<len(ev) and int(ev[ei]['event_ms'])<=cp:
      inv.apply({'event_ms':int(ev[ei]['event_ms']),'role':str(ev[ei]['role']),'side':str(ev[ei]['side']),'price':float(ev[ei]['price']),'shares':float(ev[ei]['shares'])});ei+=1
     age=cp-last_update if last_update is not None else 10**9
     if 0<=age<=2000:
      f=inv.features(cp);cn=float(f.pop('_combined_net'));dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None;bf=core.outcome_book(state,dom)
      if bf:
       ex=latest_expansion(pp,cp);extra={}
       # Event-level 3s fields to complement legacy 1/5/10s features.
       m3=[e for e in inv.events if e['role']=='MAKER' and cp-3000<int(e['event_ms'])<=cp];extra['maker_fills_3s']=float(len(m3));extra['maker_shares_3s']=float(sum(float(e['shares']) for e in m3))
       for w in (1000,3000,5000,10000):
        rp=rparents(pp,cp,w);extra[f'maker_repair_parents_{w//1000}s']=float(len(rp));extra[f'maker_repair_shares_{w//1000}s']=float(sum(p['shares'] for p in rp))
       if ex is None:extra['latest_maker_expansion_age_ms']=math.nan;extra['latest_maker_expansion_repaid_frac']=math.nan
       else:
        extra['latest_maker_expansion_age_ms']=float(cp-ex['firstEventMs']); start=max(ex['postMakerAbsNet']-ex['preMakerAbsNet'],EPS);cur=float(f['maker_abs_net']);extra['latest_maker_expansion_repaid_frac']=float((ex['postMakerAbsNet']-cur)/start)
       j=bisect.bisect_right(rtimes,cp);nxt=rtimes[j] if j<len(rtimes) else None
       rows.append({'market_id':mid,'market_end_ms':mend,'checkpoint_ms':cp,'book_age_ms':age,'seconds_left':(mend-cp)/1000.0,**f,**bf,**extra,'label_repair_taker_1s':int(nxt is not None and nxt<=cp+1000),'label_repair_taker_3s':int(nxt is not None and nxt<=cp+3000)})
     ci+=1
    if int(u['is_checkpoint']): state={'bids':{float(k):float(v) for k,v in (core.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (core.dec(u['native_asks_z']) or {}).items()}}
    else: core.apply_changes(state,core.dec(u['changes_z']) or {})
    last_update=ut
   if mi%50==0:print(json.dumps({'markets':mi,'of':len(metas),'rows':len(rows)}),flush=True)
  return rows
 finally:c.close()

def split(rows):
 mids=sorted({int(r['market_id']) for r in rows});a=max(1,int(len(mids)*.6));b=max(a+1,int(len(mids)*.8));tr=set(mids[:a]);va=set(mids[a:b]);te=set(mids[b:]);return {k:[r for r in rows if r['market_id'] in s] for k,s in [('train',tr),('validation',va),('test',te)]},{'trainMarkets':len(tr),'validationMarkets':len(va),'testMarkets':len(te),'trainMax':max(tr),'validationRange':[min(va),max(va)],'testMin':min(te)}
def mat(rr,label):return np.asarray([[np.nan if r.get(k) is None else float(r.get(k)) for k in FEATURES] for r in rr],np.float32),np.asarray([int(r[label]) for r in rr],int)
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);ix=np.argsort(-p);k=max(1,int(math.ceil(len(y)*.1)));return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'top10pctPositiveRate':float(y[ix[:k]].mean()) if len(y) else None,'top10pctRecall':float(y[ix[:k]].sum()/y.sum()) if y.sum()>0 else None}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-prefix',required=True);a=ap.parse_args();rows=build(a.db);parts,sp=split(rows);out={'version':'TARGET_ETH_REPAIR_TAKER_ESCALATION_HAZARD_V1','researchOnly':True,'coverage':{'rows':len(rows),'markets':len({r['market_id'] for r in rows})},'split':sp,'features':FEATURES,'horizons':{},'boundary':['Fresh ETH Target only.','Future REPAIR_EFFECT Taker is label only.','No winner/PnL/future side/size in features.','No BTC weights or numeric thresholds transferred.','No runtime Taker authority.']}
 for h in (1,3):
  lab=f'label_repair_taker_{h}s';Xtr,ytr=mat(parts['train'],lab);Xv,yv=mat(parts['validation'],lab);Xt,yt=mat(parts['test'],lab);m=HistGradientBoostingClassifier(max_iter=300,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=30,l2_regularization=2.0,class_weight='balanced',random_state=9200+h).fit(Xtr,ytr);pv=m.predict_proba(Xv)[:,1];pt=m.predict_proba(Xt)[:,1];ptr=m.predict_proba(Xtr)[:,1];mp=f'{a.model_prefix}_{h}s.joblib';joblib.dump({'version':'TARGET_ETH_REPAIR_TAKER_ESCALATION_HAZARD_V1','horizonSec':h,'features':FEATURES,'model':m},mp);out['horizons'][str(h)]={'train':metrics(ytr,ptr),'validation':metrics(yv,pv),'test':metrics(yt,pt),'model':mp}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
