from __future__ import annotations
import argparse, json, math, os, random, sqlite3
from pathlib import Path
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

SEED=20260901
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
SEQ=16
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','prev_rel','prev_age_log','avg_cost_gap','gross_log']
TOK_FEATURES=['rel','delta_absnet','delta_paircov','delta_floor','elapsed_log','qty_gap_ratio','price','post_absnet','post_paircov','same_segment_age','same_segment_progress']
TASKS=('same_objective_next','segment_completes_next','activity_within_30s','phase_switch_next')


def rel_for(side,up,dn):
    if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
    return 1 if ((side=='UP' and up<dn) or (side=='DOWN' and dn<up)) else -1

def state(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t):
    gross=up+dn; pair=min(up,dn); gap=abs(up-dn)
    pc=2*pair/gross if gross>1e-9 else 1.; ab=gap/gross if gross>1e-9 else 0.
    fr=(pair-cost)/max(cost,1.); br=(max(up,dn)-cost)/max(cost,1.)
    avu=upcost/up if up>1e-9 else 0.; avd=dncost/dn if dn>1e-9 else 0.
    cur=np.asarray([max(-30,min(330,(end-t)/1000))/300 if end else 0.,pc,ab,max(-5,min(5,fr)),max(-5,min(5,br)),taker_n/max(maker_n+taker_n,1),float(prev_rel),math.log1p(min(prev_age,120000))/math.log1p(120000),max(-1,min(1,avu-avd)),math.log1p(gross)/math.log1p(500)],np.float32)
    return cur,{'gross':gross,'gap':gap,'pc':pc,'ab':ab,'fr':fr}

def build(db):
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    ends=sorted(set(mend.values())); cut=ends[int(len(ends)*.70)]
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id")); c.close()
    by_market={}
    for r in raw: by_market.setdefault(int(r['market_id']),[]).append(r)
    rows=[]; episodes=0
    for mid, evs in by_market.items():
        end=mend.get(mid); up=dn=cost=upcost=dncost=0.; maker_n=taker_n=0; hist=[]; makers=[]
        seg_id=-1; seg_rel=0; seg_start_t=None; seg_start_ab=0.; seg_start_pc=1.
        for r in evs:
            role=str(r['role']); side=str(r['side']); t=int(r['first_event_ms']); sh=float(r['shares']); px=float(r['average_price'])
            prev_rel=makers[-1]['rel'] if makers else 0; prev_age=(t-makers[-1]['time']) if makers else 1e6
            cur,met=state(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t)
            rel=rel_for(side,up,dn) if role=='MAKER' else 0
            if role=='MAKER' and rel!=0:
                if rel!=seg_rel:
                    seg_id+=1; episodes+=1; seg_rel=rel; seg_start_t=t; seg_start_ab=met['ab']; seg_start_pc=met['pc']
                seq=np.zeros((SEQ,len(TOK_FEATURES)),np.float32); mask=np.zeros(SEQ,np.float32); hh=hist[-SEQ:]
                if hh: seq[-len(hh):]=np.asarray(hh,np.float32); mask[-len(hh):]=1.
                rows.append({'market':mid,'end':end,'t':t,'seg':seg_id,'rel':rel,'cur':cur,'seq':seq,'mask':mask,'maker_index':len(makers),'seg_start_t':seg_start_t})
            pre_ab,pre_pc,pre_fr,pre_gap=met['ab'],met['pc'],met['fr'],met['gap']
            if side=='UP': up+=sh; upcost+=sh*px
            else: dn+=sh; dncost+=sh*px
            cost+=sh*px
            if role=='MAKER' and rel!=0:
                _,post=state(up,dn,cost,upcost,dncost,maker_n+1,taker_n,rel,1,end,t)
                elapsed=(t-makers[-1]['time']) if makers else 1e6; qgr=sh/max(pre_gap,1.)
                seg_age=0. if seg_start_t is None else min(1.,max(0.,(t-seg_start_t)/120000.))
                seg_prog=max(-1,min(1,(seg_start_ab-post['ab']) if rel==1 else (post['ab']-seg_start_ab)))
                tok=np.asarray([float(rel),post['ab']-pre_ab,post['pc']-pre_pc,max(-5,min(5,post['fr']-pre_fr)),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,qgr)),px,post['ab'],post['pc'],seg_age,seg_prog],np.float32)
                hist.append(tok); makers.append({'rel':rel,'time':t,'seg':seg_id}); maker_n+=1
            elif role=='TAKER': taker_n+=1
        # future labels within each market, derived only for supervision
        rr=[x for x in rows if x['market']==mid]
        for j,x in enumerate(rr):
            nxt=rr[j+1] if j+1<len(rr) else None
            same= int(nxt is not None and nxt['seg']==x['seg'])
            switch=int(nxt is not None and nxt['rel']!=x['rel'])
            completes=int(nxt is None or nxt['seg']!=x['seg'])
            active30=int(nxt is not None and (nxt['t']-x['t'])<=30000)
            x.update({'same_objective_next':same,'segment_completes_next':completes,'activity_within_30s':active30,'phase_switch_next':switch})
    return rows,cut,len(ends),episodes

def standardize(tr,te):
    X=np.stack([r['cur'] for r in tr]); mu=X.mean(0); sd=np.where(X.std(0)<1e-5,1,X.std(0))
    toks=[r['seq'][r['mask']>0] for r in tr if r['mask'].sum()>0]; T=np.concatenate(toks,0); tmu=T.mean(0); tsd=np.where(T.std(0)<1e-5,1,T.std(0))
    for z in (tr,te):
        for r in z:
            r['curz']=((r['cur']-mu)/sd).astype(np.float32); r['seqz']=((r['seq']-tmu)/tsd).astype(np.float32)*r['mask'][:,None]
    return mu,sd,tmu,tsd

def perturb(seq,mask,rng):
    s=seq.copy(); m=mask.copy(); valid=np.where(m>0)[0]
    # randomized observation lag / temporary invisibility: hide 0-3 latest strict-past materialized tokens
    if len(valid):
        k=int(rng.integers(0,min(4,len(valid))+1))
        if k: m[valid[-k:]]=0.; s[valid[-k:]]=0.
    # randomized timing/queue-like nuisance on visible strict-past tokens, preserving role/inventory transition semantics
    valid=np.where(m>0)[0]
    if len(valid):
        s[valid,4]=np.clip(s[valid,4]+rng.normal(0,.06,len(valid)), -4,4)
        if rng.random()<.35:
            q=int(rng.choice(valid)); s[q,5]*=float(rng.uniform(.55,1.0))
    return s,m

class EpisodeActor(nn.Module):
    def __init__(self):
        super().__init__(); self.gru=nn.GRU(len(TOK_FEATURES),48,batch_first=True)
        self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),40),nn.ReLU(),nn.LayerNorm(40))
        self.mix=nn.Sequential(nn.Linear(88,72),nn.ReLU(),nn.LayerNorm(72),nn.Linear(72,56),nn.ReLU())
        self.head=nn.ModuleDict({t:nn.Linear(56,1) for t in TASKS})
    def encode(self,cur,seq):
        _,h=self.gru(seq); return self.mix(torch.cat([self.cur(cur),h[-1]],1))
    def forward(self,cur,seq,t): return self.head[t](self.encode(cur,seq)).squeeze(-1)

def arr(rows,t):
    z=[r for r in rows if r[t] is not None]
    return np.stack([r['curz'] for r in z]),np.stack([r['seqz'] for r in z]),np.stack([r['mask'] for r in z]),np.asarray([r[t] for r in z],np.float32)

def metric(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float); pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def train(model,tr,dev):
    model.to(dev); opt=torch.optim.AdamW(model.parameters(),lr=9e-4,weight_decay=1e-4); rng=np.random.default_rng(SEED)
    pools={t:arr(tr,t) for t in TASKS}
    for ep in range(18):
        running=0.
        for step in range(180):
            opt.zero_grad(); loss=0.
            for t in TASKS:
                X,S,M,y=pools[t]; idx=rng.integers(0,len(y),size=min(384,len(y)))
                cx=X[idx]; cs=S[idx]; cm=M[idx]; cy=y[idx]
                ps=[]; pm=[]
                for s,m in zip(cs,cm): a,b=perturb(s,m,rng); ps.append(a); pm.append(b)
                ps=np.stack(ps)
                xt=torch.from_numpy(cx).to(dev); st=torch.from_numpy(cs).to(dev); pt=torch.from_numpy(ps).to(dev); yt=torch.from_numpy(cy).to(dev)
                clean=model(xt,st,t); noisy=model(xt,pt,t)
                pos=max(float(yt.mean()),1e-4); pw=torch.tensor((1-pos)/pos,device=dev).clamp(.3,4)
                sup=nn.functional.binary_cross_entropy_with_logits(clean,yt,pos_weight=pw)
                aug=nn.functional.binary_cross_entropy_with_logits(noisy,yt,pos_weight=pw)
                consistency=nn.functional.mse_loss(torch.sigmoid(noisy),torch.sigmoid(clean).detach())
                loss += sup + .65*aug + .35*consistency
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),5); opt.step(); running+=float(loss.detach().cpu())
        print(json.dumps({'epoch':ep+1,'loss':running/180}),flush=True)
    return model

def evaluate(model,rows,dev,perturbed=False):
    rng=np.random.default_rng(SEED+99); out={}; model.eval()
    with torch.no_grad():
        for t in TASKS:
            X,S,M,y=arr(rows,t); ps=[]
            if perturbed:
                S=np.stack([perturb(s,m,rng)[0] for s,m in zip(S,M)])
            for i in range(0,len(y),4096):
                log=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev),t); ps.append(torch.sigmoid(log).cpu().numpy())
            out[t]=metric(y,np.concatenate(ps))
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output'); ap.add_argument('--model-out'); a=ap.parse_args()
    rows,cut,nwin,episodes=build(a.db); tr=[r for r in rows if r['end']<cut]; te=[r for r in rows if r['end']>=cut]
    mu,sd,tmu,tsd=standardize(tr,te); dev='cuda' if torch.cuda.is_available() else 'cpu'; torch.manual_seed(SEED)
    model=train(EpisodeActor(),tr,dev); clean=evaluate(model,te,dev,False); noisy=evaluate(model,te,dev,True)
    degradation={t:(None if clean[t]['auc'] is None or noisy[t]['auc'] is None else noisy[t]['auc']-clean[t]['auc']) for t in TASKS}
    survival_heads=['same_objective_next','segment_completes_next','activity_within_30s']; auc_ok=sum(clean[t]['auc'] is not None and clean[t]['auc']>=.62 for t in survival_heads)>=2
    robust_ok=sum(degradation[t] is not None and degradation[t]>=-.035 for t in survival_heads)>=2
    participation_ok=clean['activity_within_30s']['auc'] is not None and clean['activity_within_30s']['auc']>=.60
    passed=bool(auc_ok and robust_ok and participation_ok)
    out={'version':'ETH_PERSISTENT_REPAIR_SPECIALIST_V1','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'episodeSegments':episodes,'rows':len(rows),'trainRows':len(tr),'testRows':len(te),'sequenceLength':SEQ,'features':{'current':CUR_FEATURES,'token':TOK_FEATURES},'tasks':TASKS,'clean':clean,'randomPerturbation':noisy,'aucDeltaPerturbedMinusClean':degradation,'representationPass':passed,'passRule':'episode survival/completion heads >=0.62 AUC on >=2/3; perturbation degradation <=0.035 on >=2/3; activity-within-30s AUC >=0.60','trainingObjective':['episode-level responsibility survival/completion, not single next-side imitation','randomized strict-past observation-lag/invisibility and nuisance perturbation consistency','participation head prevents safety-by-permanent-HOLD shortcut'],'boundary':['ETH-only Target actual-filled chronology supplies all policy gradients/labels','BTC contributes architecture hypotheses only, no examples/labels/thresholds/gradients','No winner/future PnL policy feature','No TARGET_UNIT=18 or expected_parent_shares','Deterministic carrier ownership/remaining responsibility stays outside neural encoder','Development representation stage; closed-loop randomized HFT exam still required']}
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.')); op=Path(a.output) if a.output else rd/'result.json'; mp=Path(a.model_out) if a.model_out else rd/'model.pt'; op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'tokenFeatures':TOK_FEATURES,'mu':mu,'sd':sd,'tmu':tmu,'tsd':tsd,'state_dict':model.state_dict(),'representationPass':passed},mp)
    print(json.dumps({'ok':True,'representationPass':passed,'device':dev,'episodes':episodes,'trainRows':len(tr),'testRows':len(te),'clean':clean,'perturbed':noisy,'delta':degradation,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
