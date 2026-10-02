from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1
import worker_r4_adaptive_cycle_microworld_attribution_v2 as v2

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[85001,85002]
FAMILIES=v2.FAMILIES
POLICIES={'PROPOSE_ALWAYS':None,'PROBE_GAIN_0.002':.002,'PROBE_GAIN_0.005':.005,'PROBE_GAIN_0.010':.010}
TRAIN_N=6000;EVAL_N=3000;WARM_N=40000
REGIMES=v1.REGIMES;UNIQUE=v1.UNIQUE

def probe_decision(model,X,y,seed,threshold):
 if threshold is None:return True,0.,0.
 rng=np.random.default_rng(seed);ix=rng.permutation(len(X));a=int(.4*len(ix));b=int(.8*len(ix));tr=ix[:a];va=ix[a:b]
 if len(tr)<100 or len(va)<100:return True,0.,0.
 before=v2.met(y[va],model.proba(X[va]));cand=model.clone();cand.train(X[tr],y[tr],seed+17,steps=6,lr=.02,batch=512)
 after=v2.met(y[va],cand.proba(X[va]));ba_gain=after['ba']-before['ba'];ll_gain=before['ll']-after['ll']
 return bool(ba_gain>=threshold or ll_gain>=threshold),float(ba_gain),float(ll_gain)

def mix(cur,replay,seed):return v1.concat_replay(cur,replay,max_replay=18000,seed=seed)

def run_one(seed,family,policy,threshold):
 evalsets={nm:v2.world(EVAL_N,nm,family,seed+900000+i*127) for i,nm in enumerate(UNIQUE)};warm=v2.world(WARM_N,'BASE',family,seed+31)
 models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+40)
 base=v2.evalpair(models,*evalsets['BASE']);replay=[];proposed={'repair':0,'add':0};accepted={'repair':0,'add':0};rows=[]
 for ridx,reg in enumerate(REGIMES,1):
  cur=v2.world(TRAIN_N,reg['name'],family,seed+1000+ridx*23);ev=evalsets[reg['name']];Xc,yrc,yac=cur
  X,yr,ya=mix(cur,replay,seed+ridx);anchor_names=['BASE']+[x['name'] for x in REGIMES[max(0,ridx-4):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=v2.anchors(models,evalsets,anchor_names);oldc=v2.evalpair(models,*ev)
  props={};probe={}
  for cap,ycur,yfull in [('repair',yrc,yr),('add',yac,ya)]:
   ok,bg,lg=probe_decision(models[cap],Xc,ycur,seed+3000+ridx+(0 if cap=='repair' else 100),threshold);props[cap]=ok;probe[cap]={'baGain':bg,'llGain':lg}
   if not ok:continue
   proposed[cap]+=1;cand=models[cap].clone();cand.train(X,yfull,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24)
   trial=models.copy();trial[cap]=cand;newc=v2.evalpair(trial,*ev);newa=v2.anchors(trial,evalsets,anchor_names)
   if v2.accept(oldc,newc,olda,newa,cap):models[cap]=cand;accepted[cap]+=1;oldc=newc;olda=newa
  rows.append({'round':ridx,'target':reg['target'],'regime':reg['name'],'proposedRepair':props['repair'],'proposedAdd':props['add'],'probeRepair':probe['repair'],'probeAdd':probe['add']})
  replay.append(cur);replay=replay[-3:]
 per={nm:v2.evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 addonly=[x for x in rows if x['target']=='ADD'];reponly=[x for x in rows if x['target']=='REPAIR'];none=[x for x in rows if x['target']=='NONE']
 return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':float(np.mean(bas)),'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),
 'proposed':proposed,'accepted':accepted,'targetedRepairProposalRate':float(np.mean([x['proposedRepair'] for x in reponly])),'targetedAddProposalRate':float(np.mean([x['proposedAdd'] for x in addonly])),'unnecessaryRepairProposalOnAddOnly':float(np.mean([x['proposedRepair'] for x in addonly])),'unnecessaryAddProposalOnRepairOnly':float(np.mean([x['proposedAdd'] for x in reponly])),'falseProposalRateNone':float(np.mean([.5*(x['proposedRepair']+x['proposedAdd']) for x in none]))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol,thr in POLICIES.items():runs.append(run_one(seed,fam,pol,thr))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol]
  def avg(k):return float(np.mean([x[k] for x in rr]))
  summary[pol]={k:avg(k) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','targetedRepairProposalRate','targetedAddProposalRate','unnecessaryRepairProposalOnAddOnly','unnecessaryAddProposalOnRepairOnly','falseProposalRateNone']}
  summary[pol]['meanProposalsPerRun']=float(np.mean([x['proposed']['repair']+x['proposed']['add'] for x in rr]));summary[pol]['meanAcceptedPerRun']=float(np.mean([x['accepted']['repair']+x['accepted']['add'] for x in rr]))
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_PROBE_ATTRIBUTION_V3_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'runs':len(runs),'episodesApprox':int(len(FAMILIES)*len(SEEDS)*len(POLICIES)*(WARM_N+len(REGIMES)*TRAIN_N+len(UNIQUE)*EVAL_N)),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic meta-learning only. Probe attribution decides whether to spend learning effort; it cannot validate trading semantics or action authority.','contract':'r4_adaptive_cycle_microworld_probe_attribution_v3_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
