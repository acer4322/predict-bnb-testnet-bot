from pathlib import Path
import argparse,json,sys,copy,hashlib
import numpy as np, torch
from torch.utils.data import DataLoader,TensorDataset
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score,recall_score

def hstate(m):
 h=hashlib.sha256()
 for k,v in sorted(m.state_dict().items()):h.update(k.encode());h.update(v.detach().cpu().numpy().tobytes())
 return h.hexdigest()

def metrics(model,X,M,y,ix,dev):
 model.eval();ps=[];ys=[]
 with torch.no_grad():
  for st in range(0,len(ix),512):
   q=ix[st:st+512];z=model(X[q].to(dev),M[q].to(dev));ps.append(torch.softmax(z,1)[:,1].cpu().numpy());ys.append(y[q].cpu().numpy())
 p=np.concatenate(ps); yy=np.concatenate(ys).astype(int); pred=(p>=.5).astype(int)
 return {'n':int(len(yy)),'positiveSupport':int(yy.sum()),'positiveRate':float(yy.mean()),'auc':float(roc_auc_score(yy,p)) if len(np.unique(yy))>1 else None,'ap':float(average_precision_score(yy,p)) if len(np.unique(yy))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pred)) if len(np.unique(yy))>1 else None,'positiveRecall':float(recall_score(yy,pred,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(yy,pred,pos_label=0,zero_division=0))}

def train(model,X,M,y,tr,va,dev,lr=3e-5,max_epochs=12):
 yy=y[tr].cpu().numpy().astype(int);cnt=np.bincount(yy,minlength=2).astype(np.float32);w=cnt.sum()/np.maximum(cnt,1);w/=w.mean();lossfn=torch.nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=dev));opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=1e-4);best=None;bl=1e99;bad=0;hist=[]
 for ep in range(1,max_epochs+1):
  model.train();order=np.random.permutation(tr);tot=n=0
  for st in range(0,len(order),256):
   q=order[st:st+256];xb=X[q].to(dev);mb=M[q].to(dev);yb=y[q].to(dev);opt.zero_grad(set_to_none=True);loss=lossfn(model(xb,mb),yb);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);opt.step();tot+=float(loss.detach())*len(q);n+=len(q)
  model.eval();vl=vn=0
  with torch.no_grad():
   for st in range(0,len(va),512):
    q=va[st:st+512];loss=lossfn(model(X[q].to(dev),M[q].to(dev)),y[q].to(dev));vl+=float(loss)*len(q);vn+=len(q)
  vl/=max(vn,1);hist.append({'epoch':ep,'trainLoss':tot/max(n,1),'guardLoss':vl})
  if vl<bl-1e-5:bl=vl;best=copy.deepcopy({k:v.detach().cpu() for k,v in model.state_dict().items()});bad=0
  else:bad+=1
  if bad>=3:break
 model.load_state_dict(best);return hist

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--fresh-dir',required=True);ap.add_argument('--fresh-cache',required=True);ap.add_argument('--start-checkpoint',required=True);ap.add_argument('--out',required=True);ap.add_argument('--checkpoint-out',required=True);args=ap.parse_args()
 b=Path(args.bundle_dir).resolve();sys.path.insert(0,str(b));import train_r4_target_sequence_teacher_v1 as v1;import train_r4_target_sequence_teacher_v11_factorized as v11
 np.random.seed(20260830);torch.manual_seed(20260830)
 c=torch.load(args.fresh_cache,map_location='cpu',weights_only=False);X=c['HX'].float();M=c['HM'].bool();mids=c['HMIDS'].numpy().astype(int);qt=c['HTIMES'].numpy().astype(np.int64);at=c['times'].numpy().astype(np.int64);am=c['mids'].numpy().astype(int);purpose=c['purpose'].numpy().astype(int)
 # 5s independent future labels from non-flat action rows. purpose 0=REPAIR,1=ADD.
 yr=np.zeros(len(qt),np.int64);ya=np.zeros(len(qt),np.int64)
 for mid in np.unique(mids):
  qi=np.where(mids==mid)[0]; ai=np.where(am==mid)[0]; tt=at[ai];pp=purpose[ai];ordr=np.argsort(tt);tt=tt[ordr];pp=pp[ordr]
  for q in qi:
   lo=np.searchsorted(tt,qt[q],'right');hi=np.searchsorted(tt,qt[q]+5000,'right');z=pp[lo:hi]
   if len(z):yr[q]=int(np.any(z==0));ya[q]=int(np.any(z==1))
 yr=torch.from_numpy(yr);ya=torch.from_numpy(ya)
 split=json.loads((Path(args.fresh_dir)/'split.json').read_text(encoding='utf-8'));adapt=list(map(int,split.get('adapt60',split.get('adapt80',[]))));guard=list(map(int,split['guard20']));final=list(map(int,split['final20']));
 def ix(ids):s=set(ids);return np.where(np.array([int(m) in s for m in mids],bool))[0].astype(np.int64)
 tr,va,te=ix(adapt),ix(guard),ix(final);dev=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
 ck=torch.load(args.start_checkpoint,map_location='cpu',weights_only=False);base=ck['states']['purposeBinary'];repair=v11.SeqBinary(X.shape[-1]);add=v11.SeqBinary(X.shape[-1]);repair.load_state_dict(base);add.load_state_dict(base)
 # Align repair positive class=1 with original REPAIR logit=class0.
 rs=repair.state_dict();rs['out.weight']=rs['out.weight'][[1,0]].clone();rs['out.bias']=rs['out.bias'][[1,0]].clone();repair.load_state_dict(rs);repair=repair.to(dev);add=add.to(dev)
 bmr=metrics(repair,X,M,yr,te,dev);bma=metrics(add,X,M,ya,te,dev)
 add_hash_before=hstate(add);rh=train(repair,X,M,yr,tr,va,dev);add_hash_after_repair=hstate(add);repair_hash_before_add=hstate(repair);ah=train(add,X,M,ya,tr,va,dev);repair_hash_after_add=hstate(repair)
 fmr=metrics(repair,X,M,yr,te,dev);fma=metrics(add,X,M,ya,te,dev)
 # Joint label support on final.
 rv=yr[te].numpy();av=ya[te].numpy();joint={'NEITHER':int(np.sum((rv==0)&(av==0))),'REPAIR_ONLY':int(np.sum((rv==1)&(av==0))),'ADD_ONLY':int(np.sum((rv==0)&(av==1))),'BOTH':int(np.sum((rv==1)&(av==1)))}
 def delta(a,b):return {k:(a[k]-b[k] if a.get(k) is not None and b.get(k) is not None and isinstance(a.get(k),(int,float)) else None) for k in ['auc','ap','balancedAccuracy','positiveRecall','negativeRecall']}
 rep={'version':'R4_DUAL_PURPOSE_SPECIALISTS_V1_RESULT','researchOnly':True,'actionAuthority':False,'device':str(dev),'cudaDevice':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'dataset':{'queries':int(len(X)),'train':int(len(tr)),'guard':int(len(va)),'final':int(len(te)),'adaptMarkets':len(adapt),'guardMarkets':len(guard),'finalMarkets':len(final),'labelWindowSeconds':5,'finalJointLabelSupport':joint},'repairDemand5s':{'baselineFinal':bmr,'finalFinal':fmr,'delta':delta(fmr,bmr),'training':rh},'stateShapingOpportunity5s':{'baselineFinal':bma,'finalFinal':fma,'delta':delta(fma,bma),'training':ah},'parameterIsolation':{'addUnchangedDuringRepairTraining':add_hash_before==add_hash_after_repair,'repairUnchangedDuringAddTraining':repair_hash_before_add==repair_hash_after_add,'addHashBeforeRepair':add_hash_before,'addHashAfterRepair':add_hash_after_repair,'repairHashBeforeAdd':repair_hash_before_add,'repairHashAfterAdd':repair_hash_after_add},'bothCapabilitiesCanImproveSimultaneously':bool(fmr['auc']>=bmr['auc'] and fma['auc']>=bma['auc'] and fmr['balancedAccuracy']>=bmr['balancedAccuracy'] and fma['balancedAccuracy']>=bma['balancedAccuracy']),'interpretationGuard':'This is architecture/representation evidence only. Target future actions are auxiliary labels; outputs have no action authority and are not the final system objective.'}
 Path(args.out).parent.mkdir(parents=True,exist_ok=True);Path(args.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');torch.save({'version':'R4_DUAL_PURPOSE_SPECIALISTS_V1','researchOnly':True,'actionAuthority':False,'repairState':{k:v.detach().cpu() for k,v in repair.state_dict().items()},'addState':{k:v.detach().cpu() for k,v in add.state_dict().items()}},args.checkpoint_out);print(json.dumps(rep,indent=2),flush=True)
if __name__=='__main__':main()
