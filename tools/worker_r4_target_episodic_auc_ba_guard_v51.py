from pathlib import Path
import argparse,json,sys
import numpy as np,torch
from sklearn.metrics import roc_auc_score,recall_score,balanced_accuracy_score

SEED=20260829

def binary_metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int)
 return {'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,
         'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,
         'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),
         'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}

def hazard_metrics(Y,P):
 names=['any','taker','add'];out={}
 for j,n in enumerate(names):out[n]=binary_metrics(Y[:,j],P[:,j])
 return out

@torch.no_grad()
def pred_binary(model,X,M,idx,dev,b=2048):
 model.eval();o=[]
 for s in range(0,len(idx),b):
  ii=idx[s:s+b];o.append(torch.softmax(model(torch.from_numpy(X[ii]).to(dev),torch.from_numpy(M[ii]).to(dev)),1)[:,1].cpu().numpy())
 return np.concatenate(o) if o else np.zeros(0,np.float32)
@torch.no_grad()
def pred_hazard(model,X,M,idx,dev,b=2048):
 model.eval();o=[]
 for s in range(0,len(idx),b):
  ii=idx[s:s+b];o.append(torch.sigmoid(model(torch.from_numpy(X[ii]).to(dev),torch.from_numpy(M[ii]).to(dev))).cpu().numpy())
 return np.concatenate(o) if o else np.zeros((0,3),np.float32)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--fresh-dir',required=True);ap.add_argument('--fresh-cache',required=True);ap.add_argument('--anchor-cache',required=True);ap.add_argument('--start-checkpoint',required=True);ap.add_argument('--out',required=True);ap.add_argument('--checkpoint-out',required=True);args=ap.parse_args()
 b=Path(args.bundle_dir).resolve();f=Path(args.fresh_dir).resolve();sys.path.insert(0,str(b));sys.path.insert(0,str(Path.cwd()))
 import train_r4_target_sequence_teacher_v1 as v1
 import train_r4_target_sequence_teacher_v11_factorized as v11
 import train_r4_target_sequence_hazard_v1 as hz
 import r4_target_episodic_teacher_loop_selective_v3 as v3
 v3.seed_all(SEED);dev=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
 fc=torch.load(Path(args.fresh_cache),map_location='cpu',weights_only=False);ac=torch.load(Path(args.anchor_cache),map_location='cpu',weights_only=False)
 def n(x):return x.numpy() if torch.is_tensor(x) else np.asarray(x)
 Xf,Mf,pf,rf,mf=n(fc['X']),n(fc['M']),n(fc['purpose']),n(fc['role']),n(fc['mids']); HXF,HMF,HYF,Hm=n(fc['HX']),n(fc['HM']),n(fc['HY']),n(fc['HMIDS'])
 Xa,Ma,pa,ra,ma=n(ac['X']),n(ac['M']),n(ac['purpose']),n(ac['role']),n(ac['mids']); HXa,HMa,HYa,Hma=n(ac['HX']),n(ac['HM']),n(ac['HY']),n(ac['HMIDS'])
 aoff=len(Xf);hoff=len(HXF);X=np.concatenate([Xf,Xa]);M=np.concatenate([Mf,Ma]);purpose=np.concatenate([pf,pa]);role=np.concatenate([rf,ra]);mids=np.concatenate([mf,ma]);HX=np.concatenate([HXF,HXa]);HM=np.concatenate([HMF,HMa]);HY=np.concatenate([HYF,HYa]);hmids=np.concatenate([Hm,Hma]);anchor_a=np.arange(aoff,len(X),dtype=np.int64);anchor_h=np.arange(hoff,len(HX),dtype=np.int64)
 split=json.loads((f/'split.json').read_text());adapt=list(map(int,split['adapt80']));guard=set(map(int,split['guard20']));final=set(map(int,split['final20']))
 ga=np.where(np.array([i<aoff and int(m) in guard for i,m in enumerate(mids)],bool))[0];fa=np.where(np.array([i<aoff and int(m) in final for i,m in enumerate(mids)],bool))[0];gh=np.where(np.array([i<hoff and int(m) in guard for i,m in enumerate(hmids)],bool))[0];fh=np.where(np.array([i<hoff and int(m) in final for i,m in enumerate(hmids)],bool))[0]
 ck=torch.load(Path(args.start_checkpoint),map_location='cpu',weights_only=False);pm=v11.SeqBinary(X.shape[-1]);pm.load_state_dict(ck['states']['purposeBinary']);rm=v11.SeqBinary(X.shape[-1]);rm.load_state_dict(ck['states']['roleBinary']);hm=hz.HazardSeq(HX.shape[-1]);hm.load_state_dict(ck['states']['hazard']);champ={'purpose':pm.to(dev),'role':rm.to(dev),'hazard':hm.to(dev)}
 losses={'purpose':v3.weighted_ce(purpose,anchor_a,dev),'role':v3.weighted_ce(role,anchor_a,dev),'hazard':v3.weighted_bce(HY,anchor_h,dev)};rng=np.random.default_rng(SEED);anc_a=rng.choice(anchor_a,size=min(1024,len(anchor_a)),replace=False);anc_h=rng.choice(anchor_h,size=min(2048,len(anchor_h)),replace=False)
 def eval_sem(models,ai,hi):
  pp=pred_binary(models['purpose'],X,M,ai,dev);rp=pred_binary(models['role'],X,M,ai,dev);hp=pred_hazard(models['hazard'],HX,HM,hi,dev)
  return {'purpose':binary_metrics(purpose[ai],pp),'role':binary_metrics(role[ai],rp),'hazard':hazard_metrics(HY[hi],hp)}
 base_guard=eval_sem(champ,ga,gh);base_final=eval_sem(champ,fa,fh);prev=base_guard;seen_a=[];seen_h=[];acc={'purpose':0,'role':0,'hazard':0};curve=[]
 for step,mid in enumerate(adapt,1):
  na=np.where(mids[:aoff]==mid)[0].astype(np.int64);nh=np.where(hmids[:hoff]==mid)[0].astype(np.int64);raidx=np.concatenate(seen_a).astype(np.int64) if seen_a else np.zeros(0,np.int64);rhidx=np.concatenate(seen_h).astype(np.int64) if seen_h else np.zeros(0,np.int64)
  before=v3.teacher_loss_parts(champ,(X,M,purpose,role),(HX,HM,HY),na,nh,dev,losses);cand=v3.clone_models(champ,v11,hz,X.shape[-1],dev);v3.update_one_market(cand,(X,M,purpose,role),(HX,HM,HY),na,nh,raidx,rhidx,anc_a,anc_h,dev,losses,rng,steps=4,lr=3e-5);after=v3.teacher_loss_parts(cand,(X,M,purpose,role),(HX,HM,HY),na,nh,dev,losses);g=eval_sem(cand,ga,gh);accept={}
  for k in ['purpose','role']:
   q0,q1=prev[k],g[k];teacher=before[k] is not None and after[k] is not None and after[k]<before[k]-1e-7;accept[k]=bool(teacher and q1['auc']>=q0['auc'] and q1['ba']>=q0['ba'])
  teacher=before['hazard'] is not None and after['hazard'] is not None and after['hazard']<before['hazard']-1e-7;ok=teacher
  for z in ['any','taker','add']:
   for met in ['auc','ba']:ok=ok and g['hazard'][z][met]>=prev['hazard'][z][met]
  accept['hazard']=bool(ok)
  for k in ['purpose','role','hazard']:
   if accept[k]:champ[k].load_state_dict(cand[k].state_dict());acc[k]+=1
  del cand
  if torch.cuda.is_available():torch.cuda.empty_cache()
  prev=eval_sem(champ,ga,gh);seen_a.append(na);seen_h.append(nh);curve.append({'step':step,'marketId':int(mid),'acceptedHeads':accept,'guard':prev});print(json.dumps({'step':step,'marketId':int(mid),'acceptedHeads':accept}),flush=True)
 final_guard=eval_sem(champ,ga,gh);final_final=eval_sem(champ,fa,fh)
 rep={'version':'R4_TARGET_EPISODIC_AUC_BA_GUARD_V5_1_RESULT','researchOnly':True,'actionAuthority':False,'acceptedHeadUpdates':acc,'baselineGuard':base_guard,'finalGuard':final_guard,'baselineFinal':base_final,'finalFinal':final_final,'curve':curve,'guards':['AUC + balanced accuracy nondecreasing per accepted head','fixed 0.5','one-pass','no outcome input']}
 out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(rep,indent=2),encoding='utf-8');cp=Path(args.checkpoint_out);torch.save({'version':'R4_TARGET_EPISODIC_AUC_BA_GUARD_V5_1_CHAMPION','researchOnly':True,'actionAuthority':False,'states':{'purposeBinary':{k:v.cpu() for k,v in champ['purpose'].state_dict().items()},'roleBinary':{k:v.cpu() for k,v in champ['role'].state_dict().items()},'hazard':{k:v.cpu() for k,v in champ['hazard'].state_dict().items()}},'features':v1.FEATURES,'sequenceLength':v1.SEQ},cp)
 print(json.dumps({'acceptedHeadUpdates':acc,'baselineFinal':base_final,'finalFinal':final_final,'out':str(out)},indent=2),flush=True)
if __name__=='__main__':main()