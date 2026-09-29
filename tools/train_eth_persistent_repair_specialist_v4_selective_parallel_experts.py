from __future__ import annotations
import argparse,json,math,os,sys,importlib.util
from pathlib import Path
import numpy as np
import torch,torch.nn as nn
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
    from tools import train_eth_persistent_repair_specialist_v3_parent_child_graph as v3
except ImportError:
    vp=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py')
    sp=importlib.util.spec_from_file_location('v3_parent_graph',vp);v3=importlib.util.module_from_spec(sp);sp.loader.exec_module(v3)

SEED=20260901
TASKS=('repair_after_current_expand_30s','repair_continues_from_current_repair_30s','activity_within_30s','parallel_children_within_30s','next_active_child_is_repair')
REG='time_to_next_repair'
MODES={
 'repair_after_current_expand_30s':'triple',
 'repair_continues_from_current_repair_30s':'repair',
 'activity_within_30s':'all',
 'parallel_children_within_30s':'triple',
 'next_active_child_is_repair':'all',
 REG:'triple'
}

def relabel(rows):
    by={}
    for r in rows:by.setdefault(int(r['market']),[]).append(r)
    for rr in by.values():
        rr.sort(key=lambda z:(int(z['t']),int(z.get('rel',0))))
        for j,x in enumerate(rr):
            fut=[]
            for y in rr[j+1:]:
                dt=int(y['t'])-int(x['t'])
                if dt>120000:break
                fut.append((dt,y))
            f30=[(dt,y) for dt,y in fut if dt<=30000]
            reps=[y for _,y in f30 if int(y['rel'])==1];exps=[y for _,y in f30 if int(y['rel'])==-1]
            x['repair_after_current_expand_30s']=None if int(x['rel'])!=-1 else int(bool(reps))
            x['repair_continues_from_current_repair_30s']=None if int(x['rel'])!=1 else int(bool(reps))
            x['activity_within_30s']=int(bool(f30))
            x['parallel_children_within_30s']=int(bool(reps) and bool(exps))
            nxt=rr[j+1] if j+1<len(rr) else None
            x['next_active_child_is_repair']=None if nxt is None else int(int(nxt['rel'])==1)
            nr=next(((dt,y) for dt,y in fut if int(y['rel'])==1),None)
            x[REG]=None if nr is None else math.log1p(nr[0])/math.log1p(120000)
    return rows

def arr(rows,task):
    z=[r for r in rows if r.get(task) is not None]
    return (z,np.stack([r['curz'] for r in z]),np.stack([r['graphz'] for r in z]),
            np.stack([r['allseqz'] for r in z]),np.stack([r['allmask'] for r in z]),
            np.stack([r['repseqz'] for r in z]),np.stack([r['repmask'] for r in z]),
            np.stack([r['expseqz'] for r in z]),np.stack([r['expmask'] for r in z]),
            np.asarray([r[task] for r in z],np.float32))

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

class TopologyExpert(nn.Module):
    def __init__(self,reg=False):
        super().__init__();d=len(v3.CUR_FEATURES)+len(v3.GRAPH_FEATURES);self.body=nn.Sequential(nn.Linear(d,72),nn.ReLU(),nn.LayerNorm(72),nn.Linear(72,48),nn.ReLU());self.out=nn.Linear(48,1);self.reg=reg
    def forward(self,c,g,a,r,e):return self.out(self.body(torch.cat([c,g],1))).squeeze(-1)

class SequenceExpert(nn.Module):
    def __init__(self,mode='all'):
        super().__init__();self.mode=mode;td=len(v3.TOK_FEATURES);d0=len(v3.CUR_FEATURES)+len(v3.GRAPH_FEATURES)
        self.top=nn.Sequential(nn.Linear(d0,72),nn.ReLU(),nn.LayerNorm(72),nn.Linear(72,48),nn.ReLU())
        if mode=='all':self.ga=nn.GRU(td,36,batch_first=True);hd=36
        elif mode=='repair':self.gr=nn.GRU(td,36,batch_first=True);hd=36
        else:self.ga=nn.GRU(td,28,batch_first=True);self.gr=nn.GRU(td,24,batch_first=True);self.ge=nn.GRU(td,24,batch_first=True);hd=76
        self.fuse=nn.Sequential(nn.Linear(48+hd,72),nn.ReLU(),nn.LayerNorm(72),nn.Linear(72,48),nn.ReLU());self.out=nn.Linear(48,1)
    def forward(self,c,g,a,r,e):
        top=self.top(torch.cat([c,g],1))
        if self.mode=='all':_,h=self.ga(a);hist=h[-1]
        elif self.mode=='repair':_,h=self.gr(r);hist=h[-1]
        else:
            _,ha=self.ga(a);_,hr=self.gr(r);_,he=self.ge(e);hist=torch.cat([ha[-1],hr[-1],he[-1]],1)
        return self.out(self.fuse(torch.cat([top,hist],1))).squeeze(-1)

def perturb_batch(A,AM,R,RM,E,EM,rng):
    na=[];nr=[];ne=[]
    for a,am,r,rm,e,em in zip(A,AM,R,RM,E,EM):
        na.append(v3.perturb(a,am,rng,True)[0]);nr.append(v3.perturb(r,rm,rng,True)[0]);ne.append(v3.perturb(e,em,rng,True)[0])
    return np.stack(na),np.stack(nr),np.stack(ne)

def train_one(model,rows,task,dev,specialist):
    model.to(dev);rng=np.random.default_rng(SEED+abs(hash(task))%10000+(100 if specialist else 0));opt=torch.optim.AdamW(model.parameters(),lr=8e-4 if specialist else 9e-4,weight_decay=1e-4)
    z,X,G,A,AM,R,RM,E,EM,y=arr(rows,task);epochs=12 if specialist else 8;steps=135 if specialist else 90
    isreg=(task==REG)
    for ep in range(epochs):
        run=0.
        for _ in range(steps):
            idx=rng.integers(0,len(y),size=min(384,len(y)));cx=X[idx];cg=G[idx];ca=A[idx];cr=R[idx];ce=E[idx];cam=AM[idx];crm=RM[idx];cem=EM[idx];cy=y[idx]
            xt=torch.from_numpy(cx).to(dev);gt=torch.from_numpy(cg).to(dev);at=torch.from_numpy(ca).to(dev);rt=torch.from_numpy(cr).to(dev);et=torch.from_numpy(ce).to(dev);yt=torch.from_numpy(cy).to(dev)
            opt.zero_grad();clean=model(xt,gt,at,rt,et)
            if isreg:loss=nn.functional.smooth_l1_loss(torch.sigmoid(clean),yt)
            else:
                pos=max(float(yt.mean()),1e-4);pw=torch.tensor((1-pos)/pos,device=dev).clamp(.3,5);loss=nn.functional.binary_cross_entropy_with_logits(clean,yt,pos_weight=pw)
            if specialist:
                nA,nR,nE=perturb_batch(ca,cam,cr,crm,ce,cem,rng);noisy=model(xt,gt,torch.from_numpy(nA).to(dev),torch.from_numpy(nR).to(dev),torch.from_numpy(nE).to(dev))
                if isreg:aug=nn.functional.smooth_l1_loss(torch.sigmoid(noisy),yt);con=nn.functional.mse_loss(torch.sigmoid(noisy),torch.sigmoid(clean).detach())
                else:aug=nn.functional.binary_cross_entropy_with_logits(noisy,yt,pos_weight=pw);con=nn.functional.mse_loss(torch.sigmoid(noisy),torch.sigmoid(clean).detach())
                loss=loss+.50*aug+.25*con
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),5);opt.step();run+=float(loss.detach().cpu())
        print(json.dumps({'task':task,'model':'SPECIALIST' if specialist else 'BASELINE','epoch':ep+1,'loss':run/steps}),flush=True)
    return model

def evaluate(model,rows,task,dev,noisy=False):
    z,X,G,A,AM,R,RM,E,EM,y=arr(rows,task);rng=np.random.default_rng(SEED+991+abs(hash(task))%1000)
    if noisy:A,R,E=perturb_batch(A,AM,R,RM,E,EM,rng)
    ps=[];model.eval()
    with torch.no_grad():
        for i in range(0,len(y),4096):ps.append(model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev)).cpu().numpy())
    p=np.concatenate(ps)
    if task==REG:
        p=1/(1+np.exp(-p));return {'n':int(len(y)),'mae':float(np.mean(np.abs(p-y))),'rmse':float(np.sqrt(np.mean((p-y)**2)))}
    return metric(y,1/(1+np.exp(-p)))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
    rows,cut,nwin,births=v3.build(a.db);rows=relabel(rows);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];stats=v3.standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
    base={};spec={};noise={};models={};delta={};rob={};rates={}
    for task in TASKS:
        torch.manual_seed(SEED);b=train_one(TopologyExpert(),tr,task,dev,False);base[task]=evaluate(b,te,task,dev,False)
        torch.manual_seed(SEED);s=train_one(SequenceExpert(MODES[task]),tr,task,dev,True);spec[task]=evaluate(s,te,task,dev,False);noise[task]=evaluate(s,te,task,dev,True);models[task]=s.state_dict();delta[task]=spec[task]['auc']-base[task]['auc'];rob[task]=noise[task]['auc']-spec[task]['auc'];rates[task]={'trainPositiveRate':float(arr(tr,task)[-1].mean()),'testPositiveRate':float(arr(te,task)[-1].mean()),'trainN':len(arr(tr,task)[-1]),'testN':len(arr(te,task)[-1])}
    torch.manual_seed(SEED);rg=train_one(SequenceExpert(MODES[REG]),tr,REG,dev,True);spec[REG]=evaluate(rg,te,REG,dev,False);noise[REG]=evaluate(rg,te,REG,dev,True);models[REG]=rg.state_dict()
    mean_delta=float(np.mean([delta[t] for t in TASKS]));passv=bool(spec['repair_after_current_expand_30s']['auc']>=.60 and spec['repair_continues_from_current_repair_30s']['auc']>=.60 and spec['activity_within_30s']['auc']>=.68 and spec['parallel_children_within_30s']['auc']>=.60 and spec['next_active_child_is_repair']['auc']>=.54 and sum(rob[t]>=-.035 for t in TASKS)>=4 and mean_delta>=0.)
    out={'version':'ETH_PERSISTENT_REPAIR_SPECIALIST_V4_SELECTIVE_PARALLEL_EXPERTS','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'graphParentBirths':births,'rows':len(rows),'trainRows':len(tr),'testRows':len(te),'taskModes':MODES,'labelRates':rates,'matchedTopologyBaseline':base,'selectiveExpertsClean':spec,'selectiveExpertsRandomPerturbation':noise,'aucDeltaExpertMinusBaseline':delta,'aucDeltaPerturbedMinusClean':rob,'meanTaskDeltaVsMatchedTopologyBaseline':mean_delta,'representationPass':passv,'passRule':'repairAfterExpand>=.60; repairContinue>=.60; activity>=.68; parallel>=.60; nextChildRepair>=.54; >=4/5 perturb drops >=-.035; mean task delta >=0','trainingObjective':['clean mutually exclusive parent/child transition semantics','independent parallel experts avoid multi-task negative transfer','structured topology owns parent/context; sequence specializes trajectory/urgency/routing','anti-passivity activity head'],'boundary':['ETH-only Target actual-filled chronology labels/gradients','BTC architecture-only','no winner/future PnL runtime feature','no TARGET_UNIT=18 or expected_parent_shares','no dream fill','deterministic execution/carrier ownership outside neural encoder','development representation only']}
    rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.pt';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');torch.save({'version':out['version'],'models':models,'representationPass':passv,'stats':stats},mp);print(json.dumps({'ok':True,'representationPass':passv,'device':dev,'labelRates':rates,'baseline':base,'experts':spec,'noise':noise,'delta':delta,'rob':rob,'meanDelta':mean_delta,'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
