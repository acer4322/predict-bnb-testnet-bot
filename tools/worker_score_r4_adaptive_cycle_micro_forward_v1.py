from pathlib import Path
import argparse,json,sys
import numpy as np, torch
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score

def probs(m,X,M,ix,dev):
 m.eval();o=[]
 with torch.no_grad():
  for s in range(0,len(ix),512):
   q=ix[s:s+512];o.append(torch.softmax(m(X[q].to(dev),M[q].to(dev)),1)[:,1].cpu().numpy())
 return np.concatenate(o)
def met(p,y):
 y=np.asarray(y,int);z=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--cache',required=True);ap.add_argument('--baseline-checkpoint',required=True);ap.add_argument('--adaptive-checkpoint',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();b=Path(a.bundle_dir).resolve();sys.path.insert(0,str(b));import train_r4_target_sequence_teacher_v11_factorized as v11
 c=torch.load(a.cache,map_location='cpu',weights_only=False);X=c['HX'].float();M=c['HM'].bool();mids=c['HMIDS'].numpy().astype(int);qt=c['HTIMES'].numpy().astype(np.int64);at=c['times'].numpy().astype(np.int64);am=c['mids'].numpy().astype(int);purpose=c['purpose'].numpy().astype(int)
 yr=np.zeros(len(qt),np.int64);ya=np.zeros(len(qt),np.int64)
 for mid in np.unique(mids):
  qi=np.where(mids==mid)[0];ai=np.where(am==mid)[0];tt=at[ai];pp=purpose[ai];o=np.argsort(tt);tt=tt[o];pp=pp[o]
  for q in qi:
   lo=np.searchsorted(tt,qt[q],'right');hi=np.searchsorted(tt,qt[q]+5000,'right');z=pp[lo:hi]
   if len(z):yr[q]=int(np.any(z==0));ya[q]=int(np.any(z==1))
 dev=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu');baseck=torch.load(a.baseline_checkpoint,map_location='cpu',weights_only=False);base=baseck['states']['purposeBinary'];br=v11.SeqBinary(X.shape[-1]);ba=v11.SeqBinary(X.shape[-1]);br.load_state_dict(base);ba.load_state_dict(base);rs=br.state_dict();rs['out.weight']=rs['out.weight'][[1,0]].clone();rs['out.bias']=rs['out.bias'][[1,0]].clone();br.load_state_dict(rs);br=br.to(dev);ba=ba.to(dev)
 adck=torch.load(a.adaptive_checkpoint,map_location='cpu',weights_only=False);ar=v11.SeqBinary(X.shape[-1]);aa=v11.SeqBinary(X.shape[-1]);ar.load_state_dict(adck['repairState']);aa.load_state_dict(adck['addState']);ar=ar.to(dev);aa=aa.to(dev);ix=np.arange(len(mids),dtype=np.int64)
 out={'version':'R4_ADAPTIVE_CYCLE_MICRO_FORWARD_V1_SCORE','researchOnly':True,'actionAuthority':False,'markets':sorted(map(int,np.unique(mids))),'queries':int(len(ix)),'repair':{'baseline':met(probs(br,X,M,ix,dev),yr),'adaptive':met(probs(ar,X,M,ix,dev),yr)},'stateShaping':{'baseline':met(probs(ba,X,M,ix,dev),ya),'adaptive':met(probs(aa,X,M,ix,dev),ya)},'perMarket':[]}
 for mid in sorted(np.unique(mids)):
  q=np.where(mids==mid)[0];out['perMarket'].append({'marketId':int(mid),'n':int(len(q)),'repair':{'baseline':met(probs(br,X,M,q,dev),yr[q]),'adaptive':met(probs(ar,X,M,q,dev),yr[q])},'stateShaping':{'baseline':met(probs(ba,X,M,q,dev),ya[q]),'adaptive':met(probs(aa,X,M,q,dev),ya[q])}})
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2),flush=True)
if __name__=='__main__':main()
