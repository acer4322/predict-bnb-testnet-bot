from __future__ import annotations
import math,sys,importlib.util
from pathlib import Path
import numpy as np
import torch

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
    from tools import train_eth_persistent_repair_specialist_v3_parent_child_graph as v3
    from tools import train_eth_persistent_repair_specialist_v5_factorized_capability_router as v5
except ImportError:
    def lm(name,file):
        p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
    v3=lm('v3_online','train_eth_persistent_repair_specialist_v3_parent_child_graph.py');v5=lm('v5_online','train_eth_persistent_repair_specialist_v5_factorized_capability_router.py')

class OnlineCapabilityState:
    def __init__(self):
        self.up=self.dn=self.cost=self.upcost=self.dncost=0.0;self.maker_n=self.taker_n=0
        self.makers=[];self.alltok=[];self.reptok=[];self.exptok=[]
        self.parent_birth_t=None;self.birth_ab=0.;self.birth_pc=1.;self.birth_fr=0.;self.min_ab=1.;self.max_pc=0.;self.max_fr=-5.
        self.rep_n=self.exp_n=self.trans_n=0;self.cum_rep_ab=self.cum_rep_pc=self.cum_rep_fr=self.cum_exp_pair=0.;self.last_rep=None;self.last_exp=None;self.last_rel=0;self.parent_births=0
    def _met(self):
        _,m=v3.state(self.up,self.dn,self.cost,self.upcost,self.dncost,self.maker_n,self.taker_n,0,1e6,0,0);return m
    def snapshot(self,t,end):
        prev_rel=self.makers[-1]['rel'] if self.makers else 0;prev_age=(int(t)-self.makers[-1]['time']) if self.makers else 1e6
        cur,met=v3.state(self.up,self.dn,self.cost,self.upcost,self.dncost,self.maker_n,self.taker_n,prev_rel,prev_age,end,t)
        if self.parent_birth_t is None:
            # Neutral graph before first responsibility child.
            graph=np.zeros(len(v3.GRAPH_FEATURES),np.float32);graph[3]=0.;graph[10]=0.;graph[11]=0.;graph[12]=0.;graph[13]=met['ab'];graph[14]=met['pc'];graph[15]=max(-5,min(5,met['fr']))
        else:
            recent=[m for m in self.makers if int(t)-m['time']<=30000];rr=sum(m['rel']==1 for m in recent);ee=sum(m['rel']==-1 for m in recent);nchild=self.rep_n+self.exp_n
            age=max(0,int(t)-self.parent_birth_t);lra=1e6 if self.last_rep is None else int(t)-self.last_rep;lea=1e6 if self.last_exp is None else int(t)-self.last_exp
            graph=np.asarray([
                math.log1p(min(age,300000))/math.log1p(300000),self.rep_n/max(nchild,1),self.exp_n/max(nchild,1),self.trans_n/max(nchild,1),
                math.log1p(min(lra,120000))/math.log1p(120000),math.log1p(min(lea,120000))/math.log1p(120000),math.tanh(self.cum_rep_ab/2),math.tanh(self.cum_rep_pc/2),math.tanh(self.cum_rep_fr/4),math.tanh(self.cum_exp_pair/2),
                max(-1,min(1,self.birth_ab-met['ab'])),max(-1,min(1,met['pc']-self.birth_pc)),max(-2,min(2,met['fr']-self.birth_fr)),self.min_ab,self.max_pc,max(-5,min(5,self.max_fr)),rr/max(len(recent),1),ee/max(len(recent),1),float(rr>0 and ee>0),math.log1p(nchild)/math.log1p(128)
            ],np.float32)
        a,am=v3.pad(self.alltok);r,rm=v3.pad(self.reptok);e,em=v3.pad(self.exptok)
        return {'cur':cur,'graph':graph,'allseq':a,'allmask':am,'repseq':r,'repmask':rm,'expseq':e,'expmask':em,'met':met,'prev_rel':prev_rel,'prev_age':prev_age}
    def apply_event(self,role,side,t,shares,price,end):
        role=str(role);side=str(side);t=int(t);sh=float(shares);px=float(price)
        snap=self.snapshot(t,end);met=snap['met'];rel=v3.rel_for(side,self.up,self.dn) if role=='MAKER' else 0
        if role=='MAKER' and rel!=0:
            quiescent=(self.makers and t-self.makers[-1]['time']>45000);near_flat=(met['gross']>0 and met['ab']<=.02 and met['pc']>=.98)
            if self.parent_birth_t is None or quiescent or near_flat:
                self.parent_birth_t=t;self.birth_ab=met['ab'];self.birth_pc=met['pc'];self.birth_fr=met['fr'];self.min_ab=met['ab'];self.max_pc=met['pc'];self.max_fr=met['fr'];self.rep_n=self.exp_n=self.trans_n=0;self.cum_rep_ab=self.cum_rep_pc=self.cum_rep_fr=self.cum_exp_pair=0.;self.last_rep=self.last_exp=None;self.last_rel=0;self.parent_births+=1
                snap=self.snapshot(t,end);met=snap['met']
        pre=met.copy();prev_time=self.makers[-1]['time'] if self.makers else None
        if side=='UP':self.up+=sh;self.upcost+=sh*px
        else:self.dn+=sh;self.dncost+=sh*px
        self.cost+=sh*px
        if role=='MAKER' and rel!=0:
            _,post=v3.state(self.up,self.dn,self.cost,self.upcost,self.dncost,self.maker_n+1,self.taker_n,rel,1,end,t);elapsed=1e6 if prev_time is None else t-prev_time;qgr=sh/max(pre['gap'],1.);parent_age=0. if self.parent_birth_t is None else min(1.,max(0.,(t-self.parent_birth_t)/300000.));recent_switch=float(self.last_rel!=0 and rel!=self.last_rel)
            tok=np.asarray([float(rel),post['ab']-pre['ab'],post['pc']-pre['pc'],max(-5,min(5,post['fr']-pre['fr'])),max(-5,min(5,post['br']-pre['br'])),math.log1p(min(elapsed,120000))/math.log1p(120000),max(0,min(3,qgr)),px,post['ab'],post['pc'],parent_age,recent_switch],np.float32)
            self.alltok.append(tok)
            if rel==1:self.reptok.append(tok);self.rep_n+=1;self.cum_rep_ab+=max(0.,pre['ab']-post['ab']);self.cum_rep_pc+=max(0.,post['pc']-pre['pc']);self.cum_rep_fr+=max(0.,post['fr']-pre['fr']);self.last_rep=t
            else:self.exptok.append(tok);self.exp_n+=1;self.cum_exp_pair+=max(0.,pre['pc']-post['pc']);self.last_exp=t
            if self.last_rel!=0 and rel!=self.last_rel:self.trans_n+=1
            self.last_rel=rel;self.min_ab=min(self.min_ab,post['ab']);self.max_pc=max(self.max_pc,post['pc']);self.max_fr=max(self.max_fr,post['fr']);self.makers.append({'rel':rel,'time':t});self.maker_n+=1
        elif role=='TAKER':self.taker_n+=1
        return rel,snap

class UnifiedCapabilityRuntime:
    TASKS=('repair_obligation_30s','expand_opportunity_30s','both_responsibilities_30s','activity_urgency_30s')
    def __init__(self,model_path,device='cpu'):
        ck=torch.load(model_path,map_location='cpu',weights_only=False);self.device=torch.device(device if device=='cuda' and torch.cuda.is_available() else 'cpu');self.models={}
        self.mu,self.sd,self.gmu,self.gsd,self.tmu,self.tsd=[np.asarray(x,np.float32) for x in ck['stats']]
        for t in self.TASKS:
            m=v5.CapabilityExpert(v5.MODES[t]);m.load_state_dict(ck['models'][t]);m.to(self.device).eval();self.models[t]=m
    def predict(self,s):
        c=((s['cur']-self.mu)/self.sd).astype(np.float32)[None,:];g=((s['graph']-self.gmu)/self.gsd).astype(np.float32)[None,:]
        def zs(x,m):return (((x-self.tmu)/self.tsd).astype(np.float32)*m[:,None])[None,:,:]
        A=zs(s['allseq'],s['allmask']);R=zs(s['repseq'],s['repmask']);E=zs(s['expseq'],s['expmask'])
        ct=torch.from_numpy(c).to(self.device);gt=torch.from_numpy(g).to(self.device);at=torch.from_numpy(A).to(self.device);rt=torch.from_numpy(R).to(self.device);et=torch.from_numpy(E).to(self.device)
        out={}
        with torch.no_grad():
            for t,m in self.models.items():out[t]=float(torch.sigmoid(m(ct,gt,at,rt,et)).item())
        out['both_responsibilities_30s']=min(out['both_responsibilities_30s'],out['repair_obligation_30s'],out['expand_opportunity_30s'])
        out['activity_urgency_30s']=max(out['activity_urgency_30s'],out['repair_obligation_30s'],out['expand_opportunity_30s'])
        return out
