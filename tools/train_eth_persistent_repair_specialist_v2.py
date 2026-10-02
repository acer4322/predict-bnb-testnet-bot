from __future__ import annotations
import argparse, json, math, os, random, sqlite3
from pathlib import Path
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

SEED=20260901
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
SEQ=24
PARENT_QUIESCENCE_MS=30000
CLASS_TASKS=('repair_alive_30s','repair_returns_after_expand_30s','activity_within_30s','phase_switch_next')
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','prev_rel','prev_age_log','avg_cost_gap','gross_log']
PARENT_FEATURES=['parent_active','parent_age_log','last_repair_age_log','last_expand_age_log','repair_child_frac','expand_child_frac','transition_rate','cum_repair_gap_reduction','cum_repair_pair_gain','cum_repair_floor_gain','gap_vs_parent_birth','pair_vs_parent_birth','floor_vs_parent_birth','min_absnet_since_birth','recent_repair_density']
TOK_FEATURES=['rel','delta_absnet','delta_paircov','delta_floor','elapsed_log','qty_gap_ratio','price','post_absnet','post_paircov','parent_active','parent_age','parent_gap_progress','repair_density']


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


def logage(ms,cap=180000):
    if ms is None:return 1.0
    return math.log1p(min(max(ms,0),cap))/math.log1p(cap)


def build(db):
    c=sqlite3.connect(db); c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    ends=sorted(set(mend.values())); cut=ends[int(len(ends)*.70)]
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id")); c.close()
    by_market={}
    for r in raw:by_market.setdefault(int(r['market_id']),[]).append(r)
    rows=[]; semantic_parent_births=0
    for mid,evs in by_market.items():
        end=mend.get(mid); up=dn=cost=upcost=dncost=0.; maker_n=taker_n=0; hist=[]; maker_rows=[]
        parent_active=False; parent_birth_t=None; parent_birth_gap=0.; parent_birth_pc=1.; parent_birth_fr=0.; parent_min_ab=1.
        parent_rep=parent_exp=parent_trans=0; cum_rep_gap=cum_rep_pc=cum_rep_fr=0.; last_repair_t=None; last_expand_t=None; parent_prev_rel=0
        recent_rels=[]
        for r in evs:
            role=str(r['role']); side=str(r['side']); t=int(r['first_event_ms']); sh=float(r['shares']); px=float(r['average_price'])
            # Strict-past semantic parent timeout. This is representation bookkeeping, not an execution/action delay.
            if parent_active and last_repair_t is not None and t-last_repair_t>PARENT_QUIESCENCE_MS:
                parent_active=False; parent_birth_t=None; parent_rep=parent_exp=parent_trans=0; cum_rep_gap=cum_rep_pc=cum_rep_fr=0.; parent_prev_rel=0
            prev_rel=maker_rows[-1]['rel'] if maker_rows else 0; prev_age=(t-maker_rows[-1]['t']) if maker_rows else 1e6
            cur,met=state(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t)
            rel=rel_for(side,up,dn) if role=='MAKER' else 0
            # New semantic repair-parent begins only when a repair child materializes after prior quiescence.
            if role=='MAKER' and rel==1 and not parent_active:
                parent_active=True; semantic_parent_births+=1; parent_birth_t=t; parent_birth_gap=met['gap']; parent_birth_pc=met['pc']; parent_birth_fr=met['fr']; parent_min_ab=met['ab']
                parent_rep=parent_exp=parent_trans=0; cum_rep_gap=cum_rep_pc=cum_rep_fr=0.; parent_prev_rel=0
            if role=='MAKER' and rel!=0:
                total_child=max(parent_rep+parent_exp,1)
                recent_density=sum(1 for z in recent_rels[-8:] if z==1)/max(len(recent_rels[-8:]),1)
                pf=np.asarray([
                    1. if parent_active else 0.,
                    logage(t-parent_birth_t if parent_birth_t is not None else None),
                    logage(t-last_repair_t if last_repair_t is not None else None),
                    logage(t-last_expand_t if last_expand_t is not None else None),
                    parent_rep/total_child if parent_active else 0.,
                    parent_exp/total_child if parent_active else 0.,
                    parent_trans/max(total_child-1,1) if parent_active else 0.,
                    math.tanh(cum_rep_gap/2), math.tanh(cum_rep_pc/2), math.tanh(cum_rep_fr/4),
                    max(-2,min(2,(met['gap']-parent_birth_gap)/max(parent_birth_gap,1.))) if parent_active else 0.,
                    max(-1,min(1,met['pc']-parent_birth_pc)) if parent_active else 0.,
                    max(-3,min(3,met['fr']-parent_birth_fr)) if parent_active else 0.,
                    parent_min_ab if parent_active else met['ab'],
                    recent_density
                ],np.float32)
                seq=np.zeros((SEQ,len(TOK_FEATURES)),np.float32); mask=np.zeros(SEQ,np.float32); hh=hist[-SEQ:]
                if hh:seq[-len(hh):]=np.asarray(hh,np.float32); mask[-len(hh):]=1.
                row={'market':mid,'end':end,'t':t,'rel':rel,'cur':cur,'parent':pf,'seq':seq,'mask':mask,'parentActive':bool(parent_active)}
                rows.append(row); maker_rows.append(row)
            pre_ab,pre_pc,pre_fr,pre_gap=met['ab'],met['pc'],met['fr'],met['gap']
            if side=='UP':up+=sh;upcost+=sh*px
            else:dn+=sh;dncost+=sh*px
            cost+=sh*px
            if role=='MAKER' and rel!=0:
                _,post=state(up,dn,cost,upcost,dncost,maker_n+1,taker_n,rel,1,end,t)
                elapsed=(t-maker_rows[-2]['t']) if len(maker_rows)>=2 else 1e6; qgr=sh/max(pre_gap,1.)
                if parent_active:
                    parent_min_ab=min(parent_min_ab,post['ab'])
                    if parent_prev_rel and parent_prev_rel!=rel:parent_trans+=1
                    if rel==1:
                        parent_rep+=1; last_repair_t=t; cum_rep_gap+=max(0.,pre_ab-post['ab']); cum_rep_pc+=max(0.,post['pc']-pre_pc); cum_rep_fr+=max(0.,post['fr']-pre_fr)
                    else:
                        parent_exp+=1; last_expand_t=t
                    parent_prev_rel=rel
                recent_rels.append(rel)
                total_child=max(parent_rep+parent_exp,1); repair_density=sum(1 for z in recent_rels[-8:] if z==1)/max(len(recent_rels[-8:]),1)
                tok=np.asarray([float(rel),post['ab']-pre_ab,post['pc']-pre_pc,max(-5,min(5,post['fr']-pre_fr)),logage(elapsed,120000),max(0,min(3,qgr)),px,post['ab'],post['pc'],1. if parent_active else 0.,logage(t-parent_birth_t if parent_birth_t is not None else None),max(-2,min(2,(post['gap']-parent_birth_gap)/max(parent_birth_gap,1.))) if parent_active else 0.,repair_density],np.float32)
                hist.append(tok); maker_n+=1
            elif role=='TAKER':taker_n+=1
        # Future labels are supervision only. Runtime features remain strict-past.
        rr=maker_rows
        for j,x in enumerate(rr):
            future=[]
            for y in rr[j+1:]:
                dt=y['t']-x['t']
                if dt>60000:break
                future.append((dt,y))
            f30=[(dt,y) for dt,y in future if dt<=30000]
            nextrow=rr[j+1] if j+1<len(rr) else None
            repairs=[(dt,y) for dt,y in f30 if y['rel']==1]
            activity=int(bool(f30))
            repair_alive=int(bool(repairs))
            # Defined only when a semantic repair parent is active at the current child.
            return_after=None
            if x['parentActive']:
                first_repair_i=None
                for ii,(dt,y) in enumerate(f30):
                    if y['rel']==1:first_repair_i=ii;break
                return_after=int(first_repair_i is not None and any(y['rel']==-1 for _,y in f30[:first_repair_i]))
            switch=int(nextrow is not None and nextrow['rel']!=x['rel'])
            ttn=None
            for dt,y in future:
                if y['rel']==1:ttn=math.log1p(dt)/math.log1p(60000);break
            x.update({'repair_alive_30s':repair_alive,'repair_returns_after_expand_30s':return_after,'activity_within_30s':activity,'phase_switch_next':switch,'time_to_next_repair':ttn})
    return rows,cut,len(ends),semantic_parent_births


def standardize(tr,te):
    X=np.stack([r['cur'] for r in tr]);mu=X.mean(0);sd=np.where(X.std(0)<1e-5,1,X.std(0))
    P=np.stack([r['parent'] for r in tr]);pmu=P.mean(0);psd=np.where(P.std(0)<1e-5,1,P.std(0))
    toks=[r['seq'][r['mask']>0] for r in tr if r['mask'].sum()>0];T=np.concatenate(toks,0);tmu=T.mean(0);tsd=np.where(T.std(0)<1e-5,1,T.std(0))
    for z in (tr,te):
        for r in z:
            r['curz']=((r['cur']-mu)/sd).astype(np.float32);r['parentz']=((r['parent']-pmu)/psd).astype(np.float32);r['seqz']=((r['seq']-tmu)/tsd).astype(np.float32)*r['mask'][:,None]
    return mu,sd,pmu,psd,tmu,tsd


def perturb(seq,mask,rng):
    s=seq.copy();m=mask.copy();valid=np.where(m>0)[0]
    if len(valid):
        k=int(rng.integers(0,min(4,len(valid))+1))
        if k:m[valid[-k:]]=0.;s[valid[-k:]]=0.
    valid=np.where(m>0)[0]
    if len(valid):
        # Temporary invisibility of one strict-past materialized child.
        if len(valid)>2 and rng.random()<.30:
            q=int(rng.choice(valid));m[q]=0.;s[q]=0.
        valid=np.where(m>0)[0]
        s[valid,4]=np.clip(s[valid,4]+rng.normal(0,.05,len(valid)),-4,4)
        if rng.random()<.45:
            q=int(rng.choice(valid));s[q,5]*=float(rng.uniform(.45,1.0))
    return s,m


class TopologyBaseline(nn.Module):
    def __init__(self):
        super().__init__();d=len(CUR_FEATURES)+len(PARENT_FEATURES)
        self.top=nn.Sequential(nn.Linear(d,80),nn.ReLU(),nn.LayerNorm(80),nn.Linear(80,64),nn.ReLU())
        self.head=nn.ModuleDict({t:nn.Linear(64,1) for t in CLASS_TASKS});self.reg=nn.Linear(64,1)
    def encode_top(self,cur,parent):return self.top(torch.cat([cur,parent],1))
    def forward(self,cur,parent,seq,t):return self.head[t](self.encode_top(cur,parent)).squeeze(-1)
    def regress(self,cur,parent,seq):return torch.sigmoid(self.reg(self.encode_top(cur,parent)).squeeze(-1))


class FactorizedRepairActor(nn.Module):
    def __init__(self):
        super().__init__();d=len(CUR_FEATURES)+len(PARENT_FEATURES)
        self.top=nn.Sequential(nn.Linear(d,80),nn.ReLU(),nn.LayerNorm(80),nn.Linear(80,64),nn.ReLU())
        self.gru=nn.GRU(len(TOK_FEATURES),40,batch_first=True)
        self.hist=nn.Sequential(nn.Linear(40,40),nn.ReLU(),nn.LayerNorm(40))
        self.fuse=nn.Sequential(nn.Linear(104,80),nn.ReLU(),nn.LayerNorm(80),nn.Linear(80,64),nn.ReLU())
        self.top_heads=nn.ModuleDict({t:nn.Linear(64,1) for t in ('repair_alive_30s','phase_switch_next')})
        self.fused_heads=nn.ModuleDict({t:nn.Linear(64,1) for t in ('repair_returns_after_expand_30s','activity_within_30s')})
        self.reg=nn.Linear(64,1)
    def encode(self,cur,parent,seq):
        top=self.top(torch.cat([cur,parent],1));_,h=self.gru(seq);hist=self.hist(h[-1]);fused=self.fuse(torch.cat([top,hist],1));return top,fused
    def forward(self,cur,parent,seq,t):
        top,fused=self.encode(cur,parent,seq)
        return (self.top_heads[t](top) if t in self.top_heads else self.fused_heads[t](fused)).squeeze(-1)
    def regress(self,cur,parent,seq):
        _,fused=self.encode(cur,parent,seq);return torch.sigmoid(self.reg(fused).squeeze(-1))


def arr(rows,t):
    z=[r for r in rows if r[t] is not None]
    return z,np.stack([r['curz'] for r in z]),np.stack([r['parentz'] for r in z]),np.stack([r['seqz'] for r in z]),np.stack([r['mask'] for r in z]),np.asarray([r[t] for r in z],np.float32)


def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}


def train(model,tr,dev,specialist):
    model.to(dev);opt=torch.optim.AdamW(model.parameters(),lr=8e-4,weight_decay=1e-4);rng=np.random.default_rng(SEED+(17 if specialist else 3))
    pools={t:arr(tr,t)[1:] for t in CLASS_TASKS};reg=arr(tr,'time_to_next_repair')[1:]
    epochs=18 if specialist else 12;steps=170 if specialist else 120
    for ep in range(epochs):
        running=0.
        for _ in range(steps):
            opt.zero_grad();loss=0.
            for t in CLASS_TASKS:
                X,P,S,M,y=pools[t];idx=rng.integers(0,len(y),size=min(384,len(y)));cx=X[idx];cp=P[idx];cs=S[idx];cm=M[idx];cy=y[idx]
                xt=torch.from_numpy(cx).to(dev);ptop=torch.from_numpy(cp).to(dev);st=torch.from_numpy(cs).to(dev);yt=torch.from_numpy(cy).to(dev)
                clean=model(xt,ptop,st,t);pos=max(float(yt.mean()),1e-4);pw=torch.tensor((1-pos)/pos,device=dev).clamp(.3,5)
                sup=nn.functional.binary_cross_entropy_with_logits(clean,yt,pos_weight=pw)
                if specialist:
                    noisy=np.stack([perturb(s,m,rng)[0] for s,m in zip(cs,cm)]);nt=torch.from_numpy(noisy).to(dev);nlog=model(xt,ptop,nt,t)
                    aug=nn.functional.binary_cross_entropy_with_logits(nlog,yt,pos_weight=pw);cons=nn.functional.mse_loss(torch.sigmoid(nlog),torch.sigmoid(clean).detach())
                    # Topology heads are intentionally current-state-owned; their noisy path is still checked but not allowed to dominate.
                    loss+=sup+(.55*aug+.25*cons if t in ('repair_returns_after_expand_30s','activity_within_30s') else .15*aug+.10*cons)
                else:loss+=sup
            # Auxiliary time-to-next-repair regression.
            X,P,S,M,y=reg;idx=rng.integers(0,len(y),size=min(384,len(y)));xt=torch.from_numpy(X[idx]).to(dev);pp=torch.from_numpy(P[idx]).to(dev);st=torch.from_numpy(S[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev)
            pred=model.regress(xt,pp,st);loss+=.35*nn.functional.smooth_l1_loss(pred,yt)
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),5);opt.step();running+=float(loss.detach().cpu())
        print(json.dumps({'model':'SPECIALIST' if specialist else 'BASELINE','epoch':ep+1,'loss':running/steps}),flush=True)
    return model


def evaluate(model,rows,dev,perturbed=False):
    rng=np.random.default_rng(SEED+99);out={};model.eval()
    with torch.no_grad():
        for t in CLASS_TASKS:
            z,X,P,S,M,y=arr(rows,t)
            if perturbed:S=np.stack([perturb(s,m,rng)[0] for s,m in zip(S,M)])
            ps=[]
            for i in range(0,len(y),4096):
                log=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(P[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev),t);ps.append(torch.sigmoid(log).cpu().numpy())
            out[t]=metric(y,np.concatenate(ps))
        z,X,P,S,M,y=arr(rows,'time_to_next_repair')
        if perturbed:S=np.stack([perturb(s,m,rng)[0] for s,m in zip(S,M)])
        pr=[]
        for i in range(0,len(y),4096):pr.append(model.regress(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(P[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev)).cpu().numpy())
        pp=np.concatenate(pr);out['time_to_next_repair']={'n':int(len(y)),'mae':float(np.mean(np.abs(pp-y))),'rmse':float(np.sqrt(np.mean((pp-y)**2)))}
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
    rows,cut,nwin,parent_births=build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut]
    mu,sd,pmu,psd,tmu,tsd=standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(SEED);base=train(TopologyBaseline(),tr,dev,False);bclean=evaluate(base,te,dev,False)
    torch.manual_seed(SEED);spec=train(FactorizedRepairActor(),tr,dev,True);clean=evaluate(spec,te,dev,False);noisy=evaluate(spec,te,dev,True)
    degradation={t:noisy[t]['auc']-clean[t]['auc'] for t in CLASS_TASKS}
    delta_vs_base={t:clean[t]['auc']-bclean[t]['auc'] for t in CLASS_TASKS}
    core=('repair_alive_30s','repair_returns_after_expand_30s','activity_within_30s')
    mean_core_delta=float(np.mean([delta_vs_base[t] for t in core]))
    robust=sum(degradation[t]>=-.035 for t in CLASS_TASKS)>=3
    passed=bool(clean['repair_alive_30s']['auc']>=.62 and clean['repair_returns_after_expand_30s']['auc']>=.60 and clean['activity_within_30s']['auc']>=.68 and robust and mean_core_delta>=-.005)
    out={'version':'ETH_PERSISTENT_REPAIR_SPECIALIST_V2','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'semanticRepairParentBirths':parent_births,'rows':len(rows),'trainRows':len(tr),'testRows':len(te),'sequenceLength':SEQ,'parentQuiescenceMsRepresentationOnly':PARENT_QUIESCENCE_MS,'features':{'current':CUR_FEATURES,'parentProgress':PARENT_FEATURES,'token':TOK_FEATURES},'tasks':list(CLASS_TASKS)+['time_to_next_repair'],'baselineCurrentTopology':bclean,'specialistClean':clean,'specialistRandomPerturbation':noisy,'aucDeltaPerturbedMinusClean':degradation,'aucDeltaSpecialistMinusBaseline':delta_vs_base,'meanCoreAucDeltaSpecialistMinusBaseline':mean_core_delta,'representationPass':passed,'passRule':'repair_alive>=0.62; repair_returns_after_expand>=0.60; activity>=0.68; >=3/4 classification heads perturbation degradation >=-0.035; mean core AUC specialist not >0.005 below current-state baseline','trainingObjective':['persistent semantic repair-parent progress survives intervening EXPAND children','current structured topology owns generic objective context; sequence specializes trajectory/urgency','randomized strict-past disturbance consistency','anti-passivity activity head'],'boundary':['ETH-only Target actual-filled chronology supplies labels/gradients','BTC architecture evidence only; no BTC policy examples/gradients','No winner/future PnL feature','No TARGET_UNIT=18 or expected_parent_shares','Deterministic carrier ownership/remaining responsibility stays outside neural encoder','30s quiescence is representation bookkeeping only, not runtime execution delay or action threshold','Development representation stage only']}
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.pt';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'parentFeatures':PARENT_FEATURES,'tokenFeatures':TOK_FEATURES,'mu':mu,'sd':sd,'pmu':pmu,'psd':psd,'tmu':tmu,'tsd':tsd,'state_dict':spec.state_dict(),'representationPass':passed},mp)
    print(json.dumps({'ok':True,'representationPass':passed,'device':dev,'rows':len(rows),'parentBirths':parent_births,'baseline':bclean,'clean':clean,'perturbed':noisy,'deltaPerturb':degradation,'deltaVsBaseline':delta_vs_base,'meanCoreDelta':mean_core_delta},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
