from __future__ import annotations
import json, math, os, time, copy
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
FEATURES=12
SEEDS=[83001,83002,83003]
WARM=100000
ROUNDS=24
TRAIN_N=20000
EVAL_N=12000

REGIMES=[
 {'name':'BASE','target':'NONE'},
 {'name':'REPAIR_SURGE','target':'REPAIR'},
 {'name':'BASE','target':'NONE'},
 {'name':'ADD_SURGE','target':'ADD'},
 {'name':'OWNER_STALL','target':'REPAIR'},
 {'name':'OPPORTUNITY_SURPLUS','target':'ADD'},
 {'name':'DUAL_STRESS','target':'BOTH'},
 {'name':'COLD','target':'BOTH'},
 {'name':'BASE','target':'NONE'},
 {'name':'REPAIR_SURGE','target':'REPAIR'},
 {'name':'ADD_SURGE','target':'ADD'},
 {'name':'EXECUTION_FRICTION','target':'BOTH'},
 {'name':'OWNER_STALL','target':'REPAIR'},
 {'name':'OPPORTUNITY_SURPLUS','target':'ADD'},
 {'name':'DUAL_STRESS','target':'BOTH'},
 {'name':'COLD','target':'BOTH'},
 {'name':'BASE','target':'NONE'},
 {'name':'ADD_SURGE','target':'ADD'},
 {'name':'REPAIR_SURGE','target':'REPAIR'},
 {'name':'EXECUTION_FRICTION','target':'BOTH'},
 {'name':'OPPORTUNITY_SURPLUS','target':'ADD'},
 {'name':'OWNER_STALL','target':'REPAIR'},
 {'name':'DUAL_STRESS','target':'BOTH'},
 {'name':'BASE','target':'NONE'},
]
UNIQUE=[]
for r in REGIMES:
 if r['name'] not in UNIQUE: UNIQUE.append(r['name'])

def sig(x): return 1/(1+np.exp(-np.clip(x,-20,20)))

def make_world(n, regime, seed):
 rng=np.random.default_rng(seed)
 X=rng.normal(0,1,(n,FEATURES)).astype(np.float32)
 # semantic-ish normalized factors: 0 deficit,1 reserve,2 shaping-edge,3 weak-owner,4 dom-owner,
 # 5 stall,6 progress,7 age,8 early-time,9 late-time,10 execution friction,11 volatility.
 X[:,3]=(rng.random(n)<sig(.2+.35*X[:,0])).astype(np.float32)*2-1
 X[:,4]=(rng.random(n)<sig(.1+.30*X[:,2])).astype(np.float32)*2-1
 X[:,6]=np.tanh(X[:,6])
 X[:,8]=np.tanh(X[:,8]);X[:,9]=np.tanh(X[:,9]);X[:,10]=np.tanh(X[:,10])
 zr=(-.55 +1.25*X[:,0]-.45*X[:,1]+.35*X[:,3]+.85*X[:,5]-.55*X[:,6]+.25*X[:,7]+.15*X[:,11])
 za=(-.80 -.65*X[:,0]+.75*X[:,1]+1.25*X[:,2]+.30*X[:,4]+.35*X[:,6]+.25*X[:,8]-.55*X[:,9]-.45*X[:,10]+.10*X[:,11])
 name=regime
 if name=='REPAIR_SURGE': zr += .70+.55*X[:,0]+.25*X[:,5]
 elif name=='ADD_SURGE': za += .70+.55*X[:,2]+.25*X[:,1]
 elif name=='OWNER_STALL': zr += .15+.80*X[:,5]+.45*X[:,3]
 elif name=='OPPORTUNITY_SURPLUS': za += .10+.65*X[:,2]+.55*X[:,1]
 elif name=='DUAL_STRESS':
  zr += .30+.35*X[:,0]+.35*X[:,5]
  za += .30+.35*X[:,2]+.30*X[:,1]
 elif name=='COLD':
  zr -= .75; za -= .75
 elif name=='EXECUTION_FRICTION':
  zr += .30+.65*X[:,10]+.25*X[:,0]
  za -= .10+.55*X[:,10]
 pr=sig(zr);pa=sig(za)
 yr=(rng.random(n)<pr).astype(np.int8);ya=(rng.random(n)<pa).astype(np.int8)
 return X,yr,ya

def metric(y,p):
 y=np.asarray(y,int); p=np.asarray(p,float); z=(p>=.5).astype(int)
 pos=y==1;neg=~pos
 r1=float(z[pos].mean()) if pos.any() else 0.;r0=float((z[neg]==0).mean()) if neg.any() else 0.
 auc=float(roc_auc_score(y,p)) if len(np.unique(y))>1 else .5
 return {'auc':auc,'ba':.5*(r0+r1),'r0':r0,'r1':r1,'n':int(len(y)),'posRate':float(y.mean())}

class Logit:
 def __init__(self,d,seed):
  rng=np.random.default_rng(seed); self.w=rng.normal(0,.01,d+1).astype(np.float64)
 def clone(self):
  z=Logit(len(self.w)-1,1);z.w=self.w.copy();return z
 def proba(self,X): return sig(X@self.w[:-1]+self.w[-1])
 def train(self,X,y,seed,steps=24,lr=.035,batch=1024,l2=2e-4):
  if len(X)==0:return
  rng=np.random.default_rng(seed);y=np.asarray(y,float)
  pos=max(1.,y.sum());neg=max(1.,len(y)-y.sum());wp=len(y)/(2*pos);wn=len(y)/(2*neg)
  for _ in range(steps):
   ix=rng.integers(0,len(X),size=min(batch,len(X))); xb=X[ix].astype(np.float64);yb=y[ix]
   p=self.proba(xb);sw=np.where(yb>0,wp,wn);e=(p-yb)*sw
   gw=xb.T@e/len(ix)+l2*self.w[:-1];gb=float(e.mean())
   self.w[:-1]-=lr*gw;self.w[-1]-=lr*gb

def concat_replay(cur,replay,max_replay=40000,seed=0):
 X,y1,y2=cur
 if not replay:return X,y1,y2
 xr=np.concatenate([z[0] for z in replay],axis=0);r1=np.concatenate([z[1] for z in replay]);r2=np.concatenate([z[2] for z in replay])
 if len(xr)>max_replay:
  rng=np.random.default_rng(seed);ix=rng.choice(len(xr),max_replay,replace=False);xr=xr[ix];r1=r1[ix];r2=r2[ix]
 return np.concatenate([X,xr]),np.concatenate([y1,r1]),np.concatenate([y2,r2])

def eval_pair(models,X,yr,ya,shared=False):
 if shared:
  pa=models['shared'].proba(X);pr=1-pa
 else:
  pr=models['repair'].proba(X);pa=models['add'].proba(X)
 return {'repair':metric(yr,pr),'add':metric(ya,pa)}

def train_shared(models,X,yr,ya,seed):
 ex=yr!=ya
 if ex.sum()<100:return
 y=ya[ex]
 models['shared'].train(X[ex],y,seed)

def train_dual(models,X,yr,ya,seed):
 models['repair'].train(X,yr,seed)
 models['add'].train(X,ya,seed+1)

def anchor_metrics(models,evalsets,names,shared=False):
 rr=[];aa=[];r0=[];r1=[];a0=[];a1=[]
 for nm in names:
  X,yr,ya=evalsets[nm];m=eval_pair(models,X,yr,ya,shared)
  rr.append(m['repair']['ba']);aa.append(m['add']['ba']);r0.append(m['repair']['r0']);r1.append(m['repair']['r1']);a0.append(m['add']['r0']);a1.append(m['add']['r1'])
 return {'repairBA':float(np.mean(rr)),'addBA':float(np.mean(aa)),'repairR0':float(np.mean(r0)),'repairR1':float(np.mean(r1)),'addR0':float(np.mean(a0)),'addR1':float(np.mean(a1))}

def selective_accept(old_current,new_current,old_anchor,new_anchor,cap):
 oc=old_current[cap];nc=new_current[cap]
 ba_gain=nc['ba']-oc['ba'];auc_gain=nc['auc']-oc['auc']
 if ba_gain < -0.0015 or auc_gain < -0.002:return False
 if max(ba_gain,auc_gain)<0.001:return False
 if cap=='repair':
  if new_anchor['repairBA']<old_anchor['repairBA']-.002:return False
  if new_anchor['repairR0']<old_anchor['repairR0']-.02 or new_anchor['repairR1']<old_anchor['repairR1']-.02:return False
 else:
  if new_anchor['addBA']<old_anchor['addBA']-.002:return False
  if new_anchor['addR0']<old_anchor['addR0']-.02 or new_anchor['addR1']<old_anchor['addR1']-.02:return False
 return True

def run_seed(seed):
 evalsets={nm:make_world(EVAL_N,nm,seed+900000+i*97) for i,nm in enumerate(UNIQUE)}
 warm=make_world(WARM,'BASE',seed+101)
 variants={
  'SHARED_ZERO_SUM_PURPOSE':{'shared':Logit(FEATURES,seed+1)},
  'DUAL_ALWAYS_UPDATE':{'repair':Logit(FEATURES,seed+2),'add':Logit(FEATURES,seed+3)},
  'DUAL_REPLAY_ALWAYS_UPDATE':{'repair':Logit(FEATURES,seed+4),'add':Logit(FEATURES,seed+5)},
  'DUAL_REPLAY_SELECTIVE_ROLLBACK':{'repair':Logit(FEATURES,seed+6),'add':Logit(FEATURES,seed+7)},
 }
 train_shared(variants['SHARED_ZERO_SUM_PURPOSE'],*warm,seed+11)
 for k in ('DUAL_ALWAYS_UPDATE','DUAL_REPLAY_ALWAYS_UPDATE','DUAL_REPLAY_SELECTIVE_ROLLBACK'):train_dual(variants[k],*warm,seed+20)
 base_after={k:eval_pair(v,*evalsets['BASE'],shared=k.startswith('SHARED')) for k,v in variants.items()}
 replay=[];round_rows=[];accept={'repair':0,'add':0};reject={'repair':0,'add':0}
 for ridx,reg in enumerate(REGIMES,1):
  cur=make_world(TRAIN_N,reg['name'],seed+1000+ridx*13);ev=evalsets[reg['name']]
  before={k:eval_pair(v,*ev,shared=k.startswith('SHARED')) for k,v in variants.items()}
  # Shared zero-sum, replay always.
  sx,sr,sa=concat_replay(cur,replay,seed=seed+ridx);train_shared(variants['SHARED_ZERO_SUM_PURPOSE'],sx,sr,sa,seed+2000+ridx)
  # Dual current-only.
  train_dual(variants['DUAL_ALWAYS_UPDATE'],*cur,seed+3000+ridx)
  # Dual replay always.
  train_dual(variants['DUAL_REPLAY_ALWAYS_UPDATE'],sx,sr,sa,seed+4000+ridx)
  # Dual replay selective, each capability independently.
  sel=variants['DUAL_REPLAY_SELECTIVE_ROLLBACK']; anchor_names=['BASE']+[x['name'] for x in REGIMES[max(0,ridx-4):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names))
  old_anchor=anchor_metrics(sel,evalsets,anchor_names,False);old_cur=eval_pair(sel,*ev,False)
  for cap,y in [('repair',sr),('add',sa)]:
   cand=sel[cap].clone();cand.train(sx,y,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24)
   trial={'repair':sel['repair'],'add':sel['add']};trial=trial.copy();trial[cap]=cand
   new_cur=eval_pair(trial,*ev,False);new_anchor=anchor_metrics(trial,evalsets,anchor_names,False)
   if selective_accept(old_cur,new_cur,old_anchor,new_anchor,cap):sel[cap]=cand;accept[cap]+=1;old_cur=new_cur;old_anchor=new_anchor
   else:reject[cap]+=1
  after={k:eval_pair(v,*ev,shared=k.startswith('SHARED')) for k,v in variants.items()}
  for k in variants:
   row={'seed':seed,'round':ridx,'regime':reg['name'],'shiftTarget':reg['target'],'variant':k,
        'repairBeforeBA':before[k]['repair']['ba'],'repairAfterBA':after[k]['repair']['ba'],'addBeforeBA':before[k]['add']['ba'],'addAfterBA':after[k]['add']['ba'],
        'repairAfterR0':after[k]['repair']['r0'],'repairAfterR1':after[k]['repair']['r1'],'addAfterR0':after[k]['add']['r0'],'addAfterR1':after[k]['add']['r1']}
   round_rows.append(row)
  replay.append(cur)
  if len(replay)>3:replay.pop(0)
 final={}
 for k,v in variants.items():
  per={nm:eval_pair(v,*evalsets[nm],shared=k.startswith('SHARED')) for nm in UNIQUE}
  rb=[z['repair']['ba'] for z in per.values()];ab=[z['add']['ba'] for z in per.values()]
  recalls=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
  final[k]={'meanRepairBA':float(np.mean(rb)),'meanAddBA':float(np.mean(ab)),'meanCapabilityBA':float(np.mean(rb+ab)),'minCapabilityBA':float(min(rb+ab)),'worstClassRecall':float(min(recalls)),
            'baseRepairForgetting':float(per['BASE']['repair']['ba']-base_after[k]['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base_after[k]['add']['ba']),'perRegime':per}
 return {'seed':seed,'rounds':round_rows,'final':final,'selectiveAccepted':accept,'selectiveRejected':reject}

def summarize(runs):
 variants=list(runs[0]['final'])
 out={}
 for v in variants:
  fs=[r['final'][v] for r in runs];rows=[x for r in runs for x in r['rounds'] if x['variant']==v]
  add_only=[x for x in rows if x['shiftTarget']=='ADD'];rep_only=[x for x in rows if x['shiftTarget']=='REPAIR']
  out[v]={
   'finalMeanCapabilityBA':float(np.mean([x['meanCapabilityBA'] for x in fs])),
   'finalMinCapabilityBA':float(np.mean([x['minCapabilityBA'] for x in fs])),
   'finalWorstClassRecall':float(np.mean([x['worstClassRecall'] for x in fs])),
   'baseRepairForgetting':float(np.mean([x['baseRepairForgetting'] for x in fs])),
   'baseAddForgetting':float(np.mean([x['baseAddForgetting'] for x in fs])),
   'meanCurrentRepairBA':float(np.mean([x['repairAfterBA'] for x in rows])),
   'meanCurrentAddBA':float(np.mean([x['addAfterBA'] for x in rows])),
   'crossTalkRepairAbsDeltaOnAddOnly':float(np.mean([abs(x['repairAfterBA']-x['repairBeforeBA']) for x in add_only])),
   'crossTalkAddAbsDeltaOnRepairOnly':float(np.mean([abs(x['addAfterBA']-x['addBeforeBA']) for x in rep_only])),
  }
 selA={k:int(sum(r['selectiveAccepted'][k] for r in runs)) for k in ('repair','add')};selR={k:int(sum(r['selectiveRejected'][k] for r in runs)) for k in ('repair','add')}
 return out,selA,selR

def main():
 t=time.perf_counter();runs=[run_seed(s) for s in SEEDS];summary,acc,rej=summarize(runs)
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_CURRICULUM_V1_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,
      'episodesApprox':int(len(SEEDS)*(WARM+ROUNDS*TRAIN_N+len(UNIQUE)*EVAL_N)),'seeds':SEEDS,'roundsPerSeed':ROUNDS,'regimes':REGIMES,
      'summary':summary,'selectiveAccepted':acc,'selectiveRejected':rej,'elapsedSec':time.perf_counter()-t,
      'interpretationGuard':'Synthetic assumed-mechanism curriculum tests continual-learning mechanics only. It cannot validate market signs, Target fidelity, PnL, or action authority. Any chosen mechanic must return unchanged to realistic-HFT/fresh chronology.',
      'contract':'r4_adaptive_cycle_microworld_curriculum_v1_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
