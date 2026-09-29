from __future__ import annotations
import argparse, json, math, os, random, sqlite3, sys, time
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, average_precision_score

SEED=20260831
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
FEATURES=[
 'seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio',
 'prior_taker_frac','prev_maker_rel','prev_maker_age_log','last_delta_absnet',
 'last_delta_paircov','last_delta_floor_ratio','avg_cost_gap','gross_log'
]
TASKS=('repair','switch','reentry')


def safe_auc(y,p):
    return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def metrics(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float); pred=(p>=.5).astype(int)
    return {
      'n':int(len(y)), 'positiveRate':float(y.mean()) if len(y) else None,
      'auc':safe_auc(y,p),
      'averagePrecision':float(average_precision_score(y,p)) if len(y) and y.sum()>0 else None,
      'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(y) else None,
    }

def rel_for(side,up,dn):
    if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
    weak=(side=='UP' and up<dn) or (side=='DOWN' and dn<up)
    return 1 if weak else -1

def build_rows(db):
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row
    mend={(r['asset'],int(r['market_id'])):int(r['window_end_ms']) for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset in ('BTC','ETH') and window_end_ms is not null")}
    btc_ends={v for (a,_),v in mend.items() if a=='BTC'}; eth_ends={v for (a,_),v in mend.items() if a=='ETH'}; common=sorted(btc_ends & eth_ends)
    cutoff=common[int(len(common)*.70)]
    rows=c.execute("select parent_id,asset,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id")
    out=[]; cur=None; up=dn=cost=0.; upcost=dncost=0.; maker_n=taker_n=0; prev_mrel=0; prev_mtime=None
    last_abs=0.; last_pc=1.; last_floor=0.
    for r in rows:
        k=(str(r['asset']),int(r['market_id']))
        if k!=cur:
            cur=k; up=dn=cost=0.;upcost=dncost=0.;maker_n=taker_n=0;prev_mrel=0;prev_mtime=None;last_abs=0.;last_pc=1.;last_floor=0.
        t=int(r['first_event_ms']); side=str(r['side']); role=str(r['role']); sh=float(r['shares']); px=float(r['average_price']); end=mend.get(k)
        gross=up+dn; paired=min(up,dn); gap=abs(up-dn); pc=2*paired/gross if gross>1e-9 else 1.; absr=gap/gross if gross>1e-9 else 0.; floor=(paired-cost); fr=floor/max(cost,1.0); best=max(up,dn)-cost; br=best/max(cost,1.0)
        sl=((end-t)/1000.) if end else 0.; prevage=(t-prev_mtime) if prev_mtime is not None else 1e6
        avu=upcost/up if up>1e-9 else 0.; avd=dncost/dn if dn>1e-9 else 0.
        mrel=rel_for(side,up,dn) if role=='MAKER' else 0
        if role=='MAKER' and mrel!=0:
            x=[
              max(-30.,min(330.,sl))/300., pc, absr, max(-5.,min(5.,fr)), max(-5.,min(5.,br)),
              taker_n/max(maker_n+taker_n,1), float(prev_mrel), math.log1p(min(prevage,120000.))/math.log1p(120000.),
              absr-last_abs, pc-last_pc, max(-5.,min(5.,fr-last_floor)), max(-1.,min(1.,avu-avd)), math.log1p(gross)/math.log1p(500.)
            ]
            repair=1 if mrel==1 else 0
            switch=None if prev_mrel==0 else int(mrel!=prev_mrel)
            reentry=None if prev_mrel!=1 else int(mrel==-1)
            out.append({'asset':k[0],'market':k[1],'end':end,'x':x,'repair':repair,'switch':switch,'reentry':reentry})
        # materialize actual parent after strict-past row construction
        if side=='UP':up+=sh;upcost+=sh*px
        else:dn+=sh;dncost+=sh*px
        cost+=sh*px
        ng=up+dn; npair=min(up,dn); ngap=abs(up-dn); last_abs=ngap/ng if ng>1e-9 else 0.; last_pc=2*npair/ng if ng>1e-9 else 1.; last_floor=(npair-cost)/max(cost,1.0)
        if role=='MAKER' and mrel!=0:prev_mrel=mrel;prev_mtime=t;maker_n+=1
        elif role=='TAKER':taker_n+=1
    c.close(); return out,cutoff,len(common)

class SharedAssetHeads(nn.Module):
    def __init__(self,d):
        super().__init__();self.enc=nn.Sequential(nn.Linear(d,64),nn.ReLU(),nn.LayerNorm(64),nn.Linear(64,32),nn.ReLU())
        self.heads=nn.ModuleDict({f'{a}_{t}':nn.Linear(32,1) for a in ('BTC','ETH') for t in TASKS})
    def forward(self,x,asset,task):return self.heads[f'{asset}_{task}'](self.enc(x)).squeeze(-1)
class SingleAsset(nn.Module):
    def __init__(self,d):
        super().__init__();self.enc=nn.Sequential(nn.Linear(d,64),nn.ReLU(),nn.LayerNorm(64),nn.Linear(64,32),nn.ReLU());self.heads=nn.ModuleDict({t:nn.Linear(32,1) for t in TASKS})
    def forward(self,x,task):return self.heads[task](self.enc(x)).squeeze(-1)


def prep(rows,cutoff):
    dd={}
    for asset in ('BTC','ETH'):
      for period in ('train','test'):
       z=[r for r in rows if r['asset']==asset and ((r['end']<cutoff) if period=='train' else (r['end']>=cutoff))]
       dd[(asset,period)]=z
    # deterministic BTC thinning so shared representation cannot be overwhelmed by BTC volume
    ne=len(dd[('ETH','train')]); b=dd[('BTC','train')]
    if len(b)>int(ne*1.5):
      idx=np.linspace(0,len(b)-1,int(ne*1.5)).astype(int);dd[('BTC','train')]=[b[i] for i in idx]
    return dd

def standardize(dd):
    tr=dd[('BTC','train')]+dd[('ETH','train')];X=np.asarray([r['x'] for r in tr],np.float32);mu=X.mean(0);sd=X.std(0);sd=np.where(sd<1e-5,1.,sd)
    for k,z in dd.items():
      for r in z:r['xs']=((np.asarray(r['x'],np.float32)-mu)/sd).astype(np.float32)
    return mu,sd

def task_arrays(z,task):
    zz=[r for r in z if r[task] is not None];return np.asarray([r['xs'] for r in zz],np.float32),np.asarray([r[task] for r in zz],np.float32)

def batch_train_shared(model,dd,device,epochs=14):
    model.to(device);opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4);lossfn=nn.BCEWithLogitsLoss();rng=np.random.default_rng(SEED)
    pools={}
    for a in ('BTC','ETH'):
      for t in TASKS:pools[(a,t)]=task_arrays(dd[(a,'train')],t)
    hist=[]
    for ep in range(epochs):
      model.train();losses=[]
      for _ in range(180):
        opt.zero_grad();total=0.
        for a in ('BTC','ETH'):
          for t in TASKS:
            X,y=pools[(a,t)]; n=min(256,len(y)); idx=rng.integers(0,len(y),size=n);xt=torch.from_numpy(X[idx]).to(device);yt=torch.from_numpy(y[idx]).to(device)
            logits=model(xt,a,t); pos=max(float(yt.mean().item()),1e-4);w=torch.tensor((1-pos)/pos,device=device).clamp(.25,4.); total=total+nn.functional.binary_cross_entropy_with_logits(logits,yt,pos_weight=w)
        total.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.);opt.step();losses.append(float(total.detach().cpu()))
      hist.append(float(np.mean(losses)))
    return hist

def batch_train_single(model,z,device,epochs=14):
    model.to(device);opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4);rng=np.random.default_rng(SEED);pools={t:task_arrays(z,t) for t in TASKS};hist=[]
    for ep in range(epochs):
      model.train();losses=[]
      for _ in range(180):
        opt.zero_grad();total=0.
        for t in TASKS:
          X,y=pools[t];n=min(384,len(y));idx=rng.integers(0,len(y),size=n);xt=torch.from_numpy(X[idx]).to(device);yt=torch.from_numpy(y[idx]).to(device);logits=model(xt,t);pos=max(float(yt.mean().item()),1e-4);w=torch.tensor((1-pos)/pos,device=device).clamp(.25,4.);total=total+nn.functional.binary_cross_entropy_with_logits(logits,yt,pos_weight=w)
        total.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.);opt.step();losses.append(float(total.detach().cpu()))
      hist.append(float(np.mean(losses)))
    return hist

def eval_shared(model,z,device,asset='ETH'):
    model.eval();out={}
    with torch.no_grad():
      for t in TASKS:
        X,y=task_arrays(z,t);ps=[]
        for i in range(0,len(y),4096):ps.append(torch.sigmoid(model(torch.from_numpy(X[i:i+4096]).to(device),asset,t)).cpu().numpy())
        p=np.concatenate(ps) if ps else np.array([]);out[t]=metrics(y,p)
    return out

def eval_single(model,z,device):
    model.eval();out={}
    with torch.no_grad():
      for t in TASKS:
        X,y=task_arrays(z,t);ps=[]
        for i in range(0,len(y),4096):ps.append(torch.sigmoid(model(torch.from_numpy(X[i:i+4096]).to(device),t)).cpu().numpy())
        p=np.concatenate(ps) if ps else np.array([]);out[t]=metrics(y,p)
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args()
    rows,cutoff,common=build_rows(a.db);dd=prep(rows,cutoff);mu,sd=standardize(dd);device='cuda' if torch.cuda.is_available() else 'cpu'
    eth=SingleAsset(len(FEATURES)); shared=SharedAssetHeads(len(FEATURES)); pooled=SingleAsset(len(FEATURES))
    h_eth=batch_train_single(eth,dd[('ETH','train')],device)
    h_shared=batch_train_shared(shared,dd,device)
    # direct-copy negative control: one pooled head set trained on combined BTC+ETH without asset-specific policy heads
    h_pool=batch_train_single(pooled,dd[('BTC','train')]+dd[('ETH','train')],device)
    test=dd[('ETH','test')];m_eth=eval_single(eth,test,device);m_shared=eval_shared(shared,test,device,'ETH');m_pool=eval_single(pooled,test,device)
    deltas={t:{'sharedMinusEthOnlyAuc':None if m_eth[t]['auc'] is None else m_shared[t]['auc']-m_eth[t]['auc'],'pooledMinusEthOnlyAuc':None if m_eth[t]['auc'] is None else m_pool[t]['auc']-m_eth[t]['auc']} for t in TASKS}
    improvements=[deltas[t]['sharedMinusEthOnlyAuc'] for t in TASKS if deltas[t]['sharedMinusEthOnlyAuc'] is not None]
    pass_shared=sum(x>=.015 for x in improvements)>=2 and all(x>=-.01 for x in improvements)
    payload={'version':'ETH_PASSIVE_CROSS_ASSET_DISTILL_FEASIBILITY_V1','researchOnly':True,'device':device,'sourceDb':os.path.abspath(a.db),'commonWindows':common,'chronologyCutoff':cutoff,'features':FEATURES,'tasks':TASKS,'counts':{f'{a0}_{p}':len(dd[(a0,p)]) for a0 in ('BTC','ETH') for p in ('train','test')},'models':{'ETH_ONLY':m_eth,'SHARED_ENCODER_ASSET_HEADS':m_shared,'POOLED_SINGLE_POLICY_NEGATIVE_CONTROL':m_pool},'aucDeltas':deltas,'sharedRepresentationPass':bool(pass_shared),'passRule':'shared encoder must improve ETH-late AUC >=0.015 on at least 2/3 lifecycle tasks and degrade none by >0.01','boundary':['Target actual-filled parent sequence only; no maker_book_inference_v21 expected_parent_shares / TARGET_UNIT=18','Strict-past normalized portfolio/lifecycle state only; no winner or future PnL','BTC contributes only through shared representation; runtime ETH decisions would use ETH-specific heads','No raw share-size target or Target numeric threshold is distilled','This is representation feasibility, not HFT profitability evidence'],'trainLossLast':{'ethOnly':h_eth[-1],'shared':h_shared[-1],'pooled':h_pool[-1]}}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(payload,indent=2),encoding='utf-8');torch.save({'version':payload['version'],'features':FEATURES,'mu':mu,'sd':sd,'shared_state_dict':shared.state_dict(),'eth_only_state_dict':eth.state_dict(),'pass':bool(pass_shared)},a.model_out);print(json.dumps({'ok':True,'sharedPass':pass_shared,'deltas':deltas,'models':payload['models']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
