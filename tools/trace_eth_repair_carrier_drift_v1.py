from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))
import run_eth_repair_functional_exam_v2_responsibility_ledger as v2
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_dagger60_smoke_v1 as v1
import run_eth_repair_functional_exam_v1 as ex1

SELECT=[1818843,1821523,1817824,1818265,1818007,1818920]

class TraceSim(v2.RepairLedgerSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.trace=[];self.authz=None;self.orderMeta={}
    def choose_authorized(self,t,end,proposed_side,proposed_qty):
        z=super().choose_authorized(t,end,proposed_side,proposed_qty)
        if z is not None:
            side,qty,p=z;ai=self.auth_inv();self.authz={'t':int(t),'side':side,'qty':float(qty),'role':self.activeObjective['role'] if self.activeObjective else None,'authInv':dict(ai),'obsInv':dict(self.inv),'reservedUP':float(self.reserved_authoritative('UP')),'reservedDOWN':float(self.reserved_authoritative('DOWN')),'unobsUP':float(self.unobserved_qty('UP')),'unobsDOWN':float(self.unobserved_qty('DOWN')),'p':dict(p)}
        else:self.authz=None
        return z
    def submit(self,t,side,p,q):
        n_before=self.n;ok=super().submit(t,side,p,q)
        if ok:
            key=f'{side}_{n_before}'
            a=self.authz if self.authz and self.authz['t']==int(t) and self.authz['side']==side else None
            ai=self.auth_inv();truthRole=ex1.role_from_inv(ai,side)
            meta={'key':key,'t':int(t),'side':side,'qty':float(q),'price':float(p),'authorizedRole':a['role'] if a else 'SEED_OR_UNSUPERVISED','truthRoleAtSubmit':truthRole,'authInvAtSubmit':dict(ai),'obsInvAtSubmit':dict(self.inv),'oppositeReservedAtSubmit':float(self.reserved_authoritative('DOWN' if side=='UP' else 'UP')),'sameReservedAtSubmit':float(self.reserved_authoritative(side)),'authz':a,'firstFill':None}
            self.orderMeta[key]=meta;self.trace.append({'event':'SUBMIT',**meta})
        return ok
    def process(self,t):
        # Capture pre-fill context before parent mutates cumulative qty.
        pre=[]
        for key,o in self.orders.items():
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0.0);inc=max(0.0,cum-float(o.get('cum') or 0.0))
            if inc>v2.EPS and key not in self.firstFillSeen:
                ai=dict(self.truthInv);pre.append((key,o['side'],inc,ex1.role_from_inv(ai,o['side']),dict(ai),len(self.authHist)))
        super().process(t)
        for key,side,inc,role,preInv,hidx in pre:
            m=self.orderMeta.get(key,{})
            between=self.authHist[hidx:-1] if len(self.authHist)>hidx else []
            ev={'event':'FIRST_FILL','key':key,'t':int(t),'side':side,'inc':float(inc),'authorizedRole':m.get('authorizedRole'),'truthRoleAtSubmit':m.get('truthRoleAtSubmit'),'truthRoleAtFill':role,'preTruthInv':preInv,'oppositeReservedAtSubmit':m.get('oppositeReservedAtSubmit'),'otherFillsBetweenSubmitAndFirstFill':[{'side':x['side'],'shares':x['shares'],'rel':x['rel'],'time':x['time']} for x in between]}
            self.trace.append(ev);m['firstFill']=ev

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_carrier_trace_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];models,_,_=joblib.load(a.dagger_cache);life=v2.LifecycleRuntime(Path(a.lifecycle_model));rows=[]
        cmap={int(r['marketId']):r for r in cohort}
        for mid in SELECT:
            cr=cmap[mid];sim=TraceSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0)
            try:summary=sim.run_exam_v2(models,cr['winner'])
            finally:sim.close()
            drifts=[]
            for e in sim.trace:
                if e.get('event')=='FIRST_FILL' and e.get('authorizedRole')=='REPAIR' and e.get('truthRoleAtFill')=='EXPAND':drifts.append(e)
            rows.append({'marketId':mid,'summary':summary,'drifts':drifts,'trace':sim.trace})
            print(json.dumps({'marketId':mid,'drifts':len(drifts),'submits':summary['submits'],'fills':summary['actualFillEvents']}),flush=True)
        driftEvents=[e for r in rows for e in r['drifts']]
        out={'version':'ETH_REPAIR_CARRIER_DRIFT_TRACE_V1','markets':SELECT,'driftEvents':len(driftEvents),'driftWithOppositeReservedAtSubmit':sum(float(e.get('oppositeReservedAtSubmit') or 0)>v2.EPS for e in driftEvents),'driftWithOtherFillBeforeFirstFill':sum(bool(e.get('otherFillsBetweenSubmitAndFirstFill')) for e in driftEvents),'rows':rows,'boundary':['CONTROL only','cached DAgger; no retrain','consumed development markets only','diagnostic, not promotion evidence']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({k:out[k] for k in ['version','driftEvents','driftWithOppositeReservedAtSubmit','driftWithOtherFillBeforeFirstFill']}),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
