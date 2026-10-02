from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9;MID=1946475

def load_or_staged(fullname,filename):
    try:return importlib.import_module(fullname)
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);return m
load_or_staged('tools.eth_repair_modular.parent_execution_occupancy','parent_execution_occupancy.py')
load_or_staged('tools.eth_repair_modular.responsibility_transition','responsibility_transition.py')
load_or_staged('tools.eth_repair_modular.responsibility_frontier','responsibility_frontier.py')
load_or_staged('tools.eth_repair_modular.ownership_transition_guard','ownership_transition_guard.py')
load_or_staged('tools.run_eth_parent_occupancy_anchorless_parallel_ab','run_eth_parent_occupancy_anchorless_parallel_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_passive_evidence_ab','run_eth_parent_occupancy_passive_evidence_ab.py')
load_or_staged('tools.run_eth_parent_occupancy_transition_frontier_ab','run_eth_parent_occupancy_transition_frontier_ab.py')
pg=load_or_staged('tools.run_eth_parent_occupancy_prospective_guard_ab','run_eth_parent_occupancy_prospective_guard_ab.py')
try:
    from tools.eth_repair_modular.quota_preserving_carrier_ladder import CarrierLadderContext,QuotaPreservingCarrierLadderPolicyV1
except ImportError:
    qm=load_or_staged('tools.eth_repair_modular.quota_preserving_carrier_ladder','quota_preserving_carrier_ladder.py');CarrierLadderContext=qm.CarrierLadderContext;QuotaPreservingCarrierLadderPolicyV1=qm.QuotaPreservingCarrierLadderPolicyV1
pe=pg.pe

class QuotaPreservingMultiCarrierRepairHFT(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.ladderPolicy=QuotaPreservingCarrierLadderPolicyV1();self.extraCarrierKey=None;self.extraCarrierSubmittedAt=None;self.extraCarrierFillQty=0.0;self._extraSeen=0.0;self.extraCarrierChecks=0;self.extraCarrierBlocks={};self.extraCarrierEvents=[];self._extraBusy=False
    def _block(self,reason,row=None):
        self.extraCarrierBlocks[reason]=int(self.extraCarrierBlocks.get(reason,0))+1
        if row is not None:self.extraCarrierEvents.append({**row,'event':'MULTI_CARRIER_BLOCK','reason':reason})
        return False
    def _visible_prices(self,side):
        b=getattr(self,'book',{})
        if side=='UP':return [float(p) for p in sorted((b.get('bids') or {}).keys(),reverse=True)[:8]]
        if side=='DOWN':return [1.0-float(p) for p in sorted((b.get('asks') or {}).keys())[:8]]
        return []
    def _existing_passive(self,pid,side):
        rows=[]
        for key,e in getattr(self,'carrierLedger',{}).items():
            try:
                if int(e.get('parentId') or -1)!=int(pid):continue
            except Exception:continue
            if str(e.get('objectiveRole') or '').upper()!='REPAIR' or str(e.get('side') or '').upper()!=side:continue
            lane=str(e.get('lane') or '').upper()
            if lane.startswith('ACTIVE_') or 'ACTIVE_REPAIR' in lane:continue
            if bool(e.get('terminalConfirmed')):continue
            q=max(0.0,float(e.get('submittedQty') or getattr(self,'orders',{}).get(key,{}).get('qty') or 0.0)-float(e.get('actualFilled') or 0.0))
            if q<=EPS:continue
            px=float(getattr(self,'orders',{}).get(key,{}).get('price') or e.get('price') or 0.0)
            rows.append({'key':str(key),'remaining':q,'price':px,'entry':e})
        return rows
    def _maybe_extra_carrier(self,t):
        if self._extraBusy or self.extraCarrierKey is not None:return False
        self._extraBusy=True
        try:
            rp=getattr(self,'repairParent',None)
            if not isinstance(rp,dict):return False
            pid=int(rp.get('id'));side=str(rp.get('side') or '').upper()
            if side not in ('UP','DOWN'):return False
            self._refresh_carrier_ledger(int(t));self._sync_parent_occupancy()
            existing=self._existing_passive(pid,side)
            if not existing:return self._block('NO_EXISTING_PASSIVE_SIBLING')
            debt=float(self._parent_debt_now(pid));reserved=float(self.parentExecutionOccupancy.parent_reserved(pid));self.extraCarrierChecks+=1
            row={'t':int(t),'parentId':pid,'side':side,'authoritativeDebt':debt,'existingReservedQty':reserved,'existingPassive':[{'key':x['key'],'remaining':x['remaining'],'price':x['price']} for x in existing]}
            if debt<=EPS:return self._block('NO_PARENT_DEBT',row)
            used={round(float(x['price']),10) for x in existing if float(x['price'])>EPS}
            prices=[p for p in self._visible_prices(side) if p>EPS and round(float(p),10) not in used]
            if not prices:return self._block('NO_DISTINCT_VISIBLE_PRICE',row)
            d=self.ladderPolicy.evaluate(CarrierLadderContext(pid,debt,reserved,prices,1,12.0));row.update({'candidatePrices':prices,'ladderReason':d.reason,'availableQty':d.available_before})
            if not d.plans:return self._block(d.reason,row)
            plan=d.plans[0];oid=(existing[0]['entry'].get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None))
            if oid is None:return self._block('NO_REPAIR_OBJECTIVE_ID',row)
            n0=self.n;self._pendingAuthorizedRole='REPAIR';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=pid;self._pendingLane='QUOTA_PRESERVING_MULTI_CARRIER_REPAIR'
            ok=self.submit(int(t),side,float(plan.price),float(plan.qty))
            if not ok:return self._block('SUBMIT_FAILED',row)
            key=f'{side}_{n0}';self.extraCarrierKey=key;self.extraCarrierSubmittedAt=int(t)
            self.parentExecutionOccupancy.reserve(key=key,parent_id=pid,route='PASSIVE',qty=float(plan.qty))
            # Register this physical sibling in the same frozen Repair-first AllocationLedger path.
            if hasattr(self,'v84Composite'):
                self.v84Composite[key]={'key':key,'side':side,'parentId':pid,'price':float(plan.price),'submittedQty':float(plan.qty),'gapAtSubmit':debt,'fillSeen':0.0,'repairAllocated':0.0,'overflowAllocated':0.0,'overflowBornAt':None,'overflowDebt':0.0,'overflowPaid':0.0,'lane':'QUOTA_PRESERVING_MULTI_CARRIER_REPAIR'}
                if hasattr(self,'v84CompositeSubmits'):self.v84CompositeSubmits+=1
            self.extraCarrierEvents.append({**row,'event':'MULTI_CARRIER_REPAIR_SUBMIT','key':key,'price':float(plan.price),'qty':float(plan.qty),'objectiveId':oid,'reservedAfterSubmit':self.parentExecutionOccupancy.parent_reserved(pid)})
            return True
        finally:self._extraBusy=False
    def _scan_extra_fill(self,t):
        if not self.extraCarrierKey:return
        self._refresh_carrier_ledger(int(t));e=getattr(self,'carrierLedger',{}).get(self.extraCarrierKey,{})
        now=float(e.get('actualFilled') or 0.0)
        if now<=self._extraSeen+EPS:return
        inc=now-self._extraSeen;self._extraSeen=now;self.extraCarrierFillQty+=inc;self.extraCarrierEvents.append({'t':int(t),'event':'MULTI_CARRIER_REPAIR_FILL','key':self.extraCarrierKey,'incQty':inc,'cumQty':now})
    def process(self,t):
        out=super().process(t);self._scan_extra_fill(int(t));self._maybe_extra_carrier(int(t));return out
    def run_multi(self,models,winner):
        r=self.run_guard(models,winner);r.update({'extraCarrierKey':self.extraCarrierKey,'extraCarrierSubmittedAt':self.extraCarrierSubmittedAt,'extraCarrierFillQty':self.extraCarrierFillQty,'extraCarrierChecks':self.extraCarrierChecks,'extraCarrierBlocks':self.extraCarrierBlocks,'extraCarrierEvents':self.extraCarrierEvents[:400]});return r

def slim(r):return {'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0),'floor':float(r.get('floor') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'passiveRepairFills':int(r.get('v38PassiveRepairFillEvents') or 0),'activeRepairFillQty':float(r.get('parallelRepairActiveFillQty') or r.get('v36ActiveFillQty') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='multicarrier_behavior_1946475_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'MULTI_CARRIER_BEHAVIOR_1946475','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'MULTI_CARRIER_BEHAVIOR_1946475_START'}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        b=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:br=b.run_guard(models,cr['winner']);bcons,bbound,bparents=pe.alloc(b,br)
        finally:b.close()
        c=pe.make(QuotaPreservingMultiCarrierRepairHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:rr=c.run_multi(models,cr['winner']);cons,bound,parents=pe.alloc(c,rr);occ={str(pid):c.parentExecutionOccupancy.describe_parent(pid, float(c._parent_debt_now(pid))) for pid in c.parentExecutionOccupancy.carriers and set(x.parent_id for x in c.parentExecutionOccupancy.carriers.values())}
        finally:c.close()
        ss=pe.safety(rr);safe=all(float(v or 0)<=EPS for v in ss.values());occupancy_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True
        extra_fill=float(rr.get('extraCarrierFillQty') or 0.0);bcmp=slim(br);ccmp=slim(rr);parent_birth_same=ccmp['repairParentBirths']==bcmp['repairParentBirths']
        gates={'baselineSafe':bool(bcons and bbound),'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occupancy_bound,'extraCarrierAdmissionExercised':int(rr.get('extraCarrierChecks') or 0)>0,'extraCarrierSubmitted':bool(rr.get('extraCarrierKey')),'extraCarrierActualFill':extra_fill>EPS,'noNewRepairParent':parent_birth_same}
        if not safe or not cons or not bound or not occupancy_bound or not parent_birth_same:decision='MULTI_CARRIER_ACCOUNTING_FAIL'
        elif not rr.get('extraCarrierKey'):decision='NO_DISTINCT_CARRIER_ADMISSION'
        elif extra_fill<=EPS:decision='MULTI_CARRIER_SUBMIT_NO_FILL'
        else:decision='MULTI_CARRIER_EXECUTION_PASS'
        out={'version':'QUOTA_PRESERVING_MULTI_CARRIER_REPAIR_BEHAVIOR_1946475_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baseline':bcmp,'candidate':ccmp,'delta':{'pnlDiagnosticOnly':ccmp['pnlDiagnosticOnly']-bcmp['pnlDiagnosticOnly'],'floor':ccmp['floor']-bcmp['floor'],'fills':ccmp['fills']-bcmp['fills'],'passiveRepairFills':ccmp['passiveRepairFills']-bcmp['passiveRepairFills']},'extraCarrier':{'key':rr.get('extraCarrierKey'),'submittedAt':rr.get('extraCarrierSubmittedAt'),'fillQty':extra_fill,'checks':rr.get('extraCarrierChecks'),'blocks':rr.get('extraCarrierBlocks')},'extraCarrierEvents':rr.get('extraCarrierEvents',[])[:240],'allocationParents':parents,'occupancyParents':occ,'safety':ss,'boundary':['one market 1946475','one extra passive Repair sibling max','same parent/objective/debt; no new economic responsibility','distinct strict-past visible passive price','parent occupancy quota hard bound','AllocationLedger V2 frozen Repair-first accounting','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json' if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':bcmp,'candidate':ccmp,'delta':out['delta'],'extraCarrier':out['extraCarrier'],'events':out['extraCarrierEvents'][:12],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
