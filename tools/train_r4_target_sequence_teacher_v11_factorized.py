from __future__ import annotations
import json, copy, random
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score, recall_score

ROOT=Path(__file__).resolve().parent
# When run in the worker bundle, V1 module and data live beside this file.
import train_r4_target_sequence_teacher_v1 as v1
CON=json.loads((ROOT/'r4_target_sequence_teacher_v11_factorized_contract.json').read_text(encoding='utf-8'))
V1REP=json.loads((ROOT/'r4_target_sequence_teacher_v1_report.json').read_text(encoding='utf-8'))
OUT=ROOT/'r4_target_sequence_teacher_v11_factorized_report.json'
CKPT=ROOT/'r4_target_sequence_teacher_v11_factorized.pt'
SEED=20260829


def seed_all(s=SEED):
 random.seed(s);np.random.seed(s);torch.manual_seed(s)
 if torch.cuda.is_available():torch.cuda.manual_seed_all(s)
 torch.backends.cudnn.deterministic=True;torch.backends.cudnn.benchmark=False

class SeqBinary(nn.Module):
 def __init__(self,fd):
  super().__init__();self.inp=nn.Sequential(nn.Linear(fd,96),nn.LayerNorm(96),nn.GELU());self.pos=nn.Parameter(torch.zeros(1,v1.SEQ,96));enc=nn.TransformerEncoderLayer(96,4,256,.1,batch_first=True,norm_first=True,activation='gelu');self.enc=nn.TransformerEncoder(enc,3);self.norm=nn.LayerNorm(96);self.out=nn.Linear(96,2)
 def forward(self,x,mask):
  h=self.inp(x)+self.pos;h=self.enc(h,src_key_padding_mask=mask);return self.out(self.norm(h[:,-1]))

class QueryBinary(nn.Module):
 def __init__(self,fd):
  super().__init__();self.net=nn.Sequential(nn.Linear(fd,128),nn.GELU(),nn.Dropout(.1),nn.Linear(128,64),nn.GELU(),nn.Linear(64,2))
 def forward(self,x,mask):return self.net(x[:,-1])

def make_ds(X,M,y,ph,mids,mask,split_map,split_id):
 ix=np.where(mask & np.array([split_map[int(m)]==split_id for m in mids]))[0]
 return TensorDataset(torch.from_numpy(X[ix]),torch.from_numpy(M[ix]),torch.from_numpy(y[ix]),torch.from_numpy(ph[ix]),torch.from_numpy(mids[ix]))

def train(model,tr,va,device,seed):
 seed_all(seed);yy=np.concatenate([b[2].numpy() for b in DataLoader(tr,batch_size=4096,shuffle=False)]);cnt=np.bincount(yy,minlength=2).astype(np.float32);w=cnt.sum()/np.maximum(cnt,1);w/=w.mean();lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device));opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4);model=model.to(device);dl=DataLoader(tr,batch_size=256,shuffle=True,num_workers=0,pin_memory=device.type=='cuda');best=None;bestloss=1e99;bad=0;hist=[]
 for ep in range(1,61):
  model.train();tot=0.;n=0
  for xb,mb,yb,*_ in dl:
   xb=xb.to(device,non_blocking=True);mb=mb.to(device,non_blocking=True);yb=yb.to(device);opt.zero_grad(set_to_none=True);z=model(xb,mb);loss=lossfn(z,yb);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.0);opt.step();tot+=float(loss.detach())*len(xb);n+=len(xb)
  model.eval();vl=0.;vn=0
  with torch.no_grad():
   for xb,mb,yb,*_ in DataLoader(va,batch_size=512,shuffle=False):
    xb=xb.to(device);mb=mb.to(device);yb=yb.to(device);loss=lossfn(model(xb,mb),yb);vl+=float(loss)*len(xb);vn+=len(xb)
  vl/=max(vn,1);hist.append({'epoch':ep,'trainLoss':tot/max(n,1),'valLoss':vl})
  if vl<bestloss-1e-5:bestloss=vl;best=copy.deepcopy({k:v.detach().cpu() for k,v in model.state_dict().items()});bad=0
  else:bad+=1
  if bad>=8:break
 model.load_state_dict(best);return model,hist

@torch.no_grad()
def evaluate(model,ds,device):
 model.eval();P=[];Y=[];PH=[]
 for xb,mb,yb,pb,*_ in DataLoader(ds,batch_size=512,shuffle=False):
  pr=torch.softmax(model(xb.to(device),mb.to(device)),1)[:,1].cpu().numpy();P.append(pr);Y.append(yb.numpy());PH.append(pb.numpy())
 p=np.concatenate(P);y=np.concatenate(Y);ph=np.concatenate(PH);z=(p>=.5).astype(int)
 def met(ix):
  yy=y[ix];pp=p[ix];zz=z[ix]
  return {'n':int(len(ix)),'positiveSupport':int(yy.sum()),'negativeSupport':int(len(yy)-yy.sum()),'balancedAccuracy':float(balanced_accuracy_score(yy,zz)),'auc':float(roc_auc_score(yy,pp)) if len(np.unique(yy))>1 else None,'ap':float(average_precision_score(yy,pp)) if len(np.unique(yy))>1 else None,'positiveRecall':float(recall_score(yy,zz,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(yy,zz,pos_label=0,zero_division=0))}
 out=met(np.arange(len(y)));names={0:'FORMATION_180_300',1:'MANAGEMENT_60_180',2:'PROTECTION_0_60'};out['phases']={}
 for k,nm in names.items():
  ix=np.where(ph==k)[0]
  if len(ix):out['phases'][nm]=met(ix)
 return out

def run_task(name,X,M,y,ph,mids,mask,split_map,device,seed):
 tr=make_ds(X,M,y,ph,mids,mask,split_map,0);va=make_ds(X,M,y,ph,mids,mask,split_map,1);te=make_ds(X,M,y,ph,mids,mask,split_map,2)
 seq,sh=train(SeqBinary(X.shape[-1]),tr,va,device,seed);mlp,mh=train(QueryBinary(X.shape[-1]),tr,va,device,seed+100);sm=evaluate(seq,te,device);bm=evaluate(mlp,te,device)
 return {'support':{'train':len(tr),'validation':len(va),'test':len(te)},'sequence':sm,'queryMLP':bm,'deltas':{'balancedAccuracy':sm['balancedAccuracy']-bm['balancedAccuracy'],'auc':sm['auc']-bm['auc'],'ap':sm['ap']-bm['ap'],'positiveRecall':sm['positiveRecall']-bm['positiveRecall'],'negativeRecall':sm['negativeRecall']-bm['negativeRecall']},'training':{'sequence':sh,'queryMLP':mh},'_seqState':{k:v.cpu() for k,v in seq.state_dict().items()}}

def main():
 assert CON['status']=='FROZEN_BEFORE_FIRST_TRAINING_RUN';seed_all();d=v1.build_rows();X,M,ya,yc,yf,ph,mids,times,support=v1.build_sequences(d);actions=np.array([v1.ACTIONS[i] for i in ya]);purpose=np.array([1 if a.endswith('_ADD') else 0 for a in actions],np.int64);role=np.array([1 if a.startswith('TAKER_') else 0 for a in actions],np.int64);nonflat=np.array([not a.endswith('_FLAT') for a in actions]);taker_nonflat=np.array([a in ('TAKER_REPAIR','TAKER_ADD') for a in actions])
 tr=set(map(int,V1REP['dataset']['trainMarketIds']));va=set(map(int,V1REP['dataset']['validationMarketIds']));te=set(map(int,V1REP['dataset']['testMarketIds']));split_map={m:0 for m in tr}|{m:1 for m in va}|{m:2 for m in te};assert all(int(m) in split_map for m in np.unique(mids));device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
 tasks={}
 tasks['purposeBinary']=run_task('purpose',X,M,purpose,ph,mids,nonflat,split_map,device,51001)
 tasks['roleBinary']=run_task('role',X,M,role,ph,mids,nonflat,split_map,device,52001)
 tasks['takerPurposeBinary']=run_task('takerPurpose',X,M,purpose,ph,mids,taker_nonflat,split_map,device,53001)
 # Remove raw states from JSON and save separately.
 states={k:v.pop('_seqState') for k,v in tasks.items()}
 tp=tasks['takerPurposeBinary'];keep_tp=(tp['sequence']['balancedAccuracy']>tp['queryMLP']['balancedAccuracy'] or tp['sequence']['auc']>tp['queryMLP']['auc']) and tp['sequence']['positiveRecall']>=.45 and tp['sequence']['negativeRecall']>=.45
 pp=tasks['purposeBinary'];keep_p=pp['sequence']['balancedAccuracy']>pp['queryMLP']['balancedAccuracy'] and pp['sequence']['auc']>pp['queryMLP']['auc']
 rp=tasks['roleBinary'];keep_r=rp['sequence']['balancedAccuracy']>rp['queryMLP']['balancedAccuracy'] and rp['sequence']['auc']>rp['queryMLP']['auc']
 rep={'version':'R4_TARGET_SEQUENCE_TEACHER_V1_1_FACTORIZED_REPORT','researchOnly':True,'actionAuthority':False,'device':str(device),'cudaDevice':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'sameV1Split':True,'datasetExamples':int(len(X)),'tasks':tasks,'keep':{'purposeBinary':bool(keep_p),'roleBinary':bool(keep_r),'takerPurposeBinary':bool(keep_tp)},'commitmentPolicy':CON['commitmentPolicy'],'guards':CON['guards']};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');torch.save({'version':'R4_TARGET_SEQUENCE_TEACHER_V1_1_FACTORIZED','researchOnly':True,'actionAuthority':False,'features':v1.FEATURES,'sequenceLength':v1.SEQ,'states':states,'keep':rep['keep'],'guards':CON['guards']},CKPT);print(json.dumps({'device':str(device),'cudaDevice':rep['cudaDevice'],'keep':rep['keep'],'tasks':{k:{'support':v['support'],'sequence':v['sequence'],'queryMLP':v['queryMLP'],'deltas':v['deltas']} for k,v in tasks.items()}},indent=2),flush=True)
if __name__=='__main__':main()
