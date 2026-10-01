from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_2_failure_evidence_active_drain.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r22',_STAGED);r22=importlib.util.module_from_spec(sp);sp.loader.exec_module(r22)
else:
    import tools.run_eth_ms4_r2_2_failure_evidence_active_drain as r22
r1=r22.r1;v2=r1.v2;EPS=1e-9

class ParallelPassiveCoverageSim(r22.FailureEvidenceActiveDrainSim):
    """MS4-R2.6: materialize distinct-price passive Repair coverage in the same receipt.

    Economic Core remains the anchor. Once a live Core exists, unused Maker slots may reserve
    additional pure-Repair SATELLITE_REPAIR venue-min tranches at distinct live prices. Aggregate
    Repair reservation remains <= authoritative debt. Active failure-evidence drain remains the
    R2.2 residual actuator and can only use unreserved Repair debt.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots,['SATELLITE_REPAIR']);self.r26=Counter();self.fanoutKeys=set();self.r26events=[]

    def _parallel_repair_fill(self,t:int,side:str):
        made=0
        if self.scopeSide is None or side!=self._repair_side():return 0
        if self._core_for_side(side) is None:
            self.r26['NO_LIVE_CORE_ANCHOR']+=1;return 0
        while len(self.slot_key)<self.max_slots and len(self._live_role_rows(side=side))<self.max_slots:
            used=self._used_prices(side);chosen=None
            for raw in self._live_price_levels(side):
                p=float(v2.kprice(raw))
                if p in used or p<=EPS:continue
                q=1.0/p
                if q<=EPS or q>12.0+EPS:continue
                sp=self._repair_split(side,p,q)
                if sp is None:continue
                if float(sp.get('overflowQty') or 0.0)>EPS:
                    self.r26['FANOUT_OVERFLOW_NOT_ALLOWED']+=1;continue
                chosen=(p,q,sp);break
            if chosen is None:
                self.r26['NO_MORE_PURE_REPAIR_OPTION']+=1;break
            p,q,sp=chosen;before_n=self.n
            if not self._submit_role_v8(t,side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
                self.r26['FANOUT_SUBMIT_BLOCKED']+=1;break
            key=f'{side}_{before_n}';self.fanoutKeys.add(key);made+=1;self.r26['FANOUT_SUBMIT']+=1
            ev={'t':int(t),'event':'PARALLEL_PASSIVE_REPAIR_FANOUT_SUBMIT','key':key,'generation':int(self.scopeGeneration),'side':side,'price':p,'qty':q,'debt':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(side)),'liveSlotsAfter':len(self.slot_key)}
            self.r26events.append(ev);self.slot_history.append(ev)
        return made

    def _open_one_option(self,t:int,qv,end:int):
        # Freeze R2.2/MS4-R1 decision first, then fill legal residual passive Repair capacity.
        super()._open_one_option(t,qv,end)
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return
        if self.scopeSide is None:return
        self._parallel_repair_fill(t,self._repair_side())

    def run_r26(self,winner):
        r=super().run_r22(winner);filled=0;qty=0.0
        for k in self.fanoutKeys:
            a=float(self.keyRepairQuotaAuthorized.get(k,0.0));rem=float(self.keyRepairQuotaRemaining.get(k,a));x=max(0.0,a-rem)
            if x>EPS:filled+=1;qty+=x
        r['r26Stats']=dict(self.r26);r['parallelPassiveFanoutSubmits']=int(self.r26.get('FANOUT_SUBMIT',0));r['parallelPassiveFanoutFilledKeys']=int(filled);r['parallelPassiveFanoutRepairQty']=float(qty);r['r26Events']=self.r26events[:1000];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r26_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r22.FailureEvidenceActiveDrainSim(tape,4,['SATELLITE_REPAIR'])
            try:r0=ctl.run_r22(cr['winner'])
            finally:ctl.close()
            sim=ParallelPassiveCoverageSim(tape,4)
            try:r=sim.run_r26(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R22_SATELLITE_ONLY_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R26_PARALLEL_PASSIVE_COVERAGE','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'ctlSub':r0['submits'],'candSub':r['submits'],'ctlF':r0['fillEvents'],'candF':r['fillEvents'],'ctlP':r0['pnlDiagnosticOnly'],'candP':r['pnlDiagnosticOnly'],'ctlFloor':r0['floor'],'candFloor':r['floor'],'activeSubs':r['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'fanoutSub':r['parallelPassiveFanoutSubmits'],'fanoutFill':r['parallelPassiveFanoutFilledKeys'],'fanoutQty':r['parallelPassiveFanoutRepairQty'],'maxDistinct':r['maxSimultaneousDistinctPrices'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='R22_SATELLITE_ONLY_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R26_PARALLEL_PASSIVE_COVERAGE'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeSubmits':n[m]['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'fanoutSubmits':n[m]['parallelPassiveFanoutSubmits'],'fanoutFilledKeys':n[m]['parallelPassiveFanoutFilledKeys']})
        out={'version':'MS4_R2_6_PARALLEL_PASSIVE_COVERAGE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapse50pctPass':all(n[m]['fillEvents']>=.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['control is R2.2 SATELLITE-only failure-evidence Active drain','MS4-R1/R2.2 primary role decision executes first','when live ECONOMIC_CORE anchors Repair side, unused Maker slots are filled in same receipt with distinct-price pure SATELLITE_REPAIR tranches','fanout tranches reserve only authoritative unreserved Repair debt and carry zero Overflow','max_slots=4 is existing architecture capacity, not Target-derived numeric rule','Active remains residual actuator and receives no new debt/risk authority','no fixed seconds/ticks/PnL threshold','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
