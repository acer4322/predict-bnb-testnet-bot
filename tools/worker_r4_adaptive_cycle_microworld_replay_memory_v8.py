from __future__ import annotations
import json, os, time, math
from pathlib import Path
import numpy as np
import worker_r4_adaptive_cycle_microworld_curriculum_v1 as v1
import worker_r4_adaptive_cycle_microworld_attribution_v2 as v2

OUT=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
SEEDS=[90001,90002,90003]
FAMILIES=v2.FAMILIES
TRAIN_N=6000;EVAL_N=3000;WARM_N=40000;MAX_REPLAY=18000
POLICIES=['FIFO3','RESERVOIR6','HARD6','DIVERSE6','HYBRID_RECENT_HARD_DIVERSE6']
CYCLE=[('BASE','NONE'),('REPAIR_SURGE','REPAIR'),('ADD_SURGE','ADD'),('OWNER_STALL','REPAIR'),('OPPORTUNITY_SURPLUS','ADD'),('DUAL_STRESS','BOTH'),('EXECUTION_FRICTION','BOTH'),('COLD','BOTH'),('BASE','NONE')]
ROUNDS=[]
for cyc in range(4):
 seq=CYCLE if cyc%2==0 else [CYCLE[i] for i in [0,2,1,4,3,6,5,7,8]]
 for j,(n,t) in enumerate(seq):ROUNDS.append({'name':n,'target':t,'cycle':cyc+1,'slot':j+1})
UNIQUE=[]
for r in ROUNDS:
 if r['name'] not in UNIQUE:UNIQUE.append(r['name'])

def bce(y,p):
 p=np.clip(np.asarray(p,float),1e-6,1-1e-6);y=np.asarray(y,float);return float(np.mean(-(y*np.log(p)+(1-y)*np.log(1-p))))

def probe_gain(model,X,y,seed):
 rng=np.random.default_rng(seed);ix=rng.permutation(len(X));a=int(.4*len(ix));b=int(.8*len(ix));tr=ix[:a];va=ix[a:b]
 before=v2.met(y[va],model.proba(X[va]));cand=model.clone();cand.train(X[tr],y[tr],seed+17,steps=6,lr=.02,batch=512);after=v2.met(y[va],cand.proba(X[va]));return float(max(after['ba']-before['ba'],before['ll']-after['ll']))

def episode_record(cur,models,t):
 X,yr,ya=cur;desc=np.mean(X,axis=0).astype(np.float32);hard=.5*(bce(yr,models['repair'].proba(X))+bce(ya,models['add'].proba(X)))
 return {'data':cur,'desc':desc,'hard':float(hard),'t':int(t)}

def diverse_select(records,k):
 if len(records)<=k:return list(records)
 D=np.stack([r['desc'] for r in records]);mu=D.mean(0);first=int(np.argmax(np.linalg.norm(D-mu,axis=1)));sel=[first]
 while len(sel)<k:
  dist=np.min(np.stack([np.linalg.norm(D-D[j],axis=1) for j in sel]),axis=0);dist[sel]=-1;sel.append(int(np.argmax(dist)))
 return [records[i] for i in sel]

def select_memory(policy,records,rng):
 if not records:return []
 if policy=='FIFO3':return records[-3:]
 if policy=='RESERVOIR6':
  # deterministic uniform sample of all completed episodes at this point.
  k=min(6,len(records));ix=rng.choice(len(records),k,replace=False);return [records[int(i)] for i in ix]
 if policy=='HARD6':return sorted(records,key=lambda r:(r['hard'],r['t']),reverse=True)[:6]
 if policy=='DIVERSE6':return diverse_select(records,6)
 if policy=='HYBRID_RECENT_HARD_DIVERSE6':
  cand=[]
  for r in records[-2:]+sorted(records,key=lambda r:(r['hard'],r['t']),reverse=True)[:2]+diverse_select(records,2):
   if all(x['t']!=r['t'] for x in cand):cand.append(r)
  if len(cand)<6:
   for r in reversed(records):
    if all(x['t']!=r['t'] for x in cand):cand.append(r)
    if len(cand)>=6:break
  return cand[:6]
 raise KeyError(policy)

def replay_data(mem,seed):
 if not mem:return None
 X=np.concatenate([r['data'][0] for r in mem]);yr=np.concatenate([r['data'][1] for r in mem]);ya=np.concatenate([r['data'][2] for r in mem]);
 if len(X)>MAX_REPLAY:
  rng=np.random.default_rng(seed);ix=rng.choice(len(X),MAX_REPLAY,replace=False);X=X[ix];yr=yr[ix];ya=ya[ix]
 return X,yr,ya

def join(cur,rep):
 if rep is None:return cur
 return np.concatenate([cur[0],rep[0]]),np.concatenate([cur[1],rep[1]]),np.concatenate([cur[2],rep[2]])

def mem_div(mem):
 if len(mem)<2:return 0.0
 D=np.stack([r['desc'] for r in mem]);vals=[]
 for i in range(len(D)):
  for j in range(i+1,len(D)):vals.append(float(np.linalg.norm(D[i]-D[j])))
 return float(np.mean(vals)) if vals else 0.0

def run_one(seed,family,policy):
 evalsets={nm:v2.world(EVAL_N,nm,family,seed+900000+i*139) for i,nm in enumerate(UNIQUE)};warm=v2.world(WARM_N,'BASE',family,seed+31)
 models={'repair':v1.Logit(v1.FEATURES,seed+1),'add':v1.Logit(v1.FEATURES,seed+2)};v1.train_dual(models,*warm,seed+40);base=v2.evalpair(models,*evalsets['BASE']);records=[];rng=np.random.default_rng(seed+777);prop=acc=0;pre_return=[];divs=[]
 seen_reg={}
 for ridx,reg in enumerate(ROUNDS,1):
  cur=v2.world(TRAIN_N,reg['name'],family,seed+1000+ridx*37);ev=evalsets[reg['name']];pre=v2.evalpair(models,*ev);pairba=.5*(pre['repair']['ba']+pre['add']['ba'])
  if reg['name'] in seen_reg:pre_return.append(pairba)
  seen_reg[reg['name']]=1
  mem=select_memory(policy,records,rng);divs.append(mem_div(mem));rep=replay_data(mem,seed+ridx);X,yr,ya=join(cur,rep);Xc,yrc,yac=cur
  anchor_names=['BASE']+[x['name'] for x in ROUNDS[max(0,ridx-5):ridx-1]];anchor_names=list(dict.fromkeys(anchor_names));olda=v2.anchors(models,evalsets,anchor_names);oldc=pre
  for cap,ycur,yfull in [('repair',yrc,yr),('add',yac,ya)]:
   g=probe_gain(models[cap],Xc,ycur,seed+3000+ridx+(0 if cap=='repair' else 100))
   if g<.002:continue
   prop+=1;cand=models[cap].clone();cand.train(X,yfull,seed+5000+ridx+(0 if cap=='repair' else 100),steps=24);trial=models.copy();trial[cap]=cand;newc=v2.evalpair(trial,*ev);newa=v2.anchors(trial,evalsets,anchor_names)
   if v2.accept(oldc,newc,olda,newa,cap):models[cap]=cand;acc+=1;oldc=newc;olda=newa
  records.append(episode_record(cur,models,ridx))
 per={nm:v2.evalpair(models,*evalsets[nm]) for nm in UNIQUE};bas=[z['repair']['ba'] for z in per.values()]+[z['add']['ba'] for z in per.values()];rec=[q for z in per.values() for q in (z['repair']['r0'],z['repair']['r1'],z['add']['r0'],z['add']['r1'])]
 return {'seed':seed,'family':family,'policy':policy,'finalMeanBA':float(np.mean(bas)),'finalMinBA':float(min(bas)),'worstRecall':float(min(rec)),'baseRepairForgetting':float(per['BASE']['repair']['ba']-base['repair']['ba']),'baseAddForgetting':float(per['BASE']['add']['ba']-base['add']['ba']),'meanRecurrencePreUpdateBA':float(np.mean(pre_return)),'proposals':prop,'accepted':acc,'meanMemoryDiversity':float(np.mean(divs))}

def main():
 t=time.perf_counter();runs=[]
 for fam in FAMILIES:
  for seed in SEEDS:
   for pol in POLICIES:runs.append(run_one(seed,fam,pol))
 summary={}
 for pol in POLICIES:
  rr=[x for x in runs if x['policy']==pol];summary[pol]={k:float(np.mean([x[k] for x in rr])) for k in ['finalMeanBA','finalMinBA','worstRecall','baseRepairForgetting','baseAddForgetting','meanRecurrencePreUpdateBA','proposals','accepted','meanMemoryDiversity']}
 ref=summary['FIFO3']
 for pol,z in summary.items():
  z['finalMeanBADeltaVsFIFO3']=float(z['finalMeanBA']-ref['finalMeanBA']);z['recurrencePreUpdateBADeltaVsFIFO3']=float(z['meanRecurrencePreUpdateBA']-ref['meanRecurrencePreUpdateBA'])
 rep={'version':'R4_ADAPTIVE_CYCLE_MICROWORLD_REPLAY_MEMORY_V8_RESULT','researchOnly':True,'promotionEvidence':False,'actionAuthority':False,'worldFamilies':FAMILIES,'seeds':SEEDS,'roundsPerRun':len(ROUNDS),'summary':summary,'elapsedSec':time.perf_counter()-t,'guard':'Synthetic replay-memory mechanics only; memory selection never sees hidden regime names/targets. Any candidate must return unchanged to realistic-HFT/fresh chronology.','contract':'r4_adaptive_cycle_microworld_replay_memory_v8_contract.json'}
 OUT.mkdir(parents=True,exist_ok=True);(OUT/'synthesis.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
