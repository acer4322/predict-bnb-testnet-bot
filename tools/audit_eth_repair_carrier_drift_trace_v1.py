from __future__ import annotations
import argparse, json, tempfile, zipfile, shutil, sys
from pathlib import Path
import numpy as np
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))

import run_eth_repair_functional_exam_v3_objective_handoff as v3
import run_eth_repair_functional_exam_v1 as ex1
import run_eth_dagger60_smoke_v1 as v1

EPS=1e-9


def carrier_snapshot(sim):
    out=[]
    for key,o in sim.orders.items():
        try:s=sim.snap(o)
        except Exception:s={}
        out.append({
            'key':key,'side':o.get('side'),'objectiveId':o.get('objective_id'),'objectiveRole':o.get('objective_role'),
            'status':s.get('status'),'leavesQty':float(s.get('leavesQty') or 0.0),
            'cumExecQty':float(s.get('cumExecQty') or 0.0),'cancelRequested':key in sim.cancelRequestedAt,
            'localPending':key in sim.localPending.get(o.get('side'),{}) if o.get('side') in sim.localPending else False,
        })
    return out


class TraceSim(v3.RepairLedgerSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.submitTrace={}
        self.driftEvents=[]
    def submit(self,t,side,p,q):
        n_before=self.n; key=f'{side}_{n_before}'
        pending_role=getattr(self,'_pendingAuthorizedRole',None)
        pending_oid=getattr(self,'_pendingAuthorizedObjectiveId',None)
        snap={
            't':int(t),'side':side,'qty':float(q),'price':float(p),
            'pendingAuthorizedRole':pending_role,'pendingObjectiveId':pending_oid,
            'truthInv':dict(self.truthInv),'authInv':self.auth_inv(),
            'activeObjective':dict(self.activeObjective) if self.activeObjective else None,
            'carriers':carrier_snapshot(self),'authHistIndex':len(self.authHist),
        }
        ok=super().submit(t,side,p,q)
        if ok:self.submitTrace[key]=snap
        return ok
    def process(self,t):
        # Reproduce parent order iteration only to capture the exact pre-truth inventory for first fills.
        temp_truth=dict(self.truthInv)
        candidates=[]
        for key,o in self.orders.items():
            s=self.snap(o);cum=float(s.get('cumExecQty') or 0.0);inc=max(0.0,cum-float(o.get('cum') or 0.0))
            if inc>EPS:
                pre=dict(temp_truth); role=ex1.role_from_inv(pre,o['side'])
                if key not in self.firstFillSeen:
                    auth_role=self.submitRoleAuthorized.get(key,self.submitRoleObserved.get(key))
                    if auth_role=='REPAIR' and role=='EXPAND':
                        st=self.submitTrace.get(key,{})
                        s_inv=st.get('truthInv') or {'UP':0.0,'DOWN':0.0}
                        side=o['side'];opp='DOWN' if side=='UP' else 'UP'
                        delta_side=float(pre[side])-float(s_inv.get(side,0.0))
                        delta_opp=float(pre[opp])-float(s_inv.get(opp,0.0))
                        submit_gap=float(s_inv.get(opp,0.0))-float(s_inv.get(side,0.0))
                        candidates.append({
                            'key':key,'t':int(t),'side':side,'authorizedRole':auth_role,
                            'submit':st,'preFillTruthInv':pre,'fillInc':float(inc),
                            'deltaSameSideBeforeFirstFill':delta_side,
                            'deltaOppSideBeforeFirstFill':delta_opp,
                            'submitRepairGap':submit_gap,
                            'netInterveningSameMinusOpp':delta_side-delta_opp,
                            'crossMarginBeforeOwnFirstFill':float(pre[side])-float(pre[opp]),
                            'cancelRequestedBeforeFirstFill':key in self.cancelRequestedAt,
                            'carriersAtFirstFill':carrier_snapshot(self),
                            'activeObjectiveAtFirstFill':dict(self.activeObjective) if self.activeObjective else None,
                            'authHistSinceSubmit':[{
                                'rel':int(m['rel']),'time':int(m['time']),'side':m['side'],'shares':float(m['shares']),'price':float(m['price'])
                            } for m in self.authHist[int(st.get('authHistIndex',len(self.authHist))):]],
                        })
                temp_truth[o['side']]+=inc
        super().process(t)
        self.driftEvents.extend(candidates)


def classify(e):
    ds=e['deltaSameSideBeforeFirstFill'];do=e['deltaOppSideBeforeFirstFill'];gap=e['submitRepairGap']
    submit_carriers=e.get('submit',{}).get('carriers',[])
    same_existing=[c for c in submit_carriers if c.get('side')==e['side'] and (c.get('leavesQty',0)>EPS or c.get('localPending'))]
    canceled_same=[c for c in submit_carriers if c.get('side')==e['side'] and c.get('cancelRequested')]
    if ds>EPS and ds-do>max(gap,0)+EPS:
        if same_existing:return 'INTERVENING_SAME_SIDE_EXISTING_CARRIER'
        if canceled_same:return 'INTERVENING_SAME_SIDE_CANCEL_INFLIGHT'
        return 'INTERVENING_SAME_SIDE_FILL_OR_UNTRACKED_CARRIER'
    if e.get('cancelRequestedBeforeFirstFill'):return 'OWN_CARRIER_CANCEL_INFLIGHT_LATE_FILL'
    if ds>EPS:return 'INTERVENING_SAME_SIDE_INVENTORY_CHANGE'
    return 'OTHER_ROLE_DRIFT'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output',required=True);ap.add_argument('--markets',type=int,default=24);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='eth_repair_drift_trace_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']
        models,off1,off2=joblib.load(a.dagger_cache)
        life=v3.LifecycleRuntime(Path(a.lifecycle_model))
        test=[r for r in cohort if r['split']!='TRAIN40'];n=min(int(a.markets),len(test));idx=sorted(set(int(x) for x in np.linspace(0,len(test)-1,n)));selected=[test[i] for i in idx]
        events=[];market_rows=[]
        for i,cr in enumerate(selected,1):
            sim=TraceSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,life,0,0)
            try:r=sim.run_exam_v2(models,cr['winner'])
            finally:sim.close()
            for e in sim.driftEvents:e['marketId']=int(cr['marketId']);e['classification']=classify(e);events.append(e)
            market_rows.append({'marketId':int(cr['marketId']),'repairToExpand':int(r['repairToExpandAtFirstFill']),'driftTraceEvents':len(sim.driftEvents),'actualFillEvents':int(r['actualFillEvents'])})
            if i%8==0:print(json.dumps({'progress':i,'of':len(selected),'repairToExpand':sum(x['repairToExpand'] for x in market_rows),'traced':len(events)}),flush=True)
        counts={}
        for e in events:counts[e['classification']]=counts.get(e['classification'],0)+1
        out={
            'version':'ETH_REPAIR_CARRIER_DRIFT_TRACE_V1','researchOnly':True,'scenario':'CONTROL_0MS',
            'selectedMarketIds':[int(r['marketId']) for r in selected],
            'repairToExpandTotal':sum(x['repairToExpand'] for x in market_rows),'traceEvents':len(events),'classificationCounts':counts,
            'allDriftAtZeroObservationLag':True,'fixedDelayCannotBeSoleRootCause':bool(events),
            'marketRows':market_rows,'events':events,
            'boundary':['V3 controller unchanged','CONTROL 0ms fill observation and ack release only','No parameter tuning or retraining','No winner/PnL used for classification','Consumed development evidence only']
        }
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'repairToExpandTotal':out['repairToExpandTotal'],'traceEvents':len(events),'classificationCounts':counts,'fixedDelayCannotBeSoleRootCause':out['fixedDelayCannotBeSoleRootCause']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
