from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,math
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_rolling_reanchor_two_slot_repair_1946475 as prev
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
EPS=1e-9;MID=1946475;pe=base.pe
STATIC_BASELINE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'repairParentBirths':1,'repairParentCompletions':0,'passiveRepairFills':1,'activeRepairFillQty':1.639344262295082}

class CapacityContractionRepeatedRollingHFT(prev.RollingRepairTwoSlotHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.slotCeiling=2
        self.pendingRollingCancel=None
        self.repeatedRollingEvents=[]
        self.repeatedRollingSubmits=0
        self.repeatedRollingFills=0.0
        self._seenByKey={}
        self.contractionEvents=0
        self.reanchorCycles=0

    def _managed_live(self,t):
        self._rollingNow=int(t);self._refresh_carrier_ledger(int(t))
        rows=[]
        for key in list(dict.fromkeys(self.partitionKeys)):
            e=getattr(self,'carrierLedger',{}).get(str(key),{});o=getattr(self,'orders',{}).get(str(key),{})
            if bool(e.get('terminalConfirmed')):continue
            if str(e.get('objectiveRole') or '').upper()!='REPAIR':continue
            lane=str(e.get('lane') or '').upper()
            if 'ACTIVE' in lane:continue
            rem=max(0.0,float(e.get('submittedQty') or o.get('qty') or 0.0)-float(e.get('actualFilled') or 0.0))
            if rem<=EPS:continue
            rows.append({'key':str(key),'entry':e,'side':str(e.get('side') or o.get('side') or '').upper(),'price':float(o.get('price') or e.get('price') or 0.0),'remaining':rem,'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId')})
        return rows

    def _scan_managed_fills(self,t):
        self._refresh_carrier_ledger(int(t))
        first_two=set(self.partitionKeys[:2])
        for key in list(dict.fromkeys(self.partitionKeys)):
            e=getattr(self,'carrierLedger',{}).get(str(key),{})
            now=float(e.get('actualFilled') or 0.0);old=float(self._seenByKey.get(str(key),0.0))
            if now>old+EPS:
                inc=now-old;self._seenByKey[str(key)]=now
                if str(key) not in first_two:self.repeatedRollingFills+=inc
                self.repeatedRollingEvents.append({'t':int(t),'event':'REPEATED_ROLLING_FILL','key':str(key),'incQty':inc,'cumQty':now})

    def _structural_slot_capacity(self,pid,side,best):
        debt=float(self._parent_debt_now(int(pid)))
        legal=1.0/float(best) if float(best)>EPS else math.inf
        raw=int(math.floor((debt+EPS)/legal)) if math.isfinite(legal) and legal>EPS else 0
        return debt,legal,max(0,min(2,raw))

    def _request_cancel(self,t,row,reason):
        if self._cancel_key(int(t),row['key']):
            self.pendingRollingCancel={'key':row['key'],'side':row['side'],'parentId':int(row['parentId']),'objectiveId':row['objectiveId'],'reason':reason,'oldPrice':row['price']}
            self.repeatedRollingEvents.append({'t':int(t),'event':'REPEATED_ROLLING_CANCEL_REQUEST','key':row['key'],'side':row['side'],'parentId':int(row['parentId']),'oldPrice':row['price'],'reason':reason,'slotCeiling':self.slotCeiling})
            return True
        return False

    def _submit_replacement(self,t,meta,rows,best):
        olde=getattr(self,'carrierLedger',{}).get(meta['key'],{})
        if not bool(olde.get('terminalConfirmed')):return False
        pid=int(meta['parentId']);side=str(meta['side']).upper();oid=meta.get('objectiveId')
        if oid is None:return False
        debt,legal,structural=self._structural_slot_capacity(pid,side,best)
        if structural<self.slotCeiling:
            old=self.slotCeiling;self.slotCeiling=structural;self.contractionEvents+=1
            self.repeatedRollingEvents.append({'t':int(t),'event':'SLOT_CAPACITY_CONTRACTION','parentId':pid,'side':side,'oldCeiling':old,'newCeiling':self.slotCeiling,'authoritativeDebt':debt,'legalQtyAtBest':legal,'reason':'DEBT_NO_LONGER_SUPPORTS_PRIOR_VENUE_MIN_SLOT_COUNT'})
        live=[r for r in rows if r['key']!=meta['key']]
        if len(live)>=self.slotCeiling:
            self.repeatedRollingEvents.append({'t':int(t),'event':'REPEATED_ROLLING_NO_REPLACEMENT','oldKey':meta['key'],'reason':'CONTRACTED_CAPACITY_ALREADY_OCCUPIED','liveCount':len(live),'slotCeiling':self.slotCeiling})
            self.pendingRollingCancel=None;return False
        if self.slotCeiling<=0:
            self.repeatedRollingEvents.append({'t':int(t),'event':'REPEATED_ROLLING_NO_REPLACEMENT','oldKey':meta['key'],'reason':'NO_PASSIVE_SLOT_CAPACITY','slotCeiling':self.slotCeiling})
            self.pendingRollingCancel=None;return False
        self._sync_parent_occupancy();available=float(self.parentExecutionOccupancy.available(pid,debt))
        old_sub=float(olde.get('submittedQty') or 0.0);old_fill=float(olde.get('actualFilled') or 0.0);old_rem=max(0.0,old_sub-old_fill);qty=min(old_rem,available)
        row={'t':int(t),'event':'REPEATED_ROLLING_EVALUATION','oldKey':meta['key'],'side':side,'parentId':pid,'objectiveId':oid,'targetPrice':float(best),'oldRemainingQty':old_rem,'authoritativeDebt':debt,'availableQty':available,'legalQty':legal,'slotCeiling':self.slotCeiling,'otherLive':[{'key':r['key'],'price':r['price'],'remaining':r['remaining']} for r in live]}
        if qty+EPS<legal or qty<=EPS:
            row['decision']='BLOCK_NO_LEGAL_REPLACEMENT_QTY';self.repeatedRollingEvents.append(row);return False
        before=float(self._current_payoffs().get('floor') or 0.0);plans=[(r['price'],r['remaining']) for r in live]+[(float(best),qty)];after,floors=self._floor_after_buys(side,plans);row.update({'floorBefore':before,'floorAfterAllLiveOptionsFill':after,'incrementalFloors':floors,'replacementQty':qty})
        if after is None or after<before-1e-7 or any(float(x)<before-1e-7 for x in floors):
            row['decision']='BLOCK_JOINT_WORST_CASE_FLOOR_DAMAGE';self.repeatedRollingEvents.append(row);return False
        n0=int(self.n);self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='CAPACITY_CONTRACTION_REPEATED_ROLLING_REPAIR'
        prevflag=bool(getattr(self,'_allowSameSideParallelReservation',False));self._allowSameSideParallelReservation=True
        try:ok=base.InitialRepairQuotaPartitionHFT.submit(self,int(t),side,float(best),qty)
        finally:self._allowSameSideParallelReservation=prevflag
        row['submit']=bool(ok)
        if not ok:
            row['decision']='SUBMIT_FAILED';self.repeatedRollingEvents.append(row);return False
        key=f'{side}_{n0}';self.partitionKeys.append(key);self.repeatedRollingSubmits+=1;self.reanchorCycles+=1;self.pendingRollingCancel=None
        row.update({'decision':'REPEATED_ROLLING_SUBMITTED','replacementKey':key});self.repeatedRollingEvents.append(row);return True

    def _maybe_reanchor(self,t):
        self._scan_managed_fills(int(t));rows=self._managed_live(int(t))
        if self.pendingRollingCancel is not None:
            e=getattr(self,'carrierLedger',{}).get(self.pendingRollingCancel['key'],{})
            if not bool(e.get('terminalConfirmed')):return
            qv=v1.quotes(self.book)
            if not qv:return
            side=str(self.pendingRollingCancel['side']).upper();best=float(qv[side]['bid'])
            self._submit_replacement(int(t),self.pendingRollingCancel,rows,best);return
        if not rows:return
        side=rows[0]['side'];pid=rows[0]['parentId']
        if side not in ('UP','DOWN') or pid is None or any(r['side']!=side or int(r['parentId'])!=int(pid) for r in rows):return
        qv=v1.quotes(self.book)
        if not qv:return
        best=float(qv[side]['bid']);debt,legal,structural=self._structural_slot_capacity(int(pid),side,best)
        if structural<self.slotCeiling:
            old=self.slotCeiling;self.slotCeiling=structural;self.contractionEvents+=1
            self.repeatedRollingEvents.append({'t':int(t),'event':'SLOT_CAPACITY_CONTRACTION','parentId':int(pid),'side':side,'oldCeiling':old,'newCeiling':self.slotCeiling,'authoritativeDebt':debt,'legalQtyAtBest':legal,'reason':'DEBT_NO_LONGER_SUPPORTS_PRIOR_VENUE_MIN_SLOT_COUNT'})
        if len(rows)>self.slotCeiling:
            old=min(rows,key=lambda r:r['price']);self._request_cancel(int(t),old,'CAPACITY_CONTRACTION');return
        if not rows or self.slotCeiling<=0:return
        front=max(r['price'] for r in rows)
        if best<=front+EPS:return
        old=min(rows,key=lambda r:r['price']) if len(rows)>1 else rows[0]
        self._request_cancel(int(t),old,'FRONTIER_REANCHOR')

    def run_repeated(self,models,winner):
        r=base.InitialRepairQuotaPartitionHFT.run_partition(self,models,winner);self._scan_managed_fills(int(self.capEnd))
        r.update({'slotCeilingFinal':self.slotCeiling,'contractionEvents':self.contractionEvents,'reanchorCycles':self.reanchorCycles,'repeatedRollingSubmits':self.repeatedRollingSubmits,'repeatedRollingFillQty':self.repeatedRollingFills,'repeatedRollingEvents':self.repeatedRollingEvents[:600]});return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='capacity_contraction_repeated_rolling_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(CapacityContractionRepeatedRollingHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_repeated(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        gates={'twoInitialChildrenSubmitted':int(r.get('partitionApplied') or 0)>0,'capacityContractionExercised':int(r.get('contractionEvents') or 0)>0,'repeatedRollingSubmitted':int(r.get('repeatedRollingSubmits') or 0)>0,'repeatedRollingPhysicalFill':float(r.get('repeatedRollingFillQty') or 0)>EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==STATIC_BASELINE['repairParentBirths']}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='REPEATED_ROLLING_ACCOUNTING_FAIL'
        elif not gates['repeatedRollingSubmitted']:decision='REPEATED_ROLLING_NOT_MATERIALIZED'
        elif not gates['repeatedRollingPhysicalFill']:decision='REPEATED_ROLLING_SUBMIT_NO_FILL'
        else:decision='CAPACITY_CONTRACTION_REPEATED_ROLLING_PHYSICAL_PASS'
        out={'version':'CAPACITY_CONTRACTION_REPEATED_ROLLING_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'gates':gates,'slotCeilingFinal':r.get('slotCeilingFinal'),'contractionEvents':r.get('contractionEvents'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingSubmits':r.get('repeatedRollingSubmits'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'events':r.get('repeatedRollingEvents',[])[:600],'partitionFills':r.get('partitionFills') or [],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['one market 1946475','same-parent Repair multi-slot only','capacity derived from authoritative debt / current venue-min qty; max 2 in this experiment','capacity may contract but not regrow within this responsibility','event-driven only: fill/debt, cancel terminal, or live frontier invalidation','cancel-pending keeps reservation until terminal','replacements use same parent/objective/debt','joint all-live-sibling floor nonworse gate retained for causal isolation','no fixed reprice delay/tick threshold','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'gates':gates,'slotCeilingFinal':r.get('slotCeilingFinal'),'contractionEvents':r.get('contractionEvents'),'reanchorCycles':r.get('reanchorCycles'),'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'events':out['events'][:24],'partitionFills':out['partitionFills'],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
