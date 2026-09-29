from pathlib import Path
import argparse,json,sys,copy,hashlib,random
import numpy as np, torch
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score

SEED=20260830

def hstate(m):
 h=hashlib.sha256()
 for k,v in sorted(m.state_dict().items()): h.update(k.encode()); h.update(v.detach().cpu().numpy().tobytes())
 return h.hexdigest()

def probs(model,X,M,ix,dev):
 model.eval(); out=[]
 with torch.no_grad():
  for st in range(0,len(ix),512):
   q=ix[st:st+512]; out.append(torch.softmax(model(X[q].to(dev),M[q].to(dev)),1)[:,1].cpu().numpy())
 return np.concatenate(out) if len(out) else np.array([],dtype=np.float32)

def met_from(p,y):
 y=np.asarray(y,dtype=int); z=(p>=.5).astype(int)
 if len(y)==0: return {'n':0,'auc':None,'ba':None,'r0':None,'r1':None}
 return {'n':int(len(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}

def metrics(model,X,M,y,ix,dev): return met_from(probs(model,X,M,ix,dev),y[ix].numpy())

def balanced_pick(rng,ix,y,n):
 ix=np.asarray(ix,dtype=np.int64)
 if len(ix)==0:return ix
 yy=y[ix].numpy().astype(int); a=ix[yy==0]; b=ix[yy==1]; half=n//2
 def take(v,k):
  if len(v)==0:return np.array([],dtype=np.int64)
  return rng.choice(v,size=min(k,len(v)),replace=False)
 q=np.concatenate([take(a,half),take(b,n-half)])
 if len(q)<min(n,len(ix)):
  used=set(map(int,q)); rem=np.array([x for x in ix if int(x) not in used],dtype=np.int64)
  if len(rem): q=np.concatenate([q,take(rem,min(n-len(q),len(rem)))])
 rng.shuffle(q); return q

def candidate_update(model,X,M,y,current_ix,replay_ix,dev,rng,lr=3e-5,steps=2):
 cand=copy.deepcopy(model).to(dev); cand.train(); opt=torch.optim.AdamW(cand.parameters(),lr=lr,weight_decay=1e-4); lossfn=torch.nn.CrossEntropyLoss()
 cur=balanced_pick(rng,current_ix,y,256); rep=balanced_pick(rng,replay_ix,y,256) if len(replay_ix) else np.array([],dtype=np.int64); train_ix=np.concatenate([cur,rep]) if len(rep) else cur
 if len(train_ix)==0:return cand
 for _ in range(steps):
  q=train_ix.copy(); rng.shuffle(q)
  for st in range(0,len(q),256):
   z=q[st:st+256]; opt.zero_grad(set_to_none=True); loss=lossfn(cand(X[z].to(dev),M[z].to(dev)),y[z].to(dev)); loss.backward(); torch.nn.utils.clip_grad_norm_(cand.parameters(),1.); opt.step()
 return cand

def accept(old,new):
 if None in (old['auc'],old['ba'],new['auc'],new['ba']): return False
 no_down=(new['auc']>=old['auc']-1e-8 and new['ba']>=old['ba']-1e-8 and new['r0']>=old['r0']-0.02 and new['r1']>=old['r1']-0.02)
 material=(new['auc']>old['auc']+1e-5 or new['ba']>old['ba']+1e-5)
 return bool(no_down and material)

def behavior_delta(before,after,y,phase):
 zb=(before>=.5).astype(int); za=(after>=.5).astype(int); y=np.asarray(y,dtype=int)
 out={'wrongToRight':int(np.sum((zb!=y)&(za==y))),'rightToWrong':int(np.sum((zb==y)&(za!=y))),'meanTargetProbDelta':float(np.mean(np.where(y==1,after-before,before-after)))}
 names={0:'FORMATION_180_300',1:'MANAGEMENT_60_180',2:'PROTECTION_0_60'}; out['phaseClass']=[]
 for ph,nm in names.items():
  for lab in (0,1):
   ix=np.where((phase==ph)&(y==lab))[0]
   if len(ix):
    d=(after[ix]-before[ix]) if lab==1 else (before[ix]-after[ix]); bb=zb[ix];aa=za[ix];yy=y[ix]
    out['phaseClass'].append({'phase':nm,'label':int(lab),'n':int(len(ix)),'meanTargetProbDelta':float(np.mean(d)),'wrongToRight':int(np.sum((bb!=yy)&(aa==yy))),'rightToWrong':int(np.sum((bb==yy)&(aa!=yy)))})
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--bundle-dir',required=True);ap.add_argument('--fresh-dir',required=True);ap.add_argument('--fresh-cache',required=True);ap.add_argument('--start-checkpoint',required=True);ap.add_argument('--out',required=True);ap.add_argument('--checkpoint-out',required=True);args=ap.parse_args()
 random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED);rng=np.random.default_rng(SEED)
 b=Path(args.bundle_dir).resolve();sys.path.insert(0,str(b));import train_r4_target_sequence_teacher_v11_factorized as v11
 c=torch.load(args.fresh_cache,map_location='cpu',weights_only=False);X=c['HX'].float();M=c['HM'].bool();mids=c['HMIDS'].numpy().astype(int);qt=c['HTIMES'].numpy().astype(np.int64);phase=c['HPH'].numpy().astype(int);at=c['times'].numpy().astype(np.int64);am=c['mids'].numpy().astype(int);purpose=c['purpose'].numpy().astype(int)
 yr=np.zeros(len(qt),np.int64);ya=np.zeros(len(qt),np.int64)
 for mid in np.unique(mids):
  qi=np.where(mids==mid)[0];ai=np.where(am==mid)[0];tt=at[ai];pp=purpose[ai];o=np.argsort(tt);tt=tt[o];pp=pp[o]
  for q in qi:
   lo=np.searchsorted(tt,qt[q],'right');hi=np.searchsorted(tt,qt[q]+5000,'right');z=pp[lo:hi]
   if len(z):yr[q]=int(np.any(z==0));ya[q]=int(np.any(z==1))
 yr=torch.from_numpy(yr);ya=torch.from_numpy(ya)
 split=json.loads((Path(args.fresh_dir)/'split.json').read_text(encoding='utf-8'));adapt=list(map(int,split.get('adapt60',split.get('adapt80',[]))));guard=list(map(int,split['guard20']));final=list(map(int,split['final20']));
 def ix(ids):s=set(ids);return np.where(np.array([int(m) in s for m in mids],bool))[0].astype(np.int64)
 gix=ix(guard);fix=ix(final);dev=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
 ck=torch.load(args.start_checkpoint,map_location='cpu',weights_only=False);base=ck['states']['purposeBinary'];repair=v11.SeqBinary(X.shape[-1]);add=v11.SeqBinary(X.shape[-1]);repair.load_state_dict(base);add.load_state_dict(base);rs=repair.state_dict();rs['out.weight']=rs['out.weight'][[1,0]].clone();rs['out.bias']=rs['out.bias'][[1,0]].clone();repair.load_state_dict(rs);repair=repair.to(dev);add=add.to(dev)
 caps={'REPAIR_DEMAND_5S':{'model':repair,'y':yr},'STATE_SHAPING_OPPORTUNITY_5S':{'model':add,'y':ya}}
 baseline_final={k:metrics(v['model'],X,M,v['y'],fix,dev) for k,v in caps.items()}; baseline_guard={k:metrics(v['model'],X,M,v['y'],gix,dev) for k,v in caps.items()}
 led=[];accepted={k:0 for k in caps};rejected={k:0 for k in caps};seen=[]
 for step,mid in enumerate(adapt,1):
  cur=np.where(mids==mid)[0].astype(np.int64); replay=np.where(np.isin(mids,np.array(seen,dtype=int)))[0].astype(np.int64) if seen else np.array([],dtype=np.int64)
  for name,v in caps.items():
   model=v['model']; y=v['y']; other='STATE_SHAPING_OPPORTUNITY_5S' if name=='REPAIR_DEMAND_5S' else 'REPAIR_DEMAND_5S'; other_hash_before=hstate(caps[other]['model']); old=metrics(model,X,M,y,gix,dev); bp=probs(model,X,M,gix,dev); cand=candidate_update(model,X,M,y,cur,replay,dev,rng); new=metrics(cand,X,M,y,gix,dev); ok=accept(old,new)
   if ok:
    apb=probs(cand,X,M,gix,dev); delta=behavior_delta(bp,apb,y[gix].numpy(),phase[gix]);v['model']=cand;caps[name]['model']=cand;accepted[name]+=1; led.append({'step':step,'marketId':int(mid),'capability':name,'decision':'ACCEPT_SHADOW','guardBefore':old,'guardAfter':new,'behavior':delta,'otherCapabilityHashUnchanged':other_hash_before==hstate(caps[other]['model'])})
   else:
    rejected[name]+=1;led.append({'step':step,'marketId':int(mid),'capability':name,'decision':'ROLLBACK','guardBefore':old,'guardAfterCandidate':new,'otherCapabilityHashUnchanged':other_hash_before==hstate(caps[other]['model'])})
  seen.append(int(mid));print(json.dumps({'step':step,'marketId':int(mid),'accepted':{k:accepted[k] for k in accepted}}),flush=True)
 final={k:metrics(v['model'],X,M,v['y'],fix,dev) for k,v in caps.items()}; guard_final={k:metrics(v['model'],X,M,v['y'],gix,dev) for k,v in caps.items()}
 rep={'version':'R4_ADAPTIVE_CYCLE_DUAL_SPECIALIST_LOOP_V1_RESULT','researchOnly':True,'actionAuthority':False,'developmentOnly':True,'device':str(dev),'dataset':{'adaptMarkets':len(adapt),'guardMarkets':len(guard),'finalMarkets':int(len(set(map(int,mids[fix])))),'note':'fresh100 v2 already consumed; this validates loop mechanics, not formal forward generalization'},'acceptedUpdates':accepted,'rejectedUpdates':rejected,'baselineGuard':baseline_guard,'finalGuard':guard_final,'baselineFinal':baseline_final,'finalFinal':final,'ledgerAccepted':[x for x in led if x['decision']=='ACCEPT_SHADOW'],'allIsolationChecksPass':bool(all(x['otherCapabilityHashUnchanged'] for x in led)),'contract':'r4_adaptive_cycle_dual_specialist_loop_v1_contract.json'}
 Path(args.out).parent.mkdir(parents=True,exist_ok=True);Path(args.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');torch.save({'version':'R4_ADAPTIVE_CYCLE_DUAL_SPECIALIST_LOOP_V1','researchOnly':True,'actionAuthority':False,'repairState':{k:v.detach().cpu() for k,v in caps['REPAIR_DEMAND_5S']['model'].state_dict().items()},'addState':{k:v.detach().cpu() for k,v in caps['STATE_SHAPING_OPPORTUNITY_5S']['model'].state_dict().items()}},args.checkpoint_out);print(json.dumps({'acceptedUpdates':accepted,'rejectedUpdates':rejected,'baselineFinal':baseline_final,'finalFinal':final,'allIsolationChecksPass':rep['allIsolationChecksPass']},indent=2),flush=True)
if __name__=='__main__':main()
