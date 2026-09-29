from __future__ import annotations
import json, math, random
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, recall_score
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_model_benchmark_v1.json'
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
FEATURES=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
SEQ_LEN=16
SEED=260827

def seed_all(s):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)
    if torch.cuda.is_available(): torch.cuda.manual_seed_all(s)

def metrics(y, p):
    y=np.asarray(y); p=np.asarray(p,float)
    pred=np.asarray(CLASSES)[np.argmax(p,axis=1)]
    Y=label_binarize(y,classes=CLASSES)
    aps=[]
    for j in range(len(CLASSES)):
        aps.append(float(average_precision_score(Y[:,j],p[:,j])) if Y[:,j].sum()>0 else float('nan'))
    auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'))
    rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0)
    return {
        'n':int(len(y)), 'macroAuc':auc, 'macroAp':float(np.nanmean(aps)),
        'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=CLASSES)),
        'balancedAccuracy':float(balanced_accuracy_score(y,pred)),
        'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}
    }

def market_order(d):
    return d.groupby('market_id').t.min().sort_values().index.astype(int).tolist()

def blocks(ms):
    initial=min(200,max(120,int(len(ms)*2/3))); rem=len(ms)-initial
    sizes=[rem//4]*4
    for i in range(rem%4): sizes[i]+=1
    cur=initial; out=[]
    for bi,sz in enumerate(sizes,1):
        if sz<=0: continue
        out.append((bi,ms[:cur],ms[cur:cur+sz])); cur+=sz
    return out

def fit_scaler(df):
    x=df[FEATURES].to_numpy(np.float32)
    mu=np.nanmean(x,axis=0); sd=np.nanstd(x,axis=0); sd=np.where(sd<1e-6,1.0,sd)
    return mu.astype(np.float32),sd.astype(np.float32)

def build_seq(all_d, eligible, mu, sd):
    xs=[]; ys=[]; mids=[]; times=[]
    class_to_i={c:i for i,c in enumerate(CLASSES)}
    elig_idx=set(eligible.index.tolist())
    for mid,g in all_d.groupby('market_id',sort=False):
        g=g.sort_values('t')
        arr=((g[FEATURES].to_numpy(np.float32)-mu)/sd)
        idxs=g.index.to_numpy()
        for j,idx in enumerate(idxs):
            if idx not in elig_idx: continue
            lo=max(0,j-SEQ_LEN+1); s=arr[lo:j+1]
            if len(s)<SEQ_LEN:
                pad=np.repeat(s[:1],SEQ_LEN-len(s),axis=0) if len(s) else np.zeros((SEQ_LEN,len(FEATURES)),np.float32)
                s=np.concatenate([pad,s],axis=0)
            xs.append(s); ys.append(class_to_i[str(all_d.at[idx,'management_label_5s'])]); mids.append(int(mid)); times.append(int(all_d.at[idx,'t']))
    return np.asarray(xs,np.float32),np.asarray(ys,np.int64),mids,times

class TCN(nn.Module):
    def __init__(self,nf,nc):
        super().__init__()
        self.net=nn.Sequential(
            nn.Conv1d(nf,48,3,padding=2,dilation=1),nn.ReLU(),nn.Dropout(.1),
            nn.Conv1d(48,64,3,padding=4,dilation=2),nn.ReLU(),nn.Dropout(.1),
            nn.Conv1d(64,64,3,padding=8,dilation=4),nn.ReLU(),
        )
        self.head=nn.Sequential(nn.AdaptiveAvgPool1d(1),nn.Flatten(),nn.Linear(64,nc))
    def forward(self,x):
        z=self.net(x.transpose(1,2)); return self.head(z)

class TinyTransformer(nn.Module):
    def __init__(self,nf,nc):
        super().__init__(); d=64
        self.inp=nn.Linear(nf,d)
        self.pos=nn.Parameter(torch.zeros(1,SEQ_LEN,d))
        layer=nn.TransformerEncoderLayer(d_model=d,nhead=4,dim_feedforward=128,dropout=.1,batch_first=True,norm_first=True)
        self.enc=nn.TransformerEncoder(layer,num_layers=2)
        self.norm=nn.LayerNorm(d); self.head=nn.Linear(d,nc)
    def forward(self,x):
        z=self.inp(x)+self.pos[:,:x.shape[1]]; z=self.enc(z); z=self.norm(z[:,-1]); return self.head(z)

def train_torch(model,xtr,ytr,xva,yva,device,epochs=28):
    model.to(device)
    counts=np.bincount(ytr,minlength=len(CLASSES)).astype(np.float32)
    w=counts.sum()/np.maximum(counts,1); w=w/w.mean()
    lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device))
    opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4)
    ds=TensorDataset(torch.from_numpy(xtr),torch.from_numpy(ytr)); dl=DataLoader(ds,batch_size=128,shuffle=True,num_workers=0)
    best=None; best_loss=1e9; patience=6; bad=0
    for ep in range(epochs):
        model.train()
        for xb,yb in dl:
            xb=xb.to(device); yb=yb.to(device); opt.zero_grad(set_to_none=True); loss=lossfn(model(xb),yb); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),2.0); opt.step()
        model.eval()
        with torch.no_grad():
            pv=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()
        vl=log_loss(yva,np.clip(pv,1e-7,1-1e-7),labels=list(range(len(CLASSES))))
        if vl<best_loss-1e-4:
            best_loss=vl; best={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}; bad=0
        else:
            bad+=1
            if bad>=patience: break
    if best is not None: model.load_state_dict(best)
    model.eval()
    with torch.no_grad(): p=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()
    return p, ep+1

def main():
    seed_all(SEED)
    d=pd.read_csv(SRC)
    d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FEATURES).copy().sort_values(['market_id','t']).reset_index(drop=True)
    elig=d[(d.build_now==1)&d.management_label_5s.notna()&(d.management_label_5s!='')].copy()
    ms=market_order(d); bspec=blocks(ms)
    device='cuda' if torch.cuda.is_available() else 'cpu'
    results=[]
    for bi,trm,tem in bspec:
        seed_all(SEED+bi)
        tr_all=d[d.market_id.isin(trm)].copy(); te_all=d[d.market_id.isin(tem)].copy()
        tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy()
        te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy()
        block={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(tr),'testRows':len(te)}
        # LightGBM current-state baseline
        lgb=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.0,random_state=SEED+bi,verbosity=-1,n_jobs=-1)
        lgb.fit(tr[FEATURES],tr.management_label_5s)
        pp=lgb.predict_proba(te[FEATURES]); order=list(lgb.classes_); p=np.column_stack([pp[:,order.index(c)] for c in CLASSES])
        block['LIGHTGBM']=metrics(te.management_label_5s,p)
        # sequence models; scaler fit only on training chronology
        mu,sd=fit_scaler(tr_all)
        xtr,ytr,_,_=build_seq(tr_all,tr,mu,sd); xte,yte,_,_=build_seq(te_all,te,mu,sd)
        for name,ctor in [('TCN',lambda:TCN(len(FEATURES),len(CLASSES))),('TINY_TRANSFORMER',lambda:TinyTransformer(len(FEATURES),len(CLASSES)))]:
            seed_all(SEED+bi+(11 if name=='TCN' else 29))
            p,eps=train_torch(ctor(),xtr,ytr,xte,yte,device)
            mm=metrics(np.asarray(CLASSES)[yte],p); mm['epochs']=eps; block[name]=mm
        results.append(block)
        print(json.dumps({'block':bi,'LIGHTGBM':block['LIGHTGBM'],'TCN':block['TCN'],'TINY_TRANSFORMER':block['TINY_TRANSFORMER']},ensure_ascii=False))
    def summarize(name):
        x=[b[name] for b in results]
        return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'stdMacroAuc':float(np.std([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanRecall':{c:float(np.mean([q['perClassRecall'][c] for q in x])) for c in CLASSES}}
    summary={n:summarize(n) for n in ['LIGHTGBM','TCN','TINY_TRANSFORMER']}
    base_path=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1.json'
    existing=None
    if base_path.exists():
        existing=json.loads(base_path.read_text(encoding='utf-8')).get('summary',{}).get('M1_FULL')
    art={'version':'R4_MANAGEMENT_MODEL_BENCHMARK_V1','researchOnly':True,'actionAuthority':False,'task':'M1 management_label_5s on current BUILD responsibility','classes':CLASSES,'features':FEATURES,'sequence':{'lengthRows':SEQ_LEN,'causal':True,'padding':'repeat earliest available state','note':'TCN/Transformer receive only current-and-past rows within same market; no future state.'},'coverage':{'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'phaseRows':int(len(d)),'eligibleRows':int(len(elig)),'markets':int(d.market_id.nunique()),'special20260816ExcludedBySource':True},'device':device,'summary':summary,'existingLarge300HGB_M1_FULL':existing,'blocks':results,'guards':['Same chronological market blocks as R4_MANAGEMENT_LARGE300_V1.','No winner/settlement/future features.','Sequence inputs are strictly causal and market-local.','Research only; no action authority and no runtime promotion.']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'device':device,'coverage':art['coverage'],'summary':summary,'existingLarge300HGB_M1_FULL':existing},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
