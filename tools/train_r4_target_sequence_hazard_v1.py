from __future__ import annotations
import json,copy,random
from pathlib import Path
from collections import Counter
import numpy as np,pandas as pd
import torch,torch.nn as nn
from torch.utils.data import DataLoader,TensorDataset
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,recall_score
import train_r4_target_sequence_teacher_v1 as v1
ROOT=Path(__file__).resolve().parent;D=ROOT/'data';CON=json.loads((ROOT/'r4_target_sequence_hazard_v1_contract.json').read_text());V1REP=json.loads((ROOT/'r4_target_sequence_teacher_v1_report.json').read_text());OUT=ROOT/'r4_target_sequence_hazard_v1_report.json';CKPT=ROOT/'r4_target_sequence_hazard_v1.pt';SEED=20260829
LABELS=['anyAction1s','takerAction1s','addAction1s']

def seed_all(s=SEED):
 random.seed(s);np.random.seed(s);torch.manual_seed(s)
 if torch.cuda.is_available():torch.cuda.manual_seed_all(s)
 torch.backends.cudnn.deterministic=True;torch.backends.cudnn.benchmark=False

def build_action_tokens(d):
 out={}
 for mid,g in d.groupby('market_id',sort=False):
  g=g.sort_values(['t','role','id']).reset_index(drop=True);hist=[];records=[]
  for t,grp in g.groupby('t',sort=True):
   prior=[h for h in hist if h[0]<t];p5=[h for h in prior if h[0]>=t-5000];p15=[h for h in prior if h[0]>=t-15000];trans=sum(p15[i][1]!=p15[i-1][1] for i in range(1,len(p15))) if len(p15)>1 else 0;since=(t-prior[-1][0])/1000. if prior else 999.;age=0.
   if prior:
    lp=prior[-1][1];st=prior[-1][0];j=len(prior)-2
    while j>=0 and prior[j][1]==lp and prior[j+1][0]-prior[j][0]<=15000:st=prior[j][0];j-=1
    age=(t-st)/1000.
   mem={'events5':len(p5),'events15':len(p15),'transitions15':trans,'since_prev':since,'last_purpose_run_age':age}
   pending=[]
   for _,r in grp.iterrows():
    state=v1.norm_state(r,mem);gap=max(abs(v1.safe(r.abs_gap)),18.);commit=float(np.log1p(max(0.,v1.safe(r.shares))/gap));tok=v1.add_hist(state,str(r.action),commit,np.clip(v1.safe(r.price),0.,1.),False);pending.append((int(t),str(r.purpose),str(r.role),str(r.action),tok))
   hist.extend(pending);records.extend(pending)
  out[int(mid)]=records
 return out

def build_grid():
 d=v1.build_rows();actions=build_action_tokens(d);ev=pd.read_csv(D/'events.csv').replace([np.inf,-np.inf],np.nan);ev.market_id=ev.market_id.astype(int);ev.event_ms=ev.event_ms.astype(np.int64);ev.role=ev.role.astype(str).str.upper();ev.side=ev.side.astype(str);life=pd.read_csv(D/'lifecycles.csv').replace([np.inf,-np.inf],np.nan);life.market_id=life.market_id.astype(int);life.placement_first_ms=life.placement_first_ms.astype(np.int64);life.last_target_ms=life.last_target_ms.astype(np.int64);mk=pd.read_csv(D/'markets.csv');wend={int(r.market_id):int(r.window_end_ms) for r in mk.itertuples()}
 eg={int(m):g.sort_values('event_ms').reset_index(drop=True) for m,g in ev.groupby('market_id')};lg={int(m):g.copy() for m,g in life.groupby('market_id')};fill_idx={}
 for (m,h),g in ev[ev.role.eq('MAKER')].dropna(subset=['order_hash']).groupby(['market_id','order_hash']):
  g=g.sort_values('event_ms');fill_idx[(int(m),str(h))]=(g.event_ms.to_numpy(np.int64),np.cumsum(g.shares.fillna(0).to_numpy(float)))
 def confirmed(mid,h,t):
  q=fill_idx.get((int(mid),str(h)))
  if q is None:return 0.
  tt,cs=q;k=np.searchsorted(tt,int(t),'left')-1;return float(cs[k]) if k>=0 else 0.
 X=[];M=[];Y=[];PH=[];MIDS=[];TIMES=[]
 for mid,recs in actions.items():
  ge=eg.get(mid);gl=lg.get(mid);we=wend.get(mid)
  if ge is None or gl is None or we is None:continue
  tt=ge.event_ms.to_numpy(np.int64);side=ge.side.to_numpy();role=ge.role.to_numpy();sh=ge.shares.fillna(0).to_numpy(float);px=ge.price.fillna(0).to_numpy(float);upcs=np.cumsum(np.where(side=='UP',sh,0.));dncs=np.cumsum(np.where(side=='DOWN',sh,0.));ccs=np.cumsum(sh*px);ats=np.asarray([r[0] for r in recs],np.int64);pur=np.asarray([r[1] for r in recs],object);rol=np.asarray([r[2] for r in recs],object);tokens=[r[4] for r in recs]
  for t in range(int(we)-300000,int(we),1000):
   k=np.searchsorted(tt,t,'left')-1;up=float(upcs[k]) if k>=0 else 0.;dn=float(dncs[k]) if k>=0 else 0.;cost=float(ccs[k]) if k>=0 else 0.;gap=up-dn;gross=up+dn;floor=min(up,dn)-cost;upside=max(up,dn)-cost
   if gap>1e-9:dom,weak='UP','DOWN'
   elif gap<-1e-9:dom,weak='DOWN','UP'
   else:dom=weak=None
   active=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)]
   def own(side_):
    if side_ is None or not len(active):return (0,0.,0.)
    z=active[active.target_side.astype(str)==side_];commit=real=0.
    for q in z.itertuples(index=False):
     c=v1.safe(q.placement_allocated_shares,v1.safe(q.expected_parent_shares));rr=min(c,confirmed(mid,str(q.order_hash),t));commit+=max(0.,c);real+=max(0.,rr)
    return len(z),max(0.,commit-real),real/commit if commit>1e-9 else 0.
   wa,wu,wp=own(weak);da,du,dp=own(dom);lo=np.searchsorted(tt,t-5000,'left');hi=np.searchsorted(tt,t,'left');recent_idx=np.arange(lo,hi);wf=float(sh[recent_idx][(role[recent_idx]=='MAKER')&(side[recent_idx]==weak)].sum()) if weak and len(recent_idx) else 0.;df=float(sh[recent_idx][(role[recent_idx]=='MAKER')&(side[recent_idx]==dom)].sum()) if dom and len(recent_idx) else 0.;cov=2*min(up,dn)/gross if gross>1e-9 else 0.;sec=(we-t)/1000.
   pi=np.searchsorted(ats,t,'left');prior_start=np.searchsorted(ats,t-15000,'left');p5_start=np.searchsorted(ats,t-5000,'left');p15=list(range(prior_start,pi));p5=list(range(p5_start,pi));trans=sum(pur[p15[i]]!=pur[p15[i-1]] for i in range(1,len(p15))) if len(p15)>1 else 0;since=(t-ats[pi-1])/1000. if pi else 999.;age=0.
   if pi:
    lp=pur[pi-1];st=ats[pi-1];j=pi-2
    while j>=0 and pur[j]==lp and ats[j+1]-ats[j]<=15000:st=ats[j];j-=1
    age=(t-st)/1000.
   mem={'events5':len(p5),'events15':len(p15),'transitions15':trans,'since_prev':since,'last_purpose_run_age':age};row={'seconds_left':sec,'abs_gap':abs(gap),'risk_deficit':max(0.,-floor),'floor':floor,'upside':upside,'coverage':cov,'floor_per_gross':floor/gross if gross>1e-9 else 0.,'gross':gross,'weak_active_roots':wa,'dominant_active_roots':da,'weak_unresolved_shares':wu,'dominant_unresolved_shares':du,'weak_progress_ratio':wp,'dominant_progress_ratio':dp,'weak_fill_shares_5s':wf,'dominant_fill_shares_5s':df};query=v1.add_hist(v1.norm_state(row,mem),'MAKER_REPAIR',0.,0.,True);hist_tokens=tokens[max(0,pi-(v1.SEQ-1)):pi];arr=np.zeros((v1.SEQ,len(v1.FEATURES)),np.float32);mask=np.ones(v1.SEQ,bool);st=v1.SEQ-1-len(hist_tokens)
   if hist_tokens:arr[st:v1.SEQ-1]=np.stack(hist_tokens);mask[st:v1.SEQ-1]=False
   arr[-1]=query;mask[-1]=False;f0=np.searchsorted(ats,t,'right');f1=np.searchsorted(ats,t+1000,'right');future_idx=np.arange(f0,f1);ya=int(len(future_idx)>0);yt=int(any(rol[j]=='TAKER' for j in future_idx));yadd=int(any(pur[j]=='ADD' for j in future_idx));X.append(arr);M.append(mask);Y.append([ya,yt,yadd]);PH.append(0 if sec>180 else 1 if sec>=60 else 2);MIDS.append(mid);TIMES.append(t)
 return np.stack(X),np.stack(M),np.asarray(Y,np.float32),np.asarray(PH,np.int64),np.asarray(MIDS,np.int64),np.asarray(TIMES,np.int64)

class HazardSeq(nn.Module):
 def __init__(self,fd):
  super().__init__();self.inp=nn.Sequential(nn.Linear(fd,96),nn.LayerNorm(96),nn.GELU());self.pos=nn.Parameter(torch.zeros(1,v1.SEQ,96));enc=nn.TransformerEncoderLayer(96,4,256,.1,batch_first=True,norm_first=True,activation='gelu');self.enc=nn.TransformerEncoder(enc,3);self.norm=nn.LayerNorm(96);self.out=nn.Linear(96,3)
 def forward(self,x,m):return self.out(self.norm(self.enc(self.inp(x)+self.pos,src_key_padding_mask=m)[:,-1]))
class HazardMLP(nn.Module):
 def __init__(self,fd):super().__init__();self.net=nn.Sequential(nn.Linear(fd,128),nn.GELU(),nn.Dropout(.1),nn.Linear(128,64),nn.GELU(),nn.Linear(64,3))
 def forward(self,x,m):return self.net(x[:,-1])

def ds(X,M,Y,PH,MIDS,split_map,k):
 ix=np.where(np.array([split_map[int(m)]==k for m in MIDS]))[0];return TensorDataset(torch.from_numpy(X[ix]),torch.from_numpy(M[ix]),torch.from_numpy(Y[ix]),torch.from_numpy(PH[ix]),torch.from_numpy(MIDS[ix]))
def train(model,tr,va,device,seed):
 seed_all(seed);ys=np.concatenate([b[2].numpy() for b in DataLoader(tr,batch_size=4096)]);pos=ys.sum(0);neg=len(ys)-pos;pw=torch.tensor(neg/np.maximum(pos,1),dtype=torch.float32,device=device);lossfn=nn.BCEWithLogitsLoss(pos_weight=pw);opt=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4);model=model.to(device);best=None;bl=1e99;bad=0;hist=[]
 for ep in range(1,61):
  model.train();tot=n=0
  for xb,mb,yb,*_ in DataLoader(tr,batch_size=256,shuffle=True,num_workers=0,pin_memory=device.type=='cuda'):
   xb=xb.to(device,non_blocking=True);mb=mb.to(device,non_blocking=True);yb=yb.to(device);opt.zero_grad(set_to_none=True);loss=lossfn(model(xb,mb),yb);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);opt.step();tot+=float(loss.detach())*len(xb);n+=len(xb)
  model.eval();vl=vn=0
  with torch.no_grad():
   for xb,mb,yb,*_ in DataLoader(va,batch_size=512):
    xb=xb.to(device);mb=mb.to(device);yb=yb.to(device);q=lossfn(model(xb,mb),yb);vl+=float(q)*len(xb);vn+=len(xb)
  vl/=max(vn,1);hist.append({'epoch':ep,'trainLoss':tot/max(n,1),'valLoss':vl})
  if vl<bl-1e-5:bl=vl;best=copy.deepcopy({k:v.detach().cpu() for k,v in model.state_dict().items()});bad=0
  else:bad+=1
  if bad>=8:break
 model.load_state_dict(best);return model,hist
@torch.no_grad()
def evaluate(model,data,device):
 model.eval();P=[];Y=[];PH=[]
 for xb,mb,yb,pb,*_ in DataLoader(data,batch_size=512):P.append(torch.sigmoid(model(xb.to(device),mb.to(device))).cpu().numpy());Y.append(yb.numpy());PH.append(pb.numpy())
 p=np.concatenate(P);y=np.concatenate(Y);ph=np.concatenate(PH)
 def one(j,ix):
  yy=y[ix,j];pp=p[ix,j];zz=(pp>=.5).astype(int);return {'n':int(len(ix)),'positiveSupport':int(yy.sum()),'positiveRate':float(yy.mean()),'auc':float(roc_auc_score(yy,pp)) if len(np.unique(yy))>1 else None,'ap':float(average_precision_score(yy,pp)) if len(np.unique(yy))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,zz)) if len(np.unique(yy))>1 else None,'positiveRecall':float(recall_score(yy,zz,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(yy,zz,pos_label=0,zero_division=0))}
 out={};allix=np.arange(len(y));names={0:'FORMATION_180_300',1:'MANAGEMENT_60_180',2:'PROTECTION_0_60'}
 for j,l in enumerate(LABELS):
  q=one(j,allix);q['phases']={nm:one(j,np.where(ph==k)[0]) for k,nm in names.items()};out[l]=q
 return out

def main():
 assert CON['status']=='FROZEN_BEFORE_DATASET_BUILD_AND_TRAINING';seed_all();X,M,Y,PH,MIDS,TIMES=build_grid();trm=set(map(int,V1REP['dataset']['trainMarketIds']));vam=set(map(int,V1REP['dataset']['validationMarketIds']));tem=set(map(int,V1REP['dataset']['testMarketIds']));split_map={m:0 for m in trm}|{m:1 for m in vam}|{m:2 for m in tem};tr=ds(X,M,Y,PH,MIDS,split_map,0);va=ds(X,M,Y,PH,MIDS,split_map,1);te=ds(X,M,Y,PH,MIDS,split_map,2);device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu');seq,sh=train(HazardSeq(X.shape[-1]),tr,va,device,61001);mlp,mh=train(HazardMLP(X.shape[-1]),tr,va,device,61101);sm=evaluate(seq,te,device);bm=evaluate(mlp,te,device);delta={l:{'auc':sm[l]['auc']-bm[l]['auc'],'ap':sm[l]['ap']-bm[l]['ap'],'balancedAccuracy':sm[l]['balancedAccuracy']-bm[l]['balancedAccuracy']} for l in LABELS};keep=delta['anyAction1s']['auc']>0 and delta['anyAction1s']['ap']>0 and (delta['takerAction1s']['auc']>0 or delta['addAction1s']['auc']>0) and min(delta[l]['auc'] for l in LABELS)>=-.03;rep={'version':'R4_TARGET_SEQUENCE_HAZARD_V1_REPORT','researchOnly':True,'actionAuthority':False,'device':str(device),'cudaDevice':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'dataset':{'examples':int(len(X)),'markets':int(len(np.unique(MIDS))),'train':len(tr),'validation':len(va),'test':len(te),'testLabelRates':{LABELS[j]:float(Y[np.array([split_map[int(m)]==2 for m in MIDS]),j].mean()) for j in range(3)}},'sequence':sm,'queryMLP':bm,'deltas':delta,'keep':bool(keep),'training':{'sequence':sh,'queryMLP':mh},'guards':CON['guards']};OUT.write_text(json.dumps(rep,indent=2));torch.save({'version':'R4_TARGET_SEQUENCE_HAZARD_V1','researchOnly':True,'actionAuthority':False,'features':v1.FEATURES,'sequenceLength':v1.SEQ,'stateDict':{k:v.cpu() for k,v in seq.state_dict().items()},'labels':LABELS,'keep':bool(keep),'guards':CON['guards']},CKPT);print(json.dumps({'device':str(device),'cudaDevice':rep['cudaDevice'],'dataset':rep['dataset'],'keep':keep,'sequence':sm,'queryMLP':bm,'deltas':delta},indent=2),flush=True)
if __name__=='__main__':main()
