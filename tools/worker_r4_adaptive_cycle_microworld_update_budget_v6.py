from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1
import worker_r4_adaptive_cycle_microworld_attribution_v2 as v2

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[88001,88002]
FAMILIES=v2.FAMILIES
TRAIN_N=6000;EVAL_N=3000;WARM_N=40000
BLOCKS=[('BASE','NONE'),('REPAIR_SURGE','REPAIR'),('BASE','NONE'),('ADD_SURGE','ADD'),('OWNER_STALL','REPAIR'),('OPPORTUNITY_SURPLUS','ADD'),('DUAL_STRESS','BOTH'),('BASE','NONE'),('EXECUTION_FRICTION','BOTH'),('BASE','NONE')]
ROUNDS=[{'name':n,'target':t,'block':b,'within':w} for b,(n,t) in enumerate(BLOCKS,1) for w in range(1,4)]
UNIQUE=[]
for n,_ in BLOCKS:
 if n not in UNIQUE:UNIQUE.append(n)
POLICIES=['FIXED_24','SCALED_8_16_24','SCALED_4_12_24','CAPPED_16']

def probe_gain(model,X,y,seed):
 rng=np.random.default_rng(seed);ix=rng.permutation(len(X));a=int(.4*len(ix));b=int(.8*len(ix));tr=ix[:a];va=ix[a:b]
 before=v2.met(y[va],model.proba(X[va]));cand=model.clone();cand.train(X[tr],y[tr],seed+17,steps=6,lr=.02,batch=512);after=v2.met(y[va],cand.proba(X[va]))
 return float(max(after['ba']-before['ba'],before['ll']-after['ll']))

def budget(policy,g):
 if policy=='FIXED_24':return 24
 if policy=='CAPPED_16':return 16
 if policy=='SCALED_8_16_24':return 8 if g<.004 else 16 if g<.008 else 24
 if policy=='SCALED_4_12_24':return 4 if g<.004 else 12 if g<.008 else 24
 raise KeyError(policy)

def mix(cur,replay,seed):return v1.concat_replay(cur,replay,max_replay=18000,seed=seed)

def run_one(seed,family,policy):
 evalsets={nm:v2.world(EVAL_N,nm,family,seed+900000+i*137) for i,nm in enumerate(UNIQUE)};warm=v2.world(WARM_N,'BASE',family,seed+31)
 models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+40);base=v2.evalpair(models,*evalsets['BASE']);replay=[]
 prop=acc=attempt_steps=accepted_steps=0
 for ridx,reg in enumerate(ROUNDS,1):
  cur=v2.world(TRAIN_N,reg['name'],family,seed+1000+ridx*31);ev=evalsets[reg['name']];Xc,yrc,yac=cur;X,yr,ya=mix(cur,replay,seed+ridx)
  anchor_names=['BASE']+[x['name'] for x in ROUNDS[max(0,ridx-5):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=v2.anchors(models,evalsets,anchor_names);oldc=v2.evalpair(models,*ev)
  for cap,ycur,yfull in [('repair',yrc,yr),('add',yac,ya)]:
   g=probe_gain(models[cap],Xc,ycur,seed+3000+ridx+(0 if cap=='repair' else 100))
   if g<.002:continue
   prop+=1;steps=budget(policy,g);attempt_steps+=steps;cand=models[cap].clone();cand.train(X,yfull,seed+5000+ridx+(0 if cap=='repair' else 100),steps=steps)
   trial=models.copy();trial[cap]=cand;newc=v2.evalpair(trial,*ev);newa=v2.anchors(trial,evalsets,anchor_names)
   if v2.accept(oldc,newc,olda,newa,cap):models[cap]=cand;acc+=1;accepted_steps+=steps;oldc=newc;olda=newa
  replay.append(cur);replay=replay[-3:]
 per={nm:v2.evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 meanba=float(np.mean(bas));return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':meanba,'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),'proposals':prop,'accepted':acc,'attemptedSteps':attempt_steps,'acceptedSteps':accepted_steps,'meanBAper1000AttemptedSteps':float(meanba/(max(1,attempt_steps)/1000.0))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol in POLICIES:runs.append(run_one(seed,fam,pol))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol]
  summary[pol]={k:float(np.mean([x[k] for x in rr])) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','proposals','accepted','attemptedSteps','acceptedSteps','meanBAper1000AttemptedSteps']}
  summary[pol]['attemptedStepReductionVsFixed24']=1.0-summary[pol]['attemptedSteps']/max(1e-9,float(np.mean([x['attemptedSteps'] for x in runs if x['policy']=='FIXED_24'])))
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_UPDATE_BUDGET_V6_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'episodesApprox':int(len(FAMILIES)*len(SEEDS)*len(POLICIES)*(WARM_N+len(ROUNDS)*TRAIN_N+len(UNIQUE)*EVAL_N)),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic update-budget meta-learning only; any selected schedule must return unchanged to realistic-HFT/fresh chronology.','contract':'r4_adaptive_cycle_microworld_update_budget_v6_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
