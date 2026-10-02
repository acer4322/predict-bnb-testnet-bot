from __future__ import annotations
import argparse,json,math,os,random,sqlite3
from pathlib import Path
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

SEED=20260831
random.seed(SEED);np.random.seed(SEED);torch.manual_seed(SEED)
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','prev_maker_rel','prev_maker_age_log','avg_cost_gap','gross_log']
TOK_FEATURES=['prev_rel','delta_absnet','delta_paircov','delta_floor','elapsed_log','qty_gap_ratio','price','post_absnet','post_paircov']
LEDGER_FEATURES=['objective_rel','objective_streak_log','repair_frac','expand_frac','switch_rate','cum_repair_absnet_progress','cum_repair_pair_gain','cum_repair_floor_gain','cum_expand_pair_pressure','objective_age_log','maker_count_log','segment_absnet_progress','segment_pair_gain']
TASKS=('repair','switch','reentry');SEQ=8

def rel_for(side,up,dn):
    if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
    return 1 if ((side=='UP' and up<dn) or (side=='DOWN' and dn<up)) else -1

def build(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    mend={(r['asset'],int(r['market_id'])):int(r['window_end_ms']) for r in c.execute("select asset,market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    ends=sorted(set(mend.values()));cut=ends[int(len(ends)*.70)]
    rows=c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id")
    out=[];cur=None
    up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;prev_rel=0;prev_time=None;hist=[]
    repair_n=expand_n=switch_n=same_streak=0;cum_rep_ab=cum_rep_pc=cum_rep_floor=cum_exp_pressure=0.;objective_start=None;seg_ab=seg_pc=0.
    for r in rows:
        mid=int(r['market_id']);k=('ETH',mid)
        if mid!=cur:
            cur=mid;up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;prev_rel=0;prev_time=None;hist=[]
            repair_n=expand_n=switch_n=same_streak=0;cum_rep_ab=cum_rep_pc=cum_rep_floor=cum_exp_pressure=0.;objective_start=None;seg_ab=seg_pc=0.
        role=str(r['role']);side=str(r['side']);t=int(r['first_event_ms']);sh=float(r['shares']);px=float(r['average_price']);end=mend.get(k)
        gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>1e-9 else 1.;ab=gap/gross if gross>1e-9 else 0.;fr=(pair-cost)/max(cost,1.);br=(max(up,dn)-cost)/max(cost,1.);avu=upcost/up if up>1e-9 else 0.;avd=dncost/dn if dn>1e-9 else 0.;age=(t-prev_time) if prev_time is not None else 1e6
        rel=rel_for(side,up,dn) if role=='MAKER' else 0
        if role=='MAKER' and rel!=0:
            curx=np.array([max(-30,min(330,(end-t)/1000))/300 if end else 0.,pc,ab,max(-5,min(5,fr)),max(-5,min(5,br)),taker_n/max(maker_n+taker_n,1),float(prev_rel),math.log1p(min(age,120000))/math.log1p(120000),max(-1,min(1,avu-avd)),math.log1p(gross)/math.log1p(500)],np.float32)
            seq=np.zeros((SEQ,len(TOK_FEATURES)),np.float32);mask=np.zeros(SEQ,np.float32);hh=hist[-SEQ:]
            if hh:seq[-len(hh):]=np.asarray(hh,np.float32);mask[-len(hh):]=1.
            objective_age=(t-objective_start) if objective_start is not None else 0
            led=np.array([
                float(prev_rel),
                math.log1p(same_streak)/math.log1p(32),
                repair_n/max(maker_n,1),
                expand_n/max(maker_n,1),
                switch_n/max(maker_n,1),
                math.tanh(cum_rep_ab/2),
                math.tanh(cum_rep_pc/2),
                math.tanh(cum_rep_floor/4),
                math.tanh(cum_exp_pressure/2),
                math.log1p(min(objective_age,300000))/math.log1p(300000),
                math.log1p(maker_n)/math.log1p(256),
                max(-1,min(1,seg_ab-ab)) if maker_n>0 else 0.,
                max(-1,min(1,pc-seg_pc)) if maker_n>0 else 0.
            ],np.float32)
            out.append({'end':end,'cur':curx,'seq':seq,'mask':mask,'ledger':led,'repair':int(rel==1),'switch':None if prev_rel==0 else int(rel!=prev_rel),'reentry':None if prev_rel!=1 else int(rel==-1)})
        pre_ab,pre_pc,pre_fr=ab,pc,fr;pre_gap=gap
        if side=='UP':up+=sh;upcost+=sh*px
        else:dn+=sh;dncost+=sh*px
        cost+=sh*px
        gross2=up+dn;pair2=min(up,dn);gap2=abs(up-dn);pc2=2*pair2/gross2 if gross2>1e-9 else 1.;ab2=gap2/gross2 if gross2>1e-9 else 0.;fr2=(pair2-cost)/max(cost,1.)
        if role=='MAKER' and rel!=0:
            elapsed=(t-prev_time) if prev_time is not None else 1e6;qgr=sh/max(pre_gap,1.)
            hist.append([float(rel),ab2-pre_ab,pc2-pre_pc,max(-5,min(5,fr2-pre_fr)),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,qgr)),px,ab2,pc2])
            if rel==1:
                repair_n+=1;cum_rep_ab+=max(0.,pre_ab-ab2);cum_rep_pc+=max(0.,pc2-pre_pc);cum_rep_floor+=max(0.,fr2-pre_fr)
            elif rel==-1:
                expand_n+=1;cum_exp_pressure+=max(0.,pre_pc-pc2)
            if prev_rel==0:
                same_streak=1;objective_start=t;seg_ab=ab2;seg_pc=pc2
            elif rel!=prev_rel:
                switch_n+=1;same_streak=1;objective_start=t;seg_ab=ab2;seg_pc=pc2
            else:
                same_streak+=1
            prev_rel=rel;prev_time=t;maker_n+=1
        elif role=='TAKER':taker_n+=1
    c.close();return out,cut,len(ends)

def standardize(train,test):
    X=np.stack([r['cur'] for r in train]);mu=X.mean(0);sd=np.where(X.std(0)<1e-5,1,X.std(0))
    T=np.concatenate([r['seq'][r['mask']>0] for r in train if r['mask'].sum()>0],axis=0);tmu=T.mean(0);tsd=np.where(T.std(0)<1e-5,1,T.std(0))
    L=np.stack([r['ledger'] for r in train]);lmu=L.mean(0);lsd=np.where(L.std(0)<1e-5,1,L.std(0))
    for z in (train,test):
        for r in z:
            r['curz']=((r['cur']-mu)/sd).astype(np.float32)
            r['seqz']=((r['seq']-tmu)/tsd).astype(np.float32)*r['mask'][:,None]
            r['ledgerz']=((r['ledger']-lmu)/lsd).astype(np.float32)
    return mu,sd,tmu,tsd,lmu,lsd

def arrays(z,t):
    zz=[r for r in z if r[t] is not None]
    return np.stack([r['curz'] for r in zz]),np.stack([r['seqz'] for r in zz]),np.stack([r['mask'] for r in zz]),np.stack([r['ledgerz'] for r in zz]),np.asarray([r[t] for r in zz],np.float32)

class BaselineMemory(nn.Module):
    def __init__(self):
        super().__init__();self.gru=nn.GRU(len(TOK_FEATURES),32,batch_first=True);self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),32),nn.ReLU());self.mix=nn.Sequential(nn.Linear(64,48),nn.ReLU(),nn.LayerNorm(48));self.h=nn.ModuleDict({t:nn.Linear(48,1) for t in TASKS})
    def forward(self,cur,seq,ledger,t):
        _,h=self.gru(seq);z=torch.cat([self.cur(cur),h[-1]],1);return self.h[t](self.mix(z)).squeeze(-1)

class Specialist(nn.Module):
    def __init__(self):
        super().__init__();self.gru=nn.GRU(len(TOK_FEATURES),32,batch_first=True);self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),32),nn.ReLU());self.ledger=nn.Sequential(nn.Linear(len(LEDGER_FEATURES),24),nn.ReLU(),nn.LayerNorm(24));self.mix=nn.Sequential(nn.Linear(88,64),nn.ReLU(),nn.LayerNorm(64),nn.Linear(64,48),nn.ReLU());self.h=nn.ModuleDict({t:nn.Linear(48,1) for t in TASKS})
    def forward(self,cur,seq,ledger,t):
        _,h=self.gru(seq);z=torch.cat([self.cur(cur),h[-1],self.ledger(ledger)],1);return self.h[t](self.mix(z)).squeeze(-1)

def train_model(m,tr,dev):
    m.to(dev);opt=torch.optim.AdamW(m.parameters(),lr=1.2e-3,weight_decay=1e-4);rng=np.random.default_rng(SEED);pools={t:arrays(tr,t) for t in TASKS}
    for _ in range(16):
        for __ in range(160):
            opt.zero_grad();loss=0
            for t in TASKS:
                X,S,M,L,y=pools[t];idx=rng.integers(0,len(y),size=min(320,len(y)))
                xt=torch.from_numpy(X[idx]).to(dev);st=torch.from_numpy(S[idx]).to(dev);lt=torch.from_numpy(L[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev)
                log=m(xt,st,lt,t);pos=max(float(yt.mean()),1e-4)
                loss+=nn.functional.binary_cross_entropy_with_logits(log,yt,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.25,4))
            loss.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),5);opt.step()
    return m

def met(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def evaluate(m,z,dev):
    m.eval();out={}
    with torch.no_grad():
        for t in TASKS:
            X,S,M,L,y=arrays(z,t);ps=[]
            for i in range(0,len(y),4096):
                log=m(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev),torch.from_numpy(L[i:i+4096]).to(dev),t)
                ps.append(torch.sigmoid(log).cpu().numpy())
            out[t]=met(y,np.concatenate(ps))
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
    rows,cut,nwin=build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut]
    mu,sd,tmu,tsd,lmu,lsd=standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(SEED);base=train_model(BaselineMemory(),tr,dev);base_m=evaluate(base,te,dev)
    torch.manual_seed(SEED);spec=train_model(Specialist(),tr,dev);spec_m=evaluate(spec,te,dev)
    delta={t:spec_m[t]['auc']-base_m[t]['auc'] for t in TASKS};mean_delta=float(np.mean(list(delta.values())));heads_ge=sum(v>=.01 for v in delta.values());passed=heads_ge>=2 and min(delta.values())>=-.01 and mean_delta>=.01
    out={'version':'ETH_PASSIVE_MAKER_SPECIALIST_V1','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'trainRows':len(tr),'testRows':len(te),'sequenceLength':SEQ,'currentFeatures':CUR_FEATURES,'tokenFeatures':TOK_FEATURES,'ledgerFeatures':LEDGER_FEATURES,'models':{'V2_GRU_BASELINE':base_m,'SPECIALIST_V1_GRU_PLUS_PERSISTENT_LEDGER':spec_m},'aucDeltaSpecialistMinusBaseline':delta,'meanAucDelta':mean_delta,'representationPass':bool(passed),'passRule':'at least 2/3 heads +0.01 AUC, no head below -0.01, mean delta >=0.01','boundary':['ETH Target actual-filled Maker chronology only','Strict-past full-market persistent ledger plus recent materialized lifecycle transitions','No BTC labels/gradients','No winner/future PnL','No TARGET_UNIT=18 or expected_parent_shares','Representation evidence only; must pass Repair Functional Exam and later untouched chronology before promotion']}
    result_dir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    op=Path(a.output) if a.output else result_dir/'specialist_v1_result.json';mp=Path(a.model_out) if a.model_out else result_dir/'specialist_v1_model.pt'
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'tokenFeatures':TOK_FEATURES,'ledgerFeatures':LEDGER_FEATURES,'mu':mu,'sd':sd,'tmu':tmu,'tsd':tsd,'lmu':lmu,'lsd':lsd,'state_dict':spec.state_dict(),'representationPass':bool(passed)},mp)
    print(json.dumps({'ok':True,'pass':bool(passed),'delta':delta,'meanDelta':mean_delta,'baseline':base_m,'specialist':spec_m,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
