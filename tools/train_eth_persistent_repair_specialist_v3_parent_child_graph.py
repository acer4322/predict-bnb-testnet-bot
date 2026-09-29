from __future__ import annotations
import argparse, json, math, os, random, sqlite3
from pathlib import Path
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score

SEED=20260901
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
SEQ=24
CUR_FEATURES=['seconds_left_norm','pair_coverage','absnet_ratio','floor_ratio','best_pnl_ratio','prior_taker_frac','prev_rel','prev_age_log','avg_cost_gap','gross_log']
GRAPH_FEATURES=['parent_age_log','repair_child_frac','expand_child_frac','transition_rate','last_repair_age_log','last_expand_age_log','cum_repair_gap_reduction','cum_repair_pair_gain','cum_repair_floor_gain','cum_expand_pair_pressure','gap_vs_birth','pair_vs_birth','floor_vs_birth','min_absnet_since_birth','max_pair_since_birth','max_floor_since_birth','recent_repair_density','recent_expand_density','coexistence_flag','children_log']
TOK_FEATURES=['rel','delta_absnet','delta_paircov','delta_floor','delta_bestpnl','elapsed_log','qty_gap_ratio','price','post_absnet','post_paircov','parent_age_norm','recent_switch']
CLASS_TASKS=('parent_alive_30s','repair_returns_after_expand_30s','activity_within_30s','parallel_children_within_30s','next_active_child_is_repair')
REG_TASK='time_to_next_repair'


def rel_for(side,up,dn):
    if up+dn<=1e-9 or abs(up-dn)<=1e-9:return 0
    return 1 if ((side=='UP' and up<dn) or (side=='DOWN' and dn<up)) else -1

def state(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t):
    gross=up+dn;pair=min(up,dn);gap=abs(up-dn);pc=2*pair/gross if gross>1e-9 else 1.;ab=gap/gross if gross>1e-9 else 0.
    fr=(pair-cost)/max(cost,1.);br=(max(up,dn)-cost)/max(cost,1.);avu=upcost/up if up>1e-9 else 0.;avd=dncost/dn if dn>1e-9 else 0.
    cur=np.asarray([max(-30,min(330,(end-t)/1000))/300 if end else 0.,pc,ab,max(-5,min(5,fr)),max(-5,min(5,br)),taker_n/max(maker_n+taker_n,1),float(prev_rel),math.log1p(min(prev_age,120000))/math.log1p(120000),max(-1,min(1,avu-avd)),math.log1p(gross)/math.log1p(500)],np.float32)
    return cur,{'gross':gross,'gap':gap,'pc':pc,'ab':ab,'fr':fr,'br':br}

def pad(tokens):
    x=np.zeros((SEQ,len(TOK_FEATURES)),np.float32);m=np.zeros(SEQ,np.float32);h=tokens[-SEQ:]
    if h:x[-len(h):]=np.asarray(h,np.float32);m[-len(h):]=1.
    return x,m

def build(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    ends=sorted(set(mend.values()));cut=ends[int(len(ends)*.70)]
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
    by={}
    for r in raw:by.setdefault(int(r['market_id']),[]).append(r)
    rows=[];parent_births=0
    for mid,evs in by.items():
        end=mend.get(mid);up=dn=cost=upcost=dncost=0.;maker_n=taker_n=0;makers=[];alltok=[];reptok=[];exptok=[]
        parent_birth_t=None;birth_ab=0.;birth_pc=1.;birth_fr=0.;min_ab=1.;max_pc=0.;max_fr=-5.;rep_n=exp_n=trans_n=0;cum_rep_ab=cum_rep_pc=cum_rep_fr=cum_exp_pair=0.;last_rep=None;last_exp=None;last_rel=0
        market_rows=[]
        for r in evs:
            role=str(r['role']);side=str(r['side']);t=int(r['first_event_ms']);sh=float(r['shares']);px=float(r['average_price'])
            prev_rel=makers[-1]['rel'] if makers else 0;prev_age=(t-makers[-1]['time']) if makers else 1e6
            cur,met=state(up,dn,cost,upcost,dncost,maker_n,taker_n,prev_rel,prev_age,end,t);rel=rel_for(side,up,dn) if role=='MAKER' else 0
            if role=='MAKER' and rel!=0:
                # Representation-only economic parent birth: first child, long quiescence, or strict-past near-flat safe reset.
                quiescent=(makers and t-makers[-1]['time']>45000)
                near_flat=(met['gross']>0 and met['ab']<=.02 and met['pc']>=.98)
                if parent_birth_t is None or quiescent or near_flat:
                    parent_birth_t=t;birth_ab=met['ab'];birth_pc=met['pc'];birth_fr=met['fr'];min_ab=met['ab'];max_pc=met['pc'];max_fr=met['fr'];rep_n=exp_n=trans_n=0;cum_rep_ab=cum_rep_pc=cum_rep_fr=cum_exp_pair=0.;last_rep=last_exp=None;last_rel=0;parent_births+=1
                recent=[m for m in makers if t-m['time']<=30000]
                rr=sum(m['rel']==1 for m in recent);ee=sum(m['rel']==-1 for m in recent);nchild=rep_n+exp_n
                age=max(0,t-parent_birth_t);lra=1e6 if last_rep is None else t-last_rep;lea=1e6 if last_exp is None else t-last_exp
                graph=np.asarray([
                    math.log1p(min(age,300000))/math.log1p(300000),rep_n/max(nchild,1),exp_n/max(nchild,1),trans_n/max(nchild,1),
                    math.log1p(min(lra,120000))/math.log1p(120000),math.log1p(min(lea,120000))/math.log1p(120000),math.tanh(cum_rep_ab/2),math.tanh(cum_rep_pc/2),math.tanh(cum_rep_fr/4),math.tanh(cum_exp_pair/2),
                    max(-1,min(1,birth_ab-met['ab'])),max(-1,min(1,met['pc']-birth_pc)),max(-2,min(2,met['fr']-birth_fr)),min_ab,max_pc,max(-5,min(5,max_fr)),rr/max(len(recent),1),ee/max(len(recent),1),float(rr>0 and ee>0),math.log1p(nchild)/math.log1p(128)
                ],np.float32)
                a,am=pad(alltok);rp,rpm=pad(reptok);ep,epm=pad(exptok)
                market_rows.append({'market':mid,'end':end,'t':t,'rel':rel,'cur':cur,'graph':graph,'allseq':a,'allmask':am,'repseq':rp,'repmask':rpm,'expseq':ep,'expmask':epm,'past_expand_recent':int(last_exp is not None and t-last_exp<=30000)})
            pre=met.copy();prev_time=makers[-1]['time'] if makers else None
            if side=='UP':up+=sh;upcost+=sh*px
            else:dn+=sh;dncost+=sh*px
            cost+=sh*px
            if role=='MAKER' and rel!=0:
                _,post=state(up,dn,cost,upcost,dncost,maker_n+1,taker_n,rel,1,end,t);elapsed=1e6 if prev_time is None else t-prev_time;qgr=sh/max(pre['gap'],1.)
                parent_age=0. if parent_birth_t is None else min(1.,max(0.,(t-parent_birth_t)/300000.));recent_switch=float(last_rel!=0 and rel!=last_rel)
                tok=np.asarray([float(rel),post['ab']-pre['ab'],post['pc']-pre['pc'],max(-5,min(5,post['fr']-pre['fr'])),max(-5,min(5,post['br']-pre['br'])),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,qgr)),px,post['ab'],post['pc'],parent_age,recent_switch],np.float32)
                alltok.append(tok)
                if rel==1:reptok.append(tok);rep_n+=1;cum_rep_ab+=max(0.,pre['ab']-post['ab']);cum_rep_pc+=max(0.,post['pc']-pre['pc']);cum_rep_fr+=max(0.,post['fr']-pre['fr']);last_rep=t
                else:exptok.append(tok);exp_n+=1;cum_exp_pair+=max(0.,pre['pc']-post['pc']);last_exp=t
                if last_rel!=0 and rel!=last_rel:trans_n+=1
                last_rel=rel;min_ab=min(min_ab,post['ab']);max_pc=max(max_pc,post['pc']);max_fr=max(max_fr,post['fr']);makers.append({'rel':rel,'time':t});maker_n+=1
            elif role=='TAKER':taker_n+=1
        # Future labels are supervision only; no future fields enter features.
        for j,x in enumerate(market_rows):
            fut=[y for y in market_rows[j+1:] if y['t']-x['t']<=30000]
            nxt=market_rows[j+1] if j+1<len(market_rows) else None
            reps=[y for y in fut if y['rel']==1];exps=[y for y in fut if y['rel']==-1]
            x['parent_alive_30s']=int(len(reps)>0)
            x['repair_returns_after_expand_30s']=None if not (x['rel']==-1 or x['past_expand_recent']) else int(len(reps)>0)
            x['activity_within_30s']=int(len(fut)>0)
            x['parallel_children_within_30s']=int(len(reps)>0 and len(exps)>0)
            x['next_active_child_is_repair']=None if nxt is None else int(nxt['rel']==1)
            nr=next((y for y in market_rows[j+1:] if y['rel']==1 and y['t']-x['t']<=120000),None)
            x['time_to_next_repair']=None if nr is None else math.log1p(max(0,nr['t']-x['t']))/math.log1p(120000)
        rows.extend(market_rows)
    return rows,cut,len(ends),parent_births

def standardize(tr,te):
    X=np.stack([r['cur'] for r in tr]);mu=X.mean(0);sd=np.where(X.std(0)<1e-5,1,X.std(0));G=np.stack([r['graph'] for r in tr]);gmu=G.mean(0);gsd=np.where(G.std(0)<1e-5,1,G.std(0))
    toks=[]
    for r in tr:
        if r['allmask'].sum()>0:toks.append(r['allseq'][r['allmask']>0])
    T=np.concatenate(toks,0);tmu=T.mean(0);tsd=np.where(T.std(0)<1e-5,1,T.std(0))
    for z in (tr,te):
        for r in z:
            r['curz']=((r['cur']-mu)/sd).astype(np.float32);r['graphz']=((r['graph']-gmu)/gsd).astype(np.float32)
            for k,mk in [('allseq','allmask'),('repseq','repmask'),('expseq','expmask')]:r[k+'z']=((r[k]-tmu)/tsd).astype(np.float32)*r[mk][:,None]
    return mu,sd,gmu,gsd,tmu,tsd

def perturb(seq,mask,rng,side_drop=False):
    s=seq.copy();m=mask.copy();valid=np.where(m>0)[0]
    if len(valid):
        k=int(rng.integers(0,min(4,len(valid))+1))
        if k:m[valid[-k:]]=0.;s[valid[-k:]]=0.
    valid=np.where(m>0)[0]
    if len(valid) and side_drop and rng.random()<.20:
        k=max(1,int(math.ceil(len(valid)*rng.uniform(.15,.45))));pick=rng.choice(valid,size=min(k,len(valid)),replace=False);m[pick]=0.;s[pick]=0.
    valid=np.where(m>0)[0]
    if len(valid):
        s[valid,5]=np.clip(s[valid,5]+rng.normal(0,.05,len(valid)),-4,4)
        if rng.random()<.35:
            q=int(rng.choice(valid));s[q,6]*=float(rng.uniform(.55,1.0))
    return s*m[:,None],m

def rows_arr(rows,task):
    z=[r for r in rows if r.get(task) is not None]
    return z,np.stack([r['curz'] for r in z]),np.stack([r['graphz'] for r in z]),np.stack([r['allseqz'] for r in z]),np.stack([r['allmask'] for r in z]),np.stack([r['repseqz'] for r in z]),np.stack([r['repmask'] for r in z]),np.stack([r['expseqz'] for r in z]),np.stack([r['expmask'] for r in z]),np.asarray([r[task] for r in z],np.float32)

class Baseline(nn.Module):
    def __init__(self):super().__init__();self.body=nn.Sequential(nn.Linear(len(CUR_FEATURES),48),nn.ReLU(),nn.LayerNorm(48),nn.Linear(48,40),nn.ReLU());self.h=nn.ModuleDict({t:nn.Linear(40,1) for t in CLASS_TASKS});self.reg=nn.Linear(40,1)
    def forward(self,c,t):z=self.body(c);return self.reg(z).squeeze(-1) if t==REG_TASK else self.h[t](z).squeeze(-1)

class ParentChildGraph(nn.Module):
    def __init__(self):
        super().__init__();self.allgru=nn.GRU(len(TOK_FEATURES),32,batch_first=True);self.repgru=nn.GRU(len(TOK_FEATURES),28,batch_first=True);self.expgru=nn.GRU(len(TOK_FEATURES),28,batch_first=True)
        self.cur=nn.Sequential(nn.Linear(len(CUR_FEATURES),32),nn.ReLU(),nn.LayerNorm(32));self.graph=nn.Sequential(nn.Linear(len(GRAPH_FEATURES),40),nn.ReLU(),nn.LayerNorm(40))
        self.mix=nn.Sequential(nn.Linear(160,112),nn.ReLU(),nn.LayerNorm(112),nn.Linear(112,72),nn.ReLU());self.h=nn.ModuleDict({t:nn.Linear(72,1) for t in CLASS_TASKS});self.reg=nn.Linear(72,1)
    def encode(self,c,g,a,r,e):
        _,ha=self.allgru(a);_,hr=self.repgru(r);_,he=self.expgru(e);return self.mix(torch.cat([self.cur(c),self.graph(g),ha[-1],hr[-1],he[-1]],1))
    def forward(self,c,g,a,r,e,t):z=self.encode(c,g,a,r,e);return self.reg(z).squeeze(-1) if t==REG_TASK else self.h[t](z).squeeze(-1)

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def train_baseline(m,tr,dev):
    m.to(dev);opt=torch.optim.AdamW(m.parameters(),lr=9e-4,weight_decay=1e-4);rng=np.random.default_rng(SEED);p={t:rows_arr(tr,t) for t in CLASS_TASKS+(REG_TASK,)}
    for ep in range(12):
        run=0.
        for _ in range(150):
            opt.zero_grad();L=0.
            for t in CLASS_TASKS:
                z,X,G,A,AM,R,RM,E,EM,y=p[t];idx=rng.integers(0,len(y),size=min(384,len(y)));xt=torch.from_numpy(X[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev);log=m(xt,t);pos=max(float(yt.mean()),1e-4);L+=nn.functional.binary_cross_entropy_with_logits(log,yt,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.3,4))
            z,X,G,A,AM,R,RM,E,EM,y=p[REG_TASK];idx=rng.integers(0,len(y),size=min(384,len(y)));pred=m(torch.from_numpy(X[idx]).to(dev),REG_TASK);L+=.35*nn.functional.smooth_l1_loss(pred,torch.from_numpy(y[idx]).to(dev));L.backward();nn.utils.clip_grad_norm_(m.parameters(),5);opt.step();run+=float(L.detach().cpu())
        print(json.dumps({'model':'BASELINE','epoch':ep+1,'loss':run/150}),flush=True)
    return m

def train_specialist(m,tr,dev):
    m.to(dev);opt=torch.optim.AdamW(m.parameters(),lr=8e-4,weight_decay=1e-4);rng=np.random.default_rng(SEED+1);p={t:rows_arr(tr,t) for t in CLASS_TASKS+(REG_TASK,)}
    for ep in range(20):
        run=0.
        for _ in range(180):
            opt.zero_grad();L=0.
            for t in CLASS_TASKS:
                z,X,G,A,AM,R,RM,E,EM,y=p[t];idx=rng.integers(0,len(y),size=min(384,len(y)));cx=X[idx];cg=G[idx];ca=A[idx];cr=R[idx];ce=E[idx];cam=AM[idx];crm=RM[idx];cem=EM[idx];cy=y[idx]
                na=[];nr=[];ne=[]
                for a,am,r,rm,e,em in zip(ca,cam,cr,crm,ce,cem):
                    na.append(perturb(a,am,rng,True)[0]);nr.append(perturb(r,rm,rng,True)[0]);ne.append(perturb(e,em,rng,True)[0])
                xt=torch.from_numpy(cx).to(dev);gt=torch.from_numpy(cg).to(dev);at=torch.from_numpy(ca).to(dev);rt=torch.from_numpy(cr).to(dev);et=torch.from_numpy(ce).to(dev);nat=torch.from_numpy(np.stack(na)).to(dev);nrt=torch.from_numpy(np.stack(nr)).to(dev);net=torch.from_numpy(np.stack(ne)).to(dev);yt=torch.from_numpy(cy).to(dev)
                clean=m(xt,gt,at,rt,et,t);noisy=m(xt,gt,nat,nrt,net,t);pos=max(float(yt.mean()),1e-4);pw=torch.tensor((1-pos)/pos,device=dev).clamp(.3,4);sup=nn.functional.binary_cross_entropy_with_logits(clean,yt,pos_weight=pw);aug=nn.functional.binary_cross_entropy_with_logits(noisy,yt,pos_weight=pw);con=nn.functional.mse_loss(torch.sigmoid(noisy),torch.sigmoid(clean).detach());L+=sup+.55*aug+.30*con
            z,X,G,A,AM,R,RM,E,EM,y=p[REG_TASK];idx=rng.integers(0,len(y),size=min(384,len(y)));pred=m(torch.from_numpy(X[idx]).to(dev),torch.from_numpy(G[idx]).to(dev),torch.from_numpy(A[idx]).to(dev),torch.from_numpy(R[idx]).to(dev),torch.from_numpy(E[idx]).to(dev),REG_TASK);L+=.30*nn.functional.smooth_l1_loss(pred,torch.from_numpy(y[idx]).to(dev));L.backward();nn.utils.clip_grad_norm_(m.parameters(),5);opt.step();run+=float(L.detach().cpu())
        print(json.dumps({'model':'SPECIALIST','epoch':ep+1,'loss':run/180}),flush=True)
    return m

def eval_model(m,rows,dev,specialist=False,noisy=False):
    rng=np.random.default_rng(SEED+99);out={};m.eval()
    with torch.no_grad():
        for t in CLASS_TASKS:
            z,X,G,A,AM,R,RM,E,EM,y=rows_arr(rows,t);ps=[]
            if noisy and specialist:
                A=np.stack([perturb(a,am,rng,True)[0] for a,am in zip(A,AM)]);R=np.stack([perturb(r,rm,rng,True)[0] for r,rm in zip(R,RM)]);E=np.stack([perturb(e,em,rng,True)[0] for e,em in zip(E,EM)])
            for i in range(0,len(y),4096):
                if specialist:log=m(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev),t)
                else:log=m(torch.from_numpy(X[i:i+4096]).to(dev),t)
                ps.append(torch.sigmoid(log).cpu().numpy())
            out[t]=metric(y,np.concatenate(ps))
        z,X,G,A,AM,R,RM,E,EM,y=rows_arr(rows,REG_TASK);ps=[]
        for i in range(0,len(y),4096):
            if specialist:p=m(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev),REG_TASK)
            else:p=m(torch.from_numpy(X[i:i+4096]).to(dev),REG_TASK)
            ps.append(p.cpu().numpy())
        q=np.concatenate(ps);out[REG_TASK]={'n':int(len(y)),'mae':float(np.mean(np.abs(q-y))),'rmse':float(np.sqrt(np.mean((q-y)**2)))}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args();rows,cut,nwin,births=build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];stats=standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
    torch.manual_seed(SEED);base=train_baseline(Baseline(),tr,dev);bm=eval_model(base,te,dev,False,False)
    torch.manual_seed(SEED);spec=train_specialist(ParentChildGraph(),tr,dev);sm=eval_model(spec,te,dev,True,False);nm=eval_model(spec,te,dev,True,True)
    delta={t:sm[t]['auc']-bm[t]['auc'] for t in CLASS_TASKS};rob={t:nm[t]['auc']-sm[t]['auc'] for t in CLASS_TASKS};core=['parent_alive_30s','repair_returns_after_expand_30s','activity_within_30s','parallel_children_within_30s'];mean_core=float(np.mean([delta[t] for t in core]));passv=bool(sm['repair_returns_after_expand_30s']['auc']>=.57 and delta['repair_returns_after_expand_30s']>=.02 and sm['parent_alive_30s']['auc']>=.62 and sm['activity_within_30s']['auc']>=.68 and sm['parallel_children_within_30s']['auc']>=.60 and sum(rob[t]>=-.035 for t in CLASS_TASKS)>=4 and mean_core>=.005)
    out={'version':'ETH_PERSISTENT_REPAIR_SPECIALIST_V3_PARENT_CHILD_GRAPH','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'economicParentBirths':births,'rows':len(rows),'trainRows':len(tr),'testRows':len(te),'sequenceLength':SEQ,'features':{'current':CUR_FEATURES,'graph':GRAPH_FEATURES,'token':TOK_FEATURES},'tasks':list(CLASS_TASKS)+( [REG_TASK]),'baselineCurrentTopology':bm,'specialistClean':sm,'specialistRandomPerturbation':nm,'aucDeltaSpecialistMinusBaseline':delta,'aucDeltaPerturbedMinusClean':rob,'meanCoreAucDeltaSpecialistMinusBaseline':mean_core,'representationPass':passv,'passRule':'repairReturnsAfterExpand>=0.57 and lift>=0.02; parentAlive>=0.62; activity>=0.68; parallelChildren>=0.60; >=4/5 classification perturbation drops <=0.035; mean core lift>=0.005','trainingObjective':['higher-level economic parent with repair/expand child coexistence','current structured topology + separate repair/expand/all-child trajectory encoders','random strict-past disturbance consistency','anti-passivity activity supervision'],'boundary':['ETH-only Target actual-filled chronology labels/gradients','BTC architecture hypotheses only; no BTC policy gradients','No winner/future PnL features','No TARGET_UNIT=18 or expected_parent_shares','No dream fill','Deterministic execution/carrier ownership remains outside neural encoder','Development representation only']}
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.pt';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');mu,sd,gmu,gsd,tmu,tsd=stats;torch.save({'version':out['version'],'currentFeatures':CUR_FEATURES,'graphFeatures':GRAPH_FEATURES,'tokenFeatures':TOK_FEATURES,'mu':mu,'sd':sd,'gmu':gmu,'gsd':gsd,'tmu':tmu,'tsd':tsd,'state_dict':spec.state_dict(),'representationPass':passv},mp);print(json.dumps({'ok':True,'representationPass':passv,'device':dev,'births':births,'trainRows':len(tr),'testRows':len(te),'baseline':bm,'specialist':sm,'perturbed':nm,'delta':delta,'robustness':rob,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
