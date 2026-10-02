from __future__ import annotations
import json, os, time, copy
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, log_loss
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[84001,84002,84003]
FAMILIES=['LINEAR','CORRELATED','NONLINEAR','CONTEXT_SPARSE','NOISY_EXECUTION']
POLICIES={'PROPOSE_ALWAYS':None,'ATTRIBUTED_DELTA_0.005':.005,'ATTRIBUTED_DELTA_0.010':.010,'ATTRIBUTED_DELTA_0.020':.020}
TRAIN_N=10000;EVAL_N=6000;WARM_N=60000;HIST=4
REGIMES=v1.REGIMES
UNIQUE=v1.UNIQUE

def sigmoid(x):return 1/(1+np.exp(-np.clip(x,-20,20)))

def world(n,regime,family,seed):
 rng=np.random.default_rng(seed);X=rng.normal(0,1,(n,v1.FEATURES)).astype(np.float32)
 if family=='CORRELATED':
  X[:,1]=.55*X[:,0]+.75*X[:,1];X[:,5]=.45*X[:,0]+.80*X[:,5];X[:,10]=.35*X[:,11]+.85*X[:,10]
 X[:,3]=(rng.random(n)<sigmoid(.2+.35*X[:,0])).astype(np.float32)*2-1
 X[:,4]=(rng.random(n)<sigmoid(.1+.30*X[:,2])).astype(np.float32)*2-1
 X[:,6]=np.tanh(X[:,6]);X[:,8]=np.tanh(X[:,8]);X[:,9]=np.tanh(X[:,9]);X[:,10]=np.tanh(X[:,10])
 zr=(-.55+1.25*X[:,0]-.45*X[:,1]+.35*X[:,3]+.85*X[:,5]-.55*X[:,6]+.25*X[:,7]+.15*X[:,11])
 za=(-.80-.65*X[:,0]+.75*X[:,1]+1.25*X[:,2]+.30*X[:,4]+.35*X[:,6]+.25*X[:,8]-.55*X[:,9]-.45*X[:,10]+.10*X[:,11])
 if family=='NONLINEAR':
  zr += .45*np.tanh(X[:,0]*X[:,5])-.25*np.tanh(X[:,1]*X[:,6])
  za += .50*np.tanh(X[:,1]*X[:,2])-.30*np.tanh(X[:,0]*X[:,10])
 elif family=='CONTEXT_SPARSE':
  zr += .70*((X[:,3]>0)&(X[:,5]>.4))*X[:,0]-.35*((X[:,3]<0)&(X[:,6]>.3))
  za += .75*((X[:,4]>0)&(X[:,2]>.4))*X[:,1]-.40*(X[:,9]>.5)
 elif family=='NOISY_EXECUTION':
  zr += rng.normal(0,.45,n)+.25*X[:,10]
  za += rng.normal(0,.45,n)-.20*X[:,10]
 name=regime
 if name=='REPAIR_SURGE':zr+=.70+.55*X[:,0]+.25*X[:,5]
 elif name=='ADD_SURGE':za+=.70+.55*X[:,2]+.25*X[:,1]
 elif name=='OWNER_STALL':zr+=.15+.80*X[:,5]+.45*X[:,3]
 elif name=='OPPORTUNITY_SURPLUS':za+=.10+.65*X[:,2]+.55*X[:,1]
 elif name=='DUAL_STRESS':zr+=.30+.35*X[:,0]+.35*X[:,5];za+=.30+.35*X[:,2]+.30*X[:,1]
 elif name=='COLD':zr-=.75;za-=.75
 elif name=='EXECUTION_FRICTION':zr+=.30+.65*X[:,10]+.25*X[:,0];za-=.10+.55*X[:,10]
 pr=sigmoid(zr);pa=sigmoid(za);yr=(rng.random(n)<pr).astype(np.int8);ya=(rng.random(n)<pa).astype(np.int8)
 return X,yr,ya

def met(y,p):
 m=v1.metric(y,p);m['ll']=float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]));return m

def evalpair(models,X,yr,ya):return {'repair':met(yr,models['repair'].proba(X)),'add':met(ya,models['add'].proba(X))}

def anchors(models,evalsets,names):
 rr=[];aa=[];r0=[];r1=[];a0=[];a1=[]
 for nm in names:
  q=evalpair(models,*evalsets[nm]);rr.append(q['repair']['ba']);aa.append(q['add']['ba']);r0.append(q['repair']['r0']);r1.append(q['repair']['r1']);a0.append(q['add']['r0']);a1.append(q['add']['r1'])
 return {'repairBA':float(np.mean(rr)),'addBA':float(np.mean(aa)),'repairR0':float(np.mean(r0)),'repairR1':float(np.mean(r1)),'addR0':float(np.mean(a0)),'addR1':float(np.mean(a1))}

def should_propose(hist,current,cap,delta):
 if delta is None:return True
 if len(hist)<2:return True
 recent=hist[-HIST:];ba=np.median([x['ba'] for x in recent]);ll=np.median([x['ll'] for x in recent]);r0=np.median([x['r0'] for x in recent]);r1=np.median([x['r1'] for x in recent])
 return bool(current['ba']<ba-delta or current['ll']>ll+delta or current['r0']<r0-max(.02,2*delta) or current['r1']<r1-max(.02,2*delta))

def accept(oldc,newc,olda,newa,cap):
 if newc[cap]['ba']<oldc[cap]['ba']-.0015 or newc[cap]['auc']<oldc[cap]['auc']-.002:return False
 if max(newc[cap]['ba']-oldc[cap]['ba'],newc[cap]['auc']-oldc[cap]['auc'])<.001:return False
 if cap=='repair':return bool(newa['repairBA']>=olda['repairBA']-.002 and newa['repairR0']>=olda['repairR0']-.02 and newa['repairR1']>=olda['repairR1']-.02)
 return bool(newa['addBA']>=olda['addBA']-.002 and newa['addR0']>=olda['addR0']-.02 and newa['addR1']>=olda['addR1']-.02)

def replay_mix(cur,replay,seed):return v1.concat_replay(cur,replay,max_replay=25000,seed=seed)

def run_one(seed,family,policy,delta):
 evalsets={nm:world(EVAL_N,nm,family,seed+900000+i*131) for i,nm in enumerate(UNIQUE)};warm=world(WARM_N,'BASE',family,seed+17)
 models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+20)
 base=evalpair(models,*evalsets['BASE']);hist={'repair':[],'add':[]};replay=[];rows=[];proposed={'repair':0,'add':0};accepted={'repair':0,'add':0}
 for ridx,reg in enumerate(REGIMES,1):
  cur=world(TRAIN_N,reg['name'],family,seed+1000+ridx*19);ev=evalsets[reg['name']];pre=evalpair(models,*ev)
  hist['repair'].append(pre['repair']);hist['add'].append(pre['add'])
  X,yr,ya=replay_mix(cur,replay,seed+ridx);anchor_names=['BASE']+[x['name'] for x in REGIMES[max(0,ridx-4):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=anchors(models,evalsets,anchor_names)
  prop={}
  for cap,y in [('repair',yr),('add',ya)]:
   prop[cap]=should_propose(hist[cap][:-1],pre[cap],cap,delta)
   if not prop[cap]:continue
   proposed[cap]+=1;cand=models[cap].clone();cand.train(X,y,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24)
   trial=models.copy();trial[cap]=cand;newc=evalpair(trial,*ev);newa=anchors(trial,evalsets,anchor_names)
   if accept(pre,newc,olda,newa,cap):models[cap]=cand;accepted[cap]+=1;pre=newc;olda=newa
  post=evalpair(models,*ev);rows.append({'round':ridx,'target':reg['target'],'regime':reg['name'],'proposedRepair':prop['repair'],'proposedAdd':prop['add'],'repairPreBA':pre['repair']['ba'],'repairPostBA':post['repair']['ba'],'addPreBA':pre['add']['ba'],'addPostBA':post['add']['ba']})
  replay.append(cur);replay=replay[-3:]
 per={nm:evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 addonly=[x for x in rows if x['target']=='ADD'];reponly=[x for x in rows if x['target']=='REPAIR'];none=[x for x in rows if x['target']=='NONE']
 return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':float(np.mean(bas)),'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),
  'proposed':proposed,'accepted':accepted,'targetedRepairProposalRate':float(np.mean([x['proposedRepair'] for x in reponly])),'targetedAddProposalRate':float(np.mean([x['proposedAdd'] for x in addonly])),'unnecessaryRepairProposalOnAddOnly':float(np.mean([x['proposedRepair'] for x in addonly])),'unnecessaryAddProposalOnRepairOnly':float(np.mean([x['proposedAdd'] for x in reponly])),'falseProposalRateNone':float(np.mean([.5*(x['proposedRepair']+x['proposedAdd']) for x in none]))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol,delta in POLICIES.items():runs.append(run_one(seed,fam,pol,delta))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol]
  def avg(k):return float(np.mean([x[k] for x in rr]))
  summary[pol]={k:avg(k) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','targetedRepairProposalRate','targetedAddProposalRate','unnecessaryRepairProposalOnAddOnly','unnecessaryAddProposalOnRepairOnly','falseProposalRateNone']}
  summary[pol]['meanProposalsPerRun']=float(np.mean([x['proposed']['repair']+x['proposed']['add'] for x in rr]));summary[pol]['meanAcceptedPerRun']=float(np.mean([x['accepted']['repair']+x['accepted']['add'] for x in rr]))
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_ATTRIBUTION_V2_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'runs':len(runs),'episodesApprox':int(len(FAMILIES)*len(SEEDS)*len(POLICIES)*(WARM_N+len(REGIMES)*TRAIN_N+len(UNIQUE)*EVAL_N)),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic meta-learning only; select proposal/rollback mechanics, never trading semantics or action authority.','contract':'r4_adaptive_cycle_microworld_attribution_v2_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
