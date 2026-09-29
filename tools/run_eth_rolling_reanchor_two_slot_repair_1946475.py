from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
EPS=1e-9;MID=1946475;pe=base.pe
STATIC_BASELINE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'repairParentBirths':1,'repairParentCompletions':0,'passiveRepairFills':1,'activeRepairFillQty':1.639344262295082}
class RollingRepairTwoSlotHFT(base.InitialRepairQuotaPartitionHFT):
    def __init__(self,*a,**kw):
        self.rollingEvents=[];self.rollingCancelKey=None;self.rollingReplacementKey=None;self.rollingTriggered=0;self.rollingSubmitted=0;self.rollingFillQty=0.0;self._rollingSeen=0.0
        super().__init__(*a,**kw)
    def _live_partition_rows(self):
        self._refresh_carrier_ledger(int(getattr(self,'_rollingNow',0)))
        out=[]
        for key in list(self.partitionKeys):
            e=getattr(self,'carrierLedger',{}).get(str(key),{});o=getattr(self,'orders',{}).get(str(key),{})
            if bool(e.get('terminalConfirmed')):continue
            rem=max(0.0,float(e.get('submittedQty') or o.get('qty') or 0.0)-float(e.get('actualFilled') or 0.0))
            if rem<=EPS:continue
            out.append({'key':str(key),'entry':e,'side':str(e.get('side') or o.get('side') or '').upper(),'price':float(o.get('price') or e.get('price') or 0.0),'remaining':rem,'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId')})
        return out
    def _scan_replacement_fill(self,t):
        if not self.rollingReplacementKey:return
        self._refresh_carrier_ledger(int(t));e=getattr(self,'carrierLedger',{}).get(self.rollingReplacementKey,{})
        now=float(e.get('actualFilled') or 0.0)
        if now>self._rollingSeen+EPS:
            inc=now-self._rollingSeen;self._rollingSeen=now;self.rollingFillQty+=inc;self.rollingEvents.append({'t':int(t),'event':'ROLLING_REANCHOR_FILL','key':self.rollingReplacementKey,'incQty':inc,'cumQty':now})
    def _maybe_reanchor(self,t):
        self._rollingNow=int(t);self._scan_replacement_fill(int(t))
        if self.rollingReplacementKey:return
        rows=self._live_partition_rows()
        if self.rollingCancelKey is None:
            if len(rows)<2:return
            side=rows[0]['side'];pid=rows[0]['parentId']
            if not side or pid is None or any(r['side']!=side or int(r['parentId'])!=int(pid) for r in rows):return
            qv=v1.quotes(self.book)
            if not qv:return
            best=float(qv[side]['bid']);front=max(r['price'] for r in rows)
            if best<=front+EPS:return
            old=min(rows,key=lambda r:r['price'])
            if self._cancel_key(int(t),old['key']):
                self.rollingCancelKey=old['key'];self.rollingTriggered+=1
                self.rollingEvents.append({'t':int(t),'event':'ROLLING_REANCHOR_CANCEL_REQUEST','key':old['key'],'side':side,'parentId':int(pid),'oldPrice':old['price'],'frontSlotPrice':front,'strictPastBestBid':best,'reason':'ENTIRE_TWO_SLOT_SET_BEHIND_LIVE_FRONTIER'})
            return
        olde=getattr(self,'carrierLedger',{}).get(self.rollingCancelKey,{})
        if not bool(olde.get('terminalConfirmed')):return
        side=str(olde.get('side') or '').upper();pid=olde.get('parentId');oid=olde.get('objectiveId')
        if side not in ('UP','DOWN') or pid is None or oid is None:return
        qv=v1.quotes(self.book)
        if not qv:return
        target=float(qv[side]['bid'])
        others=[r for r in rows if r['key']!=self.rollingCancelKey]
        if any(abs(r['price']-target)<=1e-10 for r in others):return
        debt=float(self._parent_debt_now(int(pid)));self._sync_parent_occupancy();available=float(self.parentExecutionOccupancy.available(int(pid),debt))
        old_sub=float(olde.get('submittedQty') or 0.0);old_fill=float(olde.get('actualFilled') or 0.0);old_rem=max(0.0,old_sub-old_fill);qty=min(old_rem,available);legal=1.0/target if target>EPS else 1e99
        row={'t':int(t),'event':'ROLLING_REANCHOR_EVALUATION','oldKey':self.rollingCancelKey,'side':side,'parentId':int(pid),'objectiveId':oid,'targetPrice':target,'oldRemainingQty':old_rem,'authoritativeDebt':debt,'availableQty':available,'legalQty':legal,'otherLive':[{'key':r['key'],'price':r['price'],'remaining':r['remaining']} for r in others]}
        if qty+EPS<legal or qty<=EPS:
            row['decision']='BLOCK_NO_LEGAL_REPLACEMENT_QTY';self.rollingEvents.append(row);return
        before=float(self._current_payoffs().get('floor') or 0.0);plans=[(r['price'],r['remaining']) for r in others]+[(target,qty)];after,floors=self._floor_after_buys(side,plans);row.update({'floorBefore':before,'floorAfterAllLiveOptionsFill':after,'incrementalFloors':floors,'replacementQty':qty})
        if after is None or after<before-1e-7 or any(float(x)<before-1e-7 for x in floors):
            row['decision']='BLOCK_JOINT_WORST_CASE_FLOOR_DAMAGE';self.rollingEvents.append(row);return
        n0=int(self.n);self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=int(pid);self._pendingLane='ROLLING_REANCHOR_REPAIR_SIBLING'
        prev=bool(getattr(self,'_allowSameSideParallelReservation',False));self._allowSameSideParallelReservation=True
        try:ok=super().submit(int(t),side,target,qty)
        finally:self._allowSameSideParallelReservation=prev
        row['submit']=bool(ok)
        if not ok:row['decision']='SUBMIT_FAILED';self.rollingEvents.append(row);return
        key=f'{side}_{n0}';self.rollingReplacementKey=key;self.partitionKeys.append(key);self.rollingSubmitted+=1;row.update({'decision':'ROLLING_REANCHOR_SUBMITTED','replacementKey':key});self.rollingEvents.append(row)
    def process(self,t):
        out=super().process(t);self._maybe_reanchor(int(t));return out
    def run_rolling(self,models,winner):
        r=self.run_partition(models,winner);r.update({'rollingTriggered':self.rollingTriggered,'rollingSubmitted':self.rollingSubmitted,'rollingCancelKey':self.rollingCancelKey,'rollingReplacementKey':self.rollingReplacementKey,'rollingFillQty':self.rollingFillQty,'rollingEvents':self.rollingEvents[:300]});return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='rolling_reanchor_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(RollingRepairTwoSlotHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_rolling(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        pf=r.get('partitionFills') or [];physical=sum(float(x.get('actualFilled') or 0) for x in pf)
        gates={'twoInitialChildrenSubmitted':int(r.get('partitionApplied') or 0)>0,'rollingTriggered':int(r.get('rollingTriggered') or 0)>0,'rollingReplacementSubmitted':int(r.get('rollingSubmitted') or 0)>0,'rollingReplacementPhysicalFill':float(r.get('rollingFillQty') or 0)>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==STATIC_BASELINE['repairParentBirths']}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='ROLLING_TWO_SLOT_ACCOUNTING_FAIL'
        elif not gates['rollingReplacementSubmitted']:decision='ROLLING_REANCHOR_NOT_MATERIALIZED'
        elif not gates['rollingReplacementPhysicalFill']:decision='ROLLING_REANCHOR_SUBMIT_NO_FILL'
        else:decision='ROLLING_TWO_SLOT_PHYSICAL_LIVENESS_PASS'
        out={'version':'ROLLING_REANCHOR_TWO_SLOT_REPAIR_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'gates':gates,'rollingFillQty':r.get('rollingFillQty'),'rollingEvents':r.get('rollingEvents',[])[:300],'partitionFills':pf,'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['one market 1946475','true same-parent physical Repair siblings','reanchor only when entire live two-slot set is behind strict-past best bid','cancel old deeper sibling first; terminal confirmation required before replacement','same parent/objective/debt','joint worst-case all-live-sibling fill floor nonworse gate','no fixed reprice delay or Target tick input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'rollingFillQty':r.get('rollingFillQty'),'events':out['rollingEvents'][:12],'partitionFills':pf,'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
