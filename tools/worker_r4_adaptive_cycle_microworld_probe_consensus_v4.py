from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1
import worker_r4_adaptive_cycle_microworld_attribution_v2 as v2

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[86001,86002];FAMILIES=v2.FAMILIES
POLICIES={
 'PROPOSE_ALWAYS':('always',0.0),
 'SINGLE_PROBE_0.002_REFERENCE':('single',.002),
 'DUAL_PROBE_CONSENSUS_0.0015':('dual',.0015),
 'DUAL_PROBE_CONSENSUS_0.0020':('dual',.0020),
 'DUAL_PROBE_CONSENSUS_0.0030':('dual',.0030),
}
TRAIN_N=6000;EVAL_N=3000;WARM_N=40000;REGIMES=v1.REGIMES;UNIQUE=v1.UNIQUE

def probe_score(model,X,y,seed):
 rng=np.random.default_rng(seed);ix=rng.permutation(len(X));a=int(.4*len(ix));b=int(.8*len(ix));tr=ix[:a];va=ix[a:b]
 before=v2.met(y[va],model.proba(X[va]));cand=model.clone();cand.train(X[tr],y[tr],seed+11,steps=6,lr=.02,batch=512);after=v2.met(y[va],cand.proba(X[va]))
 return float(max(after['ba']-before['ba'],before['ll']-after['ll']))

def decide(model,X,y,seed,mode,thr):
 if mode=='always':return True,[0.]
 s1=probe_score(model,X,y,seed)
 if mode=='single':return bool(s1>=thr),[s1]
 s2=probe_score(model,X,y,seed+100003)
 return bool(min(s1,s2)>=thr),[s1,s2]

def run_one(seed,family,policy,mode,thr):
 evalsets={nm:v2.world(EVAL_N,nm,family,seed+900000+i*127) for i,nm in enumerate(UNIQUE)};warm=v2.world(WARM_N,'BASE',family,seed+31)
 models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+40);base=v2.evalpair(models,*evalsets['BASE'])
 replay=[];proposed={'repair':0,'add':0};accepted={'repair':0,'add':0};rows=[]
 for ridx,reg in enumerate(REGIMES,1):
  cur=v2.world(TRAIN_N,reg['name'],family,seed+1000+ridx*23);ev=evalsets[reg['name']];Xc,yrc,yac=cur
  X,yr,ya=v1.concat_replay(cur,replay,max_replay=18000,seed=seed+ridx);anchor_names=['BASE']+[x['name'] for x in REGIMES[max(0,ridx-4):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=v2.anchors(models,evalsets,anchor_names);oldc=v2.evalpair(models,*ev);props={}
  for cap,ycur,yfull in [('repair',yrc,yr),('add',yac,ya)]:
   ok,_=decide(models[cap],Xc,ycur,seed+3000+ridx+(0 if cap=='repair' else 100),mode,thr);props[cap]=ok
   if not ok:continue
   proposed[cap]+=1;cand=models[cap].clone();cand.train(X,yfull,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24)
   trial=models.copy();trial[cap]=cand;newc=v2.evalpair(trial,*ev);newa=v2.anchors(trial,evalsets,anchor_names)
   if v2.accept(oldc,newc,olda,newa,cap):models[cap]=cand;accepted[cap]+=1;oldc=newc;olda=newa
  rows.append({'target':reg['target'],'proposedRepair':props['repair'],'proposedAdd':props['add']});replay.append(cur);replay=replay[-3:]
 per={nm:v2.evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 addonly=[x for x in rows if x['target']=='ADD'];reponly=[x for x in rows if x['target']=='REPAIR'];none=[x for x in rows if x['target']=='NONE']
 return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':float(np.mean(bas)),'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),'proposed':proposed,'accepted':accepted,'targetedRepairProposalRate':float(np.mean([x['proposedRepair'] for x in reponly])),'targetedAddProposalRate':float(np.mean([x['proposedAdd'] for x in addonly])),'unnecessaryRepairProposalOnAddOnly':float(np.mean([x['proposedRepair'] for x in addonly])),'unnecessaryAddProposalOnRepairOnly':float(np.mean([x['proposedAdd'] for x in reponly])),'falseProposalRateNone':float(np.mean([.5*(x['proposedRepair']+x['proposedAdd']) for x in none]))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol,(mode,thr) in POLICIES.items():runs.append(run_one(seed,fam,pol,mode,thr))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol];avg=lambda k:float(np.mean([x[k] for x in rr]))
  summary[pol]={k:avg(k) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','targetedRepairProposalRate','targetedAddProposalRate','unnecessaryRepairProposalOnAddOnly','unnecessaryAddProposalOnRepairOnly','falseProposalRateNone']};summary[pol]['meanProposalsPerRun']=float(np.mean([x['proposed']['repair']+x['proposed']['add'] for x in rr]));summary[pol]['meanAcceptedPerRun']=float(np.mean([x['accepted']['repair']+x['accepted']['add'] for x in rr]))
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_PROBE_CONSENSUS_V4_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'runs':len(runs),'episodesApprox':int(len(FAMILIES)*len(SEEDS)*len(POLICIES)*(WARM_N+len(REGIMES)*TRAIN_N+len(UNIQUE)*EVAL_N)),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic meta-learning only; no market-semantic or action promotion.','contract':'r4_adaptive_cycle_microworld_probe_consensus_v4_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
