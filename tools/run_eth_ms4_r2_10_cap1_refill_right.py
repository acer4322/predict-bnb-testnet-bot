from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
r1=r28.r1; EPS=1e-9

class Cap1RefillRightSim(r28.FanoutRoleCapacitySim):
    """R2.10: R2.8 CAP1 + responsibility-level future passive refill right before Active.
    max_slots remains 4; one extra parallel Repair satellite may be live. Active may use only
    residual debt remaining after all live reservations AND one current-book venue-min passive refill.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots); self.r210=Counter(); self.r210events=[]
    def _future_passive_min_qty(self,side:str):
        for raw in self._live_price_levels(side):
            p=float(r1.v2.kprice(raw))
            if p<=EPS: continue
            q=1.0/p
            if math.isfinite(q) and q>EPS and q<=12.0+EPS: return float(q),float(p)
        return None,None
    def _try_active_drain(self,t:int):
        while self.pendingFailure:
            ev=self.pendingFailure[0]; gen=int(ev['generation']); side=str(ev['side']); epoch=(gen,int(ev['repairProgressClock']))
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
                self.pendingFailure.popleft(); self.drainStats['STALE_EVIDENCE_DROP']+=1; continue
            if epoch in self.usedEpochs:
                self.pendingFailure.popleft(); self.drainStats['EPOCH_ALREADY_DRAINED']+=1; continue
            if self._has_live_active(): self.drainStats['ACTIVE_ALREADY_LIVE_WAIT']+=1; return False
            qv=r1.v2.base.quotes(self.book)
            if not qv or qv.get(side,{}).get('ask') is None: self.drainStats['NO_ACTIVE_ASK_WAIT']+=1; return False
            ap=float(qv[side]['ask']); aq=1.0/ap if ap>EPS else math.inf
            if not math.isfinite(aq) or aq<=EPS or aq>12.0+EPS:
                self.drainStats['BAD_ACTIVE_MIN_QTY']+=1; self.pendingFailure.popleft(); continue
            debt=float(self._scope_debt_qty()); reserved=float(self._reserved_repair_quota(side)); avail=max(0.0,debt-reserved)
            if avail+EPS<aq:
                self.drainStats['RESIDUAL_DEBT_BELOW_ACTIVE_MIN']+=1; return False
            pq,pp=self._future_passive_min_qty(side)
            if pq is None:
                self.r210['NO_FUTURE_PASSIVE_REFILL_PRICE']+=1; return False
            if avail-aq+EPS<pq:
                self.r210['PASSIVE_REFILL_RIGHT_BLOCK']+=1
                ev2={'t':int(t),'event':'CAP1_PASSIVE_REFILL_RIGHT_BLOCK','generation':gen,'repairProgressClock':epoch[1],'side':side,'debt':debt,'reservedBefore':reserved,'availableBefore':avail,'activeQty':aq,'passiveRefillQty':pq,'passiveRefillPrice':pp,'availableAfterActive':max(0.0,avail-aq)}
                self.r210events.append(ev2); self.slot_history.append(ev2); return False
            before=float(self._physical_floor()); after=float(self._candidate_alone_floor(side,ap,aq))
            if after<=before+EPS:
                self.drainStats['ACTIVE_NOT_FLOOR_IMPROVING']+=1; self.pendingFailure.popleft(); continue
            if self._submit_active(t,side,'SATELLITE_REPAIR',aq,0.0,{'rank':None,'depth':None}):
                self.usedEpochs.add(epoch); self.pendingFailure.popleft(); self.drainStats['ACTIVE_DRAIN_SUBMIT']+=1; self.r210['ACTIVE_WITH_REFILL_RIGHT']+=1
                x={'t':int(t),'event':'FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT','sourceKey':ev['sourceKey'],'sourceRole':ev['sourceRole'],'generation':gen,'repairProgressClock':epoch[1],'side':side,'activePrice':ap,'qty':aq,'debt':debt,'reservedBefore':reserved,'floorBefore':before,'candidateFloor':after,'passiveRefillQty':pq,'passiveRefillPrice':pp,'availableAfterActive':avail-aq}
                self.drainEvents.append(x); self.slot_history.append(x); return True
            self.drainStats['ACTIVE_SUBMIT_BLOCKED']+=1; return False
        return False
    def run_r210(self,w):
        r=super().run_cap(w); r['r210Stats']=dict(self.r210); r['r210Events']=self.r210events[:1000]; return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='ms4_r210_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}; rows=[]
        for mid in mids:
            cr=co[mid]; tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:r0=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=Cap1RefillRightSim(tape,4)
            try:r=sim.run_r210(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R210_CAP1_REFILL_RIGHT','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'cap1':{'sub':r0['submits'],'fills':r0['fillEvents'],'pnl':r0['pnlDiagnosticOnly'],'floor':r0['floor'],'active':r0.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)},'r210':{'sub':r['submits'],'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'active':r.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0),'fan':r['parallelPassiveFanoutSubmits'],'stats':r['r210Stats']},'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'}; C={r['marketId']:r for r in rows if r['cell']=='MS4_R210_CAP1_REFILL_RIGHT'}
        cmp=[]
        for m in mids:
            b,c=B[m],C[m]; cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'submitDelta':c['submits']-b['submits'],'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'activeDelta':c.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)-b.get('failureEvidenceActiveDrainStats',{}).get('ACTIVE_DRAIN_SUBMIT',0)})
        correctness=all(float(C[m].get('unauthorizedOverflowQty',0) or 0)<=EPS and float(C[m].get('repairQuotaExcessMax',0) or 0)<=EPS for m in mids); anti=all(C[m]['fillEvents']>=.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_10_CAP1_REFILL_RIGHT_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correctness,'antiCollapse50pctPass':anti},'boundary':['max_slots remains 4','R2.8 CAP1 fanout occupancy preserved','Active may spend only unreserved Repair debt after preserving one current-book venue-min future passive refill right','live Repair reservations remain fully authoritative','no fixed time/tick/PnL threshold','Overflow/debt/qty authority unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
