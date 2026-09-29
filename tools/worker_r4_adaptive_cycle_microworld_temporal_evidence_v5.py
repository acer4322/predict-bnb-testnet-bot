from __future__ import annotations
import json, os, time
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1
import worker_r4_adaptive_cycle_microworld_attribution_v2 as v2
import worker_r4_adaptive_cycle_microworld_probe_attribution_v3 as v3

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[87001,87002]
FAMILIES=v2.FAMILIES
TRAIN_N=6000; EVAL_N=3000; WARM_N=40000
BLOCKS=[
 ('BASE','NONE'),('REPAIR_SURGE','REPAIR'),('BASE','NONE'),('ADD_SURGE','ADD'),
 ('OWNER_STALL','REPAIR'),('OPPORTUNITY_SURPLUS','ADD'),('DUAL_STRESS','BOTH'),('BASE','NONE'),
 ('EXECUTION_FRICTION','BOTH'),('BASE','NONE')]
ROUNDS=[{'name':n,'target':t,'block':b,'within':w} for b,(n,t) in enumerate(BLOCKS,1) for w in range(1,4)]
UNIQUE=[]
for n,_ in BLOCKS:
 if n not in UNIQUE: UNIQUE.append(n)
POLICIES=['IMMEDIATE_PROBE_0.002','TWO_OF_THREE_PROBE_0.002','EWMA_PROBE','FAST_OR_PERSIST']

def probe_gain(model,X,y,seed):
 rng=np.random.default_rng(seed);ix=rng.permutation(len(X));a=int(.4*len(ix));b=int(.8*len(ix));tr=ix[:a];va=ix[a:b]
 before=v2.met(y[va],model.proba(X[va]));cand=model.clone();cand.train(X[tr],y[tr],seed+17,steps=6,lr=.02,batch=512);after=v2.met(y[va],cand.proba(X[va]))
 bg=after['ba']-before['ba'];lg=before['ll']-after['ll'];return float(max(bg,lg)),float(bg),float(lg)

def decide(policy,g,hist,ewma):
 if policy=='IMMEDIATE_PROBE_0.002': return g>=.002, ewma
 if policy=='TWO_OF_THREE_PROBE_0.002': return bool(g>=.002 and sum(x>=.002 for x in hist[-2:])>=1), ewma
 if policy=='EWMA_PROBE':
  e=.55*g+.45*ewma
  return bool(e>=.0022 and g>=0), e
 if policy=='FAST_OR_PERSIST':
  ok=(g>=.005) or (g>=.0015 and sum(x>=.0015 for x in hist[-2:])>=1)
  return bool(ok), ewma
 raise KeyError(policy)

def mix(cur,replay,seed): return v1.concat_replay(cur,replay,max_replay=18000,seed=seed)

def run_one(seed,family,policy):
 evalsets={nm:v2.world(EVAL_N,nm,family,seed+900000+i*131) for i,nm in enumerate(UNIQUE)}
 warm=v2.world(WARM_N,'BASE',family,seed+31);models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+40)
 base=v2.evalpair(models,*evalsets['BASE']);replay=[];proposed={'repair':0,'add':0};accepted={'repair':0,'add':0};hist={'repair':[],'add':[]};ew={'repair':0.0,'add':0.0};rows=[]
 for ridx,reg in enumerate(ROUNDS,1):
  cur=v2.world(TRAIN_N,reg['name'],family,seed+1000+ridx*29);ev=evalsets[reg['name']];Xc,yrc,yac=cur;X,yr,ya=mix(cur,replay,seed+ridx)
  anchor_names=['BASE']+[x['name'] for x in ROUNDS[max(0,ridx-5):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=v2.anchors(models,evalsets,anchor_names);oldc=v2.evalpair(models,*ev)
  props={};gains={}
  for cap,ycur,yfull in [('repair',yrc,yr),('add',yac,ya)]:
   g,bg,lg=probe_gain(models[cap],Xc,ycur,seed+3000+ridx+(0 if cap=='repair' else 100));ok,ew[cap]=decide(policy,g,hist[cap],ew[cap]);props[cap]=ok;gains[cap]={'gain':g,'baGain':bg,'llGain':lg};hist[cap].append(g);hist[cap]=hist[cap][-3:]
   if not ok: continue
   proposed[cap]+=1;cand=models[cap].clone();cand.train(X,yfull,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24)
   trial=models.copy();trial[cap]=cand;newc=v2.evalpair(trial,*ev);newa=v2.anchors(trial,evalsets,anchor_names)
   if v2.accept(oldc,newc,olda,newa,cap):models[cap]=cand;accepted[cap]+=1;oldc=newc;olda=newa
  rows.append({'round':ridx,'block':reg['block'],'within':reg['within'],'target':reg['target'],'regime':reg['name'],'proposedRepair':props['repair'],'proposedAdd':props['add'],'gains':gains})
  replay.append(cur);replay=replay[-3:]
 per={nm:v2.evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 # block metrics
 blockrows=[]
 for b,(nm,target) in enumerate(BLOCKS,1):
  z=[r for r in rows if r['block']==b];want=[]
  if target in ('REPAIR','BOTH'): want.append('repair')
  if target in ('ADD','BOTH'): want.append('add')
  det=[];delays=[]
  for cap in want:
   vals=[r['proposedRepair' if cap=='repair' else 'proposedAdd'] for r in z];det.append(any(vals));delays.append(next((i+1 for i,v in enumerate(vals) if v),4))
  blockrows.append({'block':b,'target':target,'correctDetected':float(np.mean(det)) if det else None,'meanDelay':float(np.mean(delays)) if delays else None,'repairProposalRate':float(np.mean([r['proposedRepair'] for r in z])),'addProposalRate':float(np.mean([r['proposedAdd'] for r in z]))})
 none=[x for x in rows if x['target']=='NONE'];rep=[x for x in rows if x['target']=='REPAIR'];add=[x for x in rows if x['target']=='ADD'];targetblocks=[x for x in blockrows if x['target']!='NONE']
 return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':float(np.mean(bas)),'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),'meanProposals':proposed['repair']+proposed['add'],'meanAccepted':accepted['repair']+accepted['add'],'targetBlockDetectionRate':float(np.mean([x['correctDetected'] for x in targetblocks])),'targetBlockMeanDelay':float(np.mean([x['meanDelay'] for x in targetblocks])),'falseProposalRateNone':float(np.mean([.5*(x['proposedRepair']+x['proposedAdd']) for x in none])),'wrongRepairOnAddOnly':float(np.mean([x['proposedRepair'] for x in add])),'wrongAddOnRepairOnly':float(np.mean([x['proposedAdd'] for x in rep]))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol in POLICIES:runs.append(run_one(seed,fam,pol))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol]
  summary[pol]={k:float(np.mean([x[k] for x in rr])) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','meanProposals','meanAccepted','targetBlockDetectionRate','targetBlockMeanDelay','falseProposalRateNone','wrongRepairOnAddOnly','wrongAddOnRepairOnly']}
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_TEMPORAL_EVIDENCE_V5_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'blocks':BLOCKS,'episodesApprox':int(len(FAMILIES)*len(SEEDS)*len(POLICIES)*(WARM_N+len(ROUNDS)*TRAIN_N+len(UNIQUE)*EVAL_N)),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic temporal meta-learning only; any selected proposal mechanic must return unchanged to realistic-HFT/fresh chronology.','contract':'r4_adaptive_cycle_microworld_temporal_evidence_v5_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
