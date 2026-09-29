from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_local_pending_reservation_v1 as lp

PHASE_FEATURES=['expansion_debt_active','repair_progress','expansion_debt_to_gross','ms_since_last_expansion','last_material_transition']

class PhaseMixin:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.expDebt=0.0;self.lastExpandDebt=0.0;self.lastExpandT=None;self.lastMaterialTransition=0.0;self.phaseExpandTransitions=0;self.phaseRepairTransitions=0
    def record_fill(self,t,side,q,p):
        pre=abs(float(self.inv['UP'])-float(self.inv['DOWN']))
        super().record_fill(t,side,q,p)
        post=abs(float(self.inv['UP'])-float(self.inv['DOWN']));d=post-pre
        if d>v1.EPS:
            self.expDebt=max(0.0,self.expDebt)+d;self.lastExpandDebt=self.expDebt;self.lastExpandT=int(t);self.lastMaterialTransition=1.0;self.phaseExpandTransitions+=1
        elif d<-v1.EPS:
            self.lastMaterialTransition=-1.0;self.phaseRepairTransitions+=1
            if self.expDebt>v1.EPS:
                self.expDebt=max(0.0,self.expDebt-(-d))
                if self.expDebt<=v1.EPS:self.expDebt=0.0;self.lastExpandDebt=0.0;self.lastExpandT=None
    def phase_values(self,t):
        gross=float(self.inv['UP'])+float(self.inv['DOWN']);active=1.0 if self.expDebt>v1.EPS and self.lastExpandDebt>v1.EPS else 0.0
        progress=max(0.0,min(1.0,(self.lastExpandDebt-self.expDebt)/self.lastExpandDebt)) if active else 0.0
        debt_ratio=self.expDebt/max(gross,1.0) if active else 0.0
        age=float(min(60000,max(0,int(t)-int(self.lastExpandT)))) if self.lastExpandT is not None else 60000.0
        return np.asarray([active,progress,debt_ratio,age,self.lastMaterialTransition],np.float32)
    def features(self,t,qv,ca,end):
        base=super().features(t,qv,ca,end);return np.concatenate([base,self.phase_values(t)]).astype(np.float32)

class PhaseTrainSim(PhaseMixin,v1.Sim):
    pass

class PhaseTestSim(PhaseMixin,lp.LocalReservedBootSim):
    pass

def train_phase(tmp,cohort,traj):
    train=[r for r in cohort if r['split']=='TRAIN40'];X1=[];A1=[];S1=[];Q1=[];M1=[]
    for cr in train:
        for seed in v1.SEEDS:
            sim=PhaseTrainSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
            try:x,b,c,d=sim.run_oracle_collect()
            finally:sim.close()
            X1.extend(x);A1.extend(b);S1.extend(c);Q1.extend(d);M1.extend([int(cr['marketId'])]*len(x))
    models1,off1=v1.fit_models(X1,A1,S1,Q1,M1)
    X2=[];A2=[];S2=[];Q2=[];M2=[]
    for i,cr in enumerate(train,1):
        for seed in v1.SEEDS:
            sim=PhaseTrainSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
            try:x,b,c,d=v2.student_collect(sim,models1)
            finally:sim.close()
            X2.extend(x);A2.extend(b);S2.extend(c);Q2.extend(d);M2.extend([int(cr['marketId'])]*len(x))
        if i%10==0:print(json.dumps({'trainProgress':i,'rows2':len(X2),'actions2':int(sum(A2))}),flush=True)
    models2,off2=v1.fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2)
    return models2,off1,off2,{'round1Rows':len(X1),'round1Actions':int(sum(A1)),'round2Rows':len(X2),'round2Actions':int(sum(A2))}

def agg(rs):
    buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);active=[r for r in rs if r['buyNotional']>v1.EPS]
    return {'markets':len(rs),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs),'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'meanExpandTransitions':statistics.mean(r['phaseExpandTransitions'] for r in rs),'meanRepairTransitions':statistics.mean(r['phaseRepairTransitions'] for r in rs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_phase_aware_dagger_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2,trstats=train_phase(tmp,cohort,traj);test=[r for r in cohort if r['split']!='TRAIN40'];rows=[]
        for i,cr in enumerate(test,1):
            sim=PhaseTestSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
            try:r=sim.run_student(models,cr['winner']);opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=sim.inv[opp]-sim.cost;r['fills']=sim.fills;r['phaseExpandTransitions']=sim.phaseExpandTransitions;r['phaseRepairTransitions']=sim.phaseRepairTransitions
            finally:sim.close()
            r.update({'marketId':int(cr['marketId']),'winner':cr['winner']});rows.append(r)
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'pnlSoFar':sum(x['pnl'] for x in rows),'winsSoFar':sum(x['pnl']>0 for x in rows)}),flush=True)
        summary=agg(rows);out={'version':'ETH_PHASE_AWARE_DAGGER_V1','boundary':['Same two-round DAgger teacher/oracle family as frozen baseline','Only representation change: five persistent objective-phase features derived from own material fills: expansion debt active, repair progress, debt/gross, time since last expansion, last material transition','Fresh101 runtime keeps BOOK_IMBALANCE bootstrap + Local Pending Reservation','No Target future/objective/winner at runtime; no threshold sweep; <=180s no new exposure'],'phaseFeatures':PHASE_FEATURES,'round1Offline':off1,'round2Offline':off2,'training':trstats,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'training':trstats,'round1Offline':off1,'round2Offline':off2,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
