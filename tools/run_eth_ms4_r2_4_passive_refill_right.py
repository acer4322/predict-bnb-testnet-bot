from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_3_active_credit_remainder_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r23',_STAGED);r23=importlib.util.module_from_spec(sp);sp.loader.exec_module(r23)
else:
    import tools.run_eth_ms4_r2_3_active_credit_remainder_ablation as r23
r22=r23.r22;r1=r23.r1;EPS=1e-9

class PassiveRefillRightSim(r22.FailureEvidenceActiveDrainSim):
    """MS4-R2.4: Active child may spend only residual Repair debt that still leaves one
    future venue-min passive Repair refill right, in addition to all current live reservations.
    No new total Repair/risk authority is created.
    """
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots,['SATELLITE_REPAIR']);self.r24=Counter();self.r24events=[]

    def _future_passive_min_qty(self,side:str):
        # Refill may reuse a price after current live carriers terminal, so do not exclude used prices.
        levels=[float(r1.v2.kprice(p)) for p in self._live_price_levels(side)]
        for p in levels:
            if p<=EPS: continue
            q=1.0/p
            if math.isfinite(q) and q>EPS and q<=12.0+EPS:
                return float(q),float(p)
        return None,None

    def _try_active_drain(self,t:int):
        while self.pendingFailure:
            ev=self.pendingFailure[0];gen=int(ev['generation']);side=str(ev['side']);epoch=(gen,int(ev['repairProgressClock']))
            if self.scopeSide is None or int(self.scopeGeneration)!=gen or side!=self._repair_side():
                self.pendingFailure.popleft();self.drainStats['STALE_EVIDENCE_DROP']+=1;continue
            if epoch in self.usedEpochs:
                self.pendingFailure.popleft();self.drainStats['EPOCH_ALREADY_DRAINED']+=1;continue
            if self._has_live_active():self.drainStats['ACTIVE_ALREADY_LIVE_WAIT']+=1;return False
            qv=r1.v2.base.quotes(self.book)
            if not qv or qv.get(side,{}).get('ask') is None:self.drainStats['NO_ACTIVE_ASK_WAIT']+=1;return False
            ap=float(qv[side]['ask']);aq=1.0/ap if ap>EPS else math.inf
            if not math.isfinite(aq) or aq<=EPS or aq>12.0+EPS:
                self.drainStats['BAD_ACTIVE_MIN_QTY']+=1;self.pendingFailure.popleft();continue
            debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
            if avail+EPS<aq:
                self.drainStats['RESIDUAL_DEBT_BELOW_ACTIVE_MIN']+=1;return False
            pq,pp=self._future_passive_min_qty(side)
            if pq is None:
                self.r24['NO_FUTURE_PASSIVE_REFILL_PRICE']+=1;return False
            # Preserve one unreserved future passive refill right after the Active child.
            if avail-aq+EPS<pq:
                self.r24['PASSIVE_REFILL_RIGHT_BLOCK']+=1
                self.r24events.append({'t':int(t),'event':'PASSIVE_REFILL_RIGHT_BLOCK','generation':gen,'repairProgressClock':epoch[1],'side':side,'debt':debt,'reservedBefore':reserved,'availableBefore':avail,'activeQty':aq,'passiveRefillQty':pq,'passiveRefillPrice':pp,'availableAfterActive':max(0.0,avail-aq)})
                return False
            before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,aq))
            if after<=before+EPS:
                self.drainStats['ACTIVE_NOT_FLOOR_IMPROVING']+=1;self.pendingFailure.popleft();continue
            if self._submit_active(t,side,'SATELLITE_REPAIR',aq,0.0,{'rank':None,'depth':None}):
                self.usedEpochs.add(epoch);self.pendingFailure.popleft();self.drainStats['ACTIVE_DRAIN_SUBMIT']+=1;self.r24['ACTIVE_WITH_REFILL_RIGHT']+=1
                x={'t':int(t),'event':'FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT','sourceKey':ev['sourceKey'],'sourceRole':ev['sourceRole'],'generation':gen,'repairProgressClock':epoch[1],'side':side,'activePrice':ap,'qty':aq,'debt':debt,'reservedBefore':reserved,'floorBefore':before,'candidateFloor':after,'passiveRefillQty':pq,'passiveRefillPrice':pp,'availableAfterActive':avail-aq}
                self.drainEvents.append(x);self.slot_history.append(x);return True
            self.drainStats['ACTIVE_SUBMIT_BLOCKED']+=1;return False
        return False

    def run_r24(self,w):
        r=super().run_r22(w);r['r24Stats']=dict(self.r24);r['r24Events']=self.r24events[:500];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r24_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r22.FailureEvidenceActiveDrainSim(tape,4,['SATELLITE_REPAIR'])
            try:r0=ctl.run_r22(cr['winner'])
            finally:ctl.close()
            sim=PassiveRefillRightSim(tape,4)
            try:r=sim.run_r24(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R22_SATELLITE_ONLY_CONTROL','winnerPostHocOnly':cr['winner'],**r0},{'marketId':mid,'cell':'MS4_R24_PASSIVE_REFILL_RIGHT','winnerPostHocOnly':cr['winner'],**r}]
            print(json.dumps({'marketId':mid,'ctlF':r0['fillEvents'],'candF':r['fillEvents'],'ctlP':r0['pnlDiagnosticOnly'],'candP':r['pnlDiagnosticOnly'],'ctlFloor':r0['floor'],'candFloor':r['floor'],'activeSubs':r['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'r24':r['r24Stats'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='R22_SATELLITE_ONLY_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R24_PASSIVE_REFILL_RIGHT'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeSubmits':n[m]['failureEvidenceActiveDrainStats'].get('ACTIVE_DRAIN_SUBMIT',0),'r24Stats':n[m]['r24Stats']})
        out={'version':'MS4_R2_4_PASSIVE_REFILL_RIGHT_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapse50pctPass':all(n[m]['fillEvents']>=.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['control is R2.2 SATELLITE-only failure-evidence Active drain','Active child uses only unreserved authoritative Repair debt','after Active reservation, enough unreserved debt must remain for one current-live-book venue-min passive SATELLITE_REPAIR refill','current passive reservations remain fully reserved','no fixed seconds/ticks/PnL threshold','no new responsibility/risk/quantity authority','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
