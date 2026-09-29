from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,statistics,sys
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_smoke_v2 as v2
import run_eth_dagger60_bootstrap_v3 as v3

class AuthoritySim(v3.BootSim):
    def __init__(self,tape,models,cap,mode):
        super().__init__(tape,'BOOK_IMBALANCE',models)
        self.cap=float(cap);self.govMode=mode;self.blocked=0;self.lastQv=None;self.pathAuthorized=0;self.pathDenied=0
    def seed_if_needed(self,t,qv):
        self.lastQv=qv
        return super().seed_if_needed(t,qv)
    def submit(self,t,side,p,q):
        if self.seeded and self.govMode!='BASELINE':
            ru=self.reserved('UP');rd=self.reserved('DOWN')
            cur=(self.inv['UP']+ru)-(self.inv['DOWN']+rd)
            nxt=cur+(q if side=='UP' else -q)
            increasing=abs(nxt)>=abs(cur)-1e-9
            beyond=abs(nxt)>self.cap+1e-9
            if increasing and beyond:
                if self.govMode=='HARD_CAP':
                    self.blocked+=1;return False
                if self.govMode=='REPAIR_PIPELINE_AUTH':
                    weak='DOWN' if side=='UP' else 'UP'
                    weak_reserved=self.reserved(weak)
                    weak_bid=float((self.lastQv or {}).get(weak,{}).get('bid') or 0.0)
                    live_path=weak_reserved>1e-9
                    economic_path=(weak_bid>0.0 and float(p)+weak_bid<=1.0+1e-9)
                    if live_path or economic_path:self.pathAuthorized+=1
                    else:
                        self.pathDenied+=1;self.blocked+=1;return False
        return super().submit(t,side,p,q)

def tail_metrics(rs):
    ps=sorted(float(r['pnl']) for r in rs);total=sum(ps);best=max(ps);worst=min(ps)
    return {'maxWin':best,'maxLoss':worst,'leaveOneBestOutPnl':total-best,'medianPnl':statistics.median(ps),'p10Pnl':float(np.quantile(ps,.1))}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_repair_pipeline_auth_v1_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));train=[r for r in cohort if r['split']=='TRAIN40'];test=[r for r in cohort if r['split']=='TEST20']
        vals=[]
        for cr in train:
            up=dn=0.0
            for r in traj.get(str(cr['marketId']),[]):
                if r['side']=='UP':up+=float(r['shares'])
                else:dn+=float(r['shares'])
                vals.append(abs(up-dn))
        cap=float(np.quantile(vals,.90))
        X1=[];A1=[];S1=[];Q1=[];M1=[]
        for cr in train:
            for seed in v1.SEEDS:
                sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
                try:x,b,c,d=sim.run_oracle_collect()
                finally:sim.close()
                X1.extend(x);A1.extend(b);S1.extend(c);Q1.extend(d);M1.extend([int(cr['marketId'])]*len(x))
        models1,off1=v1.fit_models(X1,A1,S1,Q1,M1)
        X2=[];A2=[];S2=[];Q2=[];M2=[]
        for i,cr in enumerate(train,1):
            for seed in v1.SEEDS:
                sim=v1.Sim(tmp/'tapes'/f"{cr['marketId']}.json.xz",traj.get(str(cr['marketId']),[]),seed)
                try:x,b,c,d=v2.student_collect(sim,models1)
                finally:sim.close()
                X2.extend(x);A2.extend(b);S2.extend(c);Q2.extend(d);M2.extend([int(cr['marketId'])]*len(x))
            if i%10==0:print(json.dumps({'collect':i,'rows2':len(X2)}),flush=True)
        models2,off2=v1.fit_models(X1+X2,A1+A2,S1+S2,Q1+Q2,M1+M2)
        rows=[]
        for mode in ('BASELINE','HARD_CAP','REPAIR_PIPELINE_AUTH'):
            for cr in test:
                sim=AuthoritySim(tmp/'tapes'/f"{cr['marketId']}.json.xz",models2,cap,mode)
                try:r=sim.run_student(models2,cr['winner'])
                finally:sim.close()
                r.update({'marketId':int(cr['marketId']),'mode':mode,'winner':cr['winner'],'blocked':sim.blocked,'pathAuthorized':sim.pathAuthorized,'pathDenied':sim.pathDenied});rows.append(r)
        sums=[]
        for mode in ('BASELINE','HARD_CAP','REPAIR_PIPELINE_AUTH'):
            rs=[r for r in rows if r['mode']==mode];s=v1.agg(rs);s.update(tail_metrics(rs));s['mode']=mode;s['meanBlocked']=statistics.mean(r['blocked'] for r in rs);s['meanPathAuthorized']=statistics.mean(r.get('pathAuthorized',0) for r in rs);s['meanPathDenied']=statistics.mean(r.get('pathDenied',0) for r in rs);sums.append(s)
        out={'version':'ETH_DAGGER60_REPAIR_PIPELINE_AUTHORITY_V1','boundary':['TRAIN40 two-round DAgger exactly as bootstrap-v3','TEST20 BOOK_IMBALANCE seed; no Target trajectory/objective at runtime','authority boundary frozen at TRAIN40 Target strict-past Maker cumulative abs-net p90','beyond that boundary, exposure expansion is permitted only when a weak-side repair pipeline already has reserved quantity OR current proposed dominant Maker price plus current weak-side best bid is <=1.00; exposure-reducing repair is never blocked','no threshold sweep; repair-path economics use binary pair no-loss boundary 1.00','<=180s no new exposure; Maker-only realistic HFT'],'trainAbsNetP90Cap':cap,'round1Offline':off1,'round2Offline':off2,'summaries':sums,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'cap':cap,'summaries':sums},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
