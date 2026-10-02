from __future__ import annotations
import argparse,json,math,sqlite3,random,os
from pathlib import Path
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
SEED=20260831; random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
TASKS=('repair','switch','reentry'); SEQ=8; MAX_LAG=3
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','prev_maker_rel','prev_maker_age_log','avg_cost_gap','gross_log']
TOK_FEATURES=['prev_rel','delta_absnet','delta_paircov','delta_floor','elapsed_log','qty_gap_ratio','price','post_absnet','post_paircov']
DEBT_FEATURES=['lag_norm','hidden_gross_ratio','hidden_signed_ratio','hidden_cost_ratio','truth_minus_obs_absnet','truth_minus_obs_paircov','truth_minus_obs_floor','active_objective_rel','active_objective_streak','active_objective_age_log','cum_repair_absnet_progress','cum_repair_pair_gain','cum_repair_floor_gain','active_segment_progress']

def rel_for(side,up,dn):
 if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
 return 1 if ((side=='UP' and up<dn) or (side=='DOWN' and dn<up)) else -1

def state_features(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t):
 gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>1e-9 else 1.;ab=gap/gross if gross>1e-9 else 0.;fr=(pair-cost)/max(cost,1.);br=(max(up,dn)-cost)/max(cost,1.);avu=upcost/up if up>1e-9 else 0.;avd=dncost/dn if dn>1e-9 else 0.
 cur=np.asarray([max(-30,min(330,(end-t)/1000))/300 if end else 0.,pc,ab,max(-5,min(5,fr)),max(-5,min(5,br)),taker_n/max(maker_n+taker_n,1),float(prev_rel),math.log1p(min(prev_age,120000))/math.log1p(120000),max(-1,min(1,avu-avd)),math.log1p(gross)/math.log1p(500)],np.float32)
 return cur,{'gross':gross,'gap':gap,'pc':pc,'ab':ab,'fr':fr}

def build(db):
 c=sqlite3.connect(db);c.row_factory=sqlite3.Row
 mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
 ends=sorted(set(mend.values()));cut=ends[int(len(ends)*.70)]
 raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
 rows=[];curmid=None;row_id=0
 up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;makers=[]
 for r in raw:
  mid=int(r['market_id'])
  if mid!=curmid:
   curmid=mid;up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;makers=[]
  role=str(r['role']);side=str(r['side']);t=int(r['first_event_ms']);sh=float(r['shares']);px=float(r['average_price']);end=mend.get(mid)
  true_cur,true_met=state_features(up,dn,cost,upcost,dncost,maker_n,taker_n,makers[-1]['rel'] if makers else 0,(t-makers[-1]['time']) if makers else 1e6,end,t)
  rel=rel_for(side,up,dn) if role=='MAKER' else 0
  if role=='MAKER' and rel!=0:
   prev_auth=makers[-1]['rel'] if makers else 0
   switch=None if prev_auth==0 else int(rel!=prev_auth); reentry=None if prev_auth!=1 else int(rel==-1)
   active_rel=prev_auth; streak=0
   if makers:
    for m in reversed(makers):
     if m['rel']==active_rel:streak+=1
     else:break
   auth_age=(t-makers[-1]['time']) if makers else 1e6
   repair_hist=[m for m in makers if m['rel']==1]
   cra=sum(max(0.,-m['token'][1]) for m in repair_hist); crp=sum(m['token'][2] for m in repair_hist); crf=sum(m['token'][3] for m in repair_hist)
   seg=[m for m in makers[-streak:]] if streak else []
   segprog=sum((-m['token'][1] if active_rel==1 else m['token'][1]) for m in seg) if seg else 0.
   for lag in range(MAX_LAG+1):
    hidden=makers[-lag:] if lag else [];visible=makers[:-lag] if lag else makers
    hup=sum(m['shares'] for m in hidden if m['side']=='UP');hdn=sum(m['shares'] for m in hidden if m['side']=='DOWN');hcost=sum(m['shares']*m['price'] for m in hidden);hupcost=sum(m['shares']*m['price'] for m in hidden if m['side']=='UP');hdncost=sum(m['shares']*m['price'] for m in hidden if m['side']=='DOWN')
    oup,odn,ocost,oupc,odnc=up-hup,dn-hdn,cost-hcost,upcost-hupcost,dncost-hdncost
    oprev=visible[-1]['rel'] if visible else 0;oage=(t-visible[-1]['time']) if visible else 1e6
    ocur,omet=state_features(oup,odn,ocost,oupc,odnc,max(0,maker_n-lag),taker_n,oprev,oage,end,t)
    seq=np.zeros((SEQ,len(TOK_FEATURES)),np.float32);mask=np.zeros(SEQ,np.float32);vv=visible[-SEQ:]
    if vv:
     seq[-len(vv):]=np.asarray([m['token'] for m in vv],np.float32);mask[-len(vv):]=1.
    tg=max(true_met['gross'],1.);tc=max(cost,1.)
    debt=np.asarray([lag/MAX_LAG,(hup+hdn)/tg,(hup-hdn)/tg,hcost/tc,true_met['ab']-omet['ab'],true_met['pc']-omet['pc'],max(-5,min(5,true_met['fr']-omet['fr'])),float(active_rel),min(streak,12)/12.,math.log1p(min(auth_age,120000))/math.log1p(120000),min(cra,3.),max(-3,min(3,crp)),max(-3,min(3,crf)),max(-3,min(3,segprog))],np.float32)
    rows.append({'row_id':row_id,'end':end,'lag':lag,'cur':ocur,'seq':seq,'mask':mask,'debt':debt,'repair':int(rel==1),'switch':switch,'reentry':reentry})
   row_id+=1
  pre_ab,pre_pc,pre_fr=true_met['ab'],true_met['pc'],true_met['fr'];pre_gap=true_met['gap'];prev_time=makers[-1]['time'] if makers else None
  if side=='UP':up+=sh;upcost+=sh*px
  else:dn+=sh;dncost+=sh*px
  cost+=sh*px
  if role=='MAKER' and rel!=0:
   _,post=state_features(up,dn,cost,upcost,dncost,maker_n+1,taker_n,rel,1,end,t)
   elapsed=(t-prev_time) if prev_time is not None else 1e6; qgr=sh/max(pre_gap,1.)
   token=np.asarray([float(rel),post['ab']-pre_ab,post['pc']-pre_pc,max(-5,min(5,post['fr']-pre_fr)),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,qgr)),px,post['ab'],post['pc']],np.float32)
   makers.append({'rel':rel,'time':t,'side':side,'shares':sh,'price':px,'token':token});maker_n+=1
  elif role=='TAKER':taker_n+=1
 return rows,cut,len(ends),row_id

def standardize(train,test):
 clean=[r for r in train if r['lag']==0];X=np.stack([r['cur'] for r in clean]);mu=X.mean(0);sd=np.where(X.std(0)<1e-5,1,X.std(0));T=np.concatenate([r['seq'][r['mask']>0] for r in clean if r['mask'].sum()>0],0);tmu=T.mean(0);tsd=np.where(T.std(0)<1e-5,1,T.std(0));D=np.stack([r['debt'] for r in train]);dmu=D.mean(0);dsd=np.where(D.std(0)<1e-5,1,D.std(0))
 for z in (train,test):
  for r in z:
   r['curz']=((r['cur']-mu)/sd).astype(np.float32);r['seqz']=((r['seq']-tmu)/tsd).astype(np.float32)*r['mask'][:,None];r['debtz']=((r['debt']-dmu)/dsd).astype(np.float32)
 return mu,sd,tmu,tsd,dmu,dsd

class Memory(nn.Module):
 def __init__(self):super().__init__();self.gru=nn.GRU(len(TOK_FEATURES),32,batch_first=True);self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),32),nn.ReLU());self.mix=nn.Sequential(nn.Linear(64,48),nn.ReLU(),nn.LayerNorm(48));self.h=nn.ModuleDict({t:nn.Linear(48,1) for t in TASKS})
 def forward(self,cur,seq,mask,t):_,h=self.gru(seq);z=torch.cat([self.cur(cur),h[-1]],1);return self.h[t](self.mix(z)).squeeze(-1)
class DualMemory(nn.Module):
 def __init__(self):
  super().__init__();self.gru=nn.GRU(len(TOK_FEATURES),32,batch_first=True);self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),32),nn.ReLU());self.debt=nn.Sequential(nn.Linear(len(DEBT_FEATURES),24),nn.ReLU(),nn.LayerNorm(24));self.mix=nn.Sequential(nn.Linear(88,56),nn.ReLU(),nn.LayerNorm(56));self.h=nn.ModuleDict({t:nn.Linear(56,1) for t in TASKS})
 def forward(self,cur,seq,mask,debt,t):_,h=self.gru(seq);z=torch.cat([self.cur(cur),h[-1],self.debt(debt)],1);return self.h[t](self.mix(z)).squeeze(-1)

def arrays(rows,task,lag=None):
 z=[r for r in rows if r[task] is not None and (lag is None or r['lag']==lag)]
 return z,np.stack([r['curz'] for r in z]),np.stack([r['seqz'] for r in z]),np.stack([r['mask'] for r in z]),np.stack([r['debtz'] for r in z]),np.asarray([r[task] for r in z],np.float32)

def train_model(model,rows,dev,dual=False,clean_only=False):
 model.to(dev);opt=torch.optim.AdamW(model.parameters(),lr=1.15e-3,weight_decay=1e-4);rng=np.random.default_rng(SEED);src=[r for r in rows if (r['lag']==0 if clean_only else True)];pools={t:arrays(src,t)[1:] for t in TASKS}
 for ep in range(12):
  for _ in range(140):
   opt.zero_grad();L=0.
   for t in TASKS:
    X,S,M,D,y=pools[t];idx=rng.integers(0,len(y),size=min(320,len(y)));xt=torch.from_numpy(X[idx]).to(dev);st=torch.from_numpy(S[idx]).to(dev);mt=torch.from_numpy(M[idx]).to(dev);dt=torch.from_numpy(D[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev);log=model(xt,st,mt,dt,t) if dual else model(xt,st,mt,t);pos=max(float(yt.mean()),1e-4);L+=nn.functional.binary_cross_entropy_with_logits(log,yt,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.25,4))
   L.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);opt.step()
 return model

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def predict(model,rows,task,lag,dev,dual=False):
 z,X,S,M,D,y=arrays(rows,task,lag);ps=[];model.eval()
 with torch.no_grad():
  for i in range(0,len(y),4096):
   xt=torch.from_numpy(X[i:i+4096]).to(dev);st=torch.from_numpy(S[i:i+4096]).to(dev);mt=torch.from_numpy(M[i:i+4096]).to(dev);dt=torch.from_numpy(D[i:i+4096]).to(dev);log=model(xt,st,mt,dt,task) if dual else model(xt,st,mt,task);ps.append(torch.sigmoid(log).cpu().numpy())
 return z,y,np.concatenate(ps)

def evaluate(model,rows,dev,dual=False):
 out={};preds={}
 for lag in range(MAX_LAG+1):
  out[str(lag)]={}
  for t in TASKS:
   z,y,p=predict(model,rows,t,lag,dev,dual);out[str(lag)][t]=met(y,p);preds[(lag,t)]={r['row_id']:float(q) for r,q in zip(z,p)}
 drift={}
 for t in TASKS:
  a=preds[(0,t)];b=preds[(MAX_LAG,t)];ids=sorted(set(a)&set(b));drift[t]=float(np.mean([abs(a[i]-b[i]) for i in ids]))
 return out,drift

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();rows,cut,nwin,base_n=build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];stats=standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
 torch.manual_seed(SEED);clean=train_model(Memory(),tr,dev,False,True);torch.manual_seed(SEED);aug=train_model(Memory(),tr,dev,False,False);torch.manual_seed(SEED);dual=train_model(DualMemory(),tr,dev,True,False)
 mc,dc=evaluate(clean,te,dev,False);ma,da=evaluate(aug,te,dev,False);md,dd=evaluate(dual,te,dev,True)
 dlag3={t:md[str(MAX_LAG)][t]['auc']-ma[str(MAX_LAG)][t]['auc'] for t in TASKS};clean_delta={t:md['0'][t]['auc']-mc['0'][t]['auc'] for t in TASKS};drift_gain={t:da[t]-dd[t] for t in TASKS}
 passed=sum(v>=.015 for v in dlag3.values())>=2 and min(dlag3.values())>=-.01 and sum(v>0 for v in drift_gain.values())>=2 and min(clean_delta.values())>=-.015
 out={'version':'ETH_OBSERVATION_DEBT_LIFECYCLE_MEMORY_V1','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'baseDecisionRows':base_n,'expandedRows':len(rows),'trainRows':len(tr),'testRows':len(te),'maxHiddenPriorMakerFills':MAX_LAG,'features':{'current':CUR_FEATURES,'token':TOK_FEATURES,'debt':DEBT_FEATURES},'models':{'CLEAN_GRU':mc,'LAG_AUGMENTED_GRU_NO_DEBT':ma,'DUAL_STATE_GRU_WITH_DEBT':md},'predictionDriftCleanVsLag3':{'cleanGru':dc,'augmentedNoDebt':da,'dualStateDebt':dd},'lag3AucDeltaDualMinusAugmented':dlag3,'cleanAucDeltaDualMinusCleanGru':clean_delta,'driftReductionDualVsAugmented':drift_gain,'robustnessPass':bool(passed),'passRule':'lag3 dual vs augmented >=+0.015 on >=2/3, none <-0.01; dual reduces drift on >=2/3; clean dual loss nowhere >0.015','boundary':['ETH-only Target actual-filled parent chronology for labels/gradients','BTC informed architecture selection only; no BTC examples/labels/thresholds/gradients','Hidden-fill perturbations use only already-materialized strict-past Maker events','No winner/future PnL','No TARGET_UNIT=18 or expected_parent_shares','Representation robustness only; realistic-HFT Repair Functional Exam still required']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');mu,sd,tmu,tsd,dmu,dsd=stats;torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'tokenFeatures':TOK_FEATURES,'debtFeatures':DEBT_FEATURES,'mu':mu,'sd':sd,'tmu':tmu,'tsd':tsd,'dmu':dmu,'dsd':dsd,'dual_state_dict':dual.state_dict(),'pass':bool(passed)},a.model_out);print(json.dumps({'ok':True,'robustnessPass':passed,'lag3AucDeltaDualMinusAugmented':dlag3,'cleanAucDeltaDualMinusCleanGru':clean_delta,'driftReductionDualVsAugmented':drift_gain},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
