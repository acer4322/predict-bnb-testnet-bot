from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_99_single_carrier_composite_handoff as r299
r296=r299.r296;r264=r299.r264;v2=r299.v2;EPS=1e-9

class SourceIntentPreservingCompositeHandoffSim(r299.SingleCarrierCompositeHandoffSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        self.r300=Counter();self.r300Events=[]
        super().__init__(tape,fanout_limit,max_slots)

    def _candidate_at_price(self,thesis,px,debt,reserved):
        px=float(v2.kprice(px))
        if px<=EPS:return None
        if px not in {float(v2.kprice(x)) for x in self._live_price_levels(thesis)}:return None
        if px in self._used_prices(thesis):return None
        available=max(0.0,float(debt)-float(reserved))
        if available<=EPS:return None
        venue=1.0/px;q=available+venue
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:return None
        sp=self._repair_split(thesis,px,q)
        if sp is None:return None
        if float(sp.get('repairQty') or 0)<=EPS or float(sp.get('overflowQty') or 0)<=EPS:return None
        return px,q,sp,venue,available

    def _try_materialize_pending(self,t,end):
        p=self.r299Pending
        if not p:return False
        terminal,status=self._source_terminal(p)
        if not terminal:
            self.r299['WAIT_SOURCE_TERMINAL']+=1;return False
        if not p.get('terminalObserved'):
            p['terminalObserved']=True;p['sourceTerminalStatus']=status;p['sourceTerminalAt']=int(t)
            self.r299['SOURCE_TERMINAL']+=1
            self.r299Events.append({'t':int(t),'event':'R299_SOURCE_TERMINAL','sourceKey':p['sourceKey'],'status':status,'generation':p['generation']})
        if self.scopeSide!=p['scopeSide'] or int(self.scopeGeneration)!=int(p['generation']):
            self.r299['ABANDON_SCOPE_CHANGED']+=1;self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'SCOPE_CHANGED',**p});self.r299Pending=None;return False
        if self.intentThesisSide!=p['thesisSide'] or self.intentThesisSide!=self._repair_side():
            self.r299['ABANDON_THESIS_NOT_REPAIR_SIDE']+=1;self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'THESIS_NOT_REPAIR_SIDE',**p});self.r299Pending=None;return False
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:
            self.r299['ABANDON_LATE_180S']+=1;self.r299Pending=None;return False
        if self._has_stale_scope_reservation():
            self.r299['WAIT_STALE_SCOPE_RESERVATION']+=1;return False
        if len(self.slot_key)>=self.max_slots:
            self.r299['WAIT_GLOBAL_SLOT']+=1;return False
        thesis=self.intentThesisSide;role=self._composite_role(thesis)
        if role is None:
            self.r299['WAIT_ROLE_CAPACITY']+=1;return False
        debt=max(0.0,float(self._scope_debt_qty()));reserved=max(0.0,float(self._reserved_repair_quota(thesis)))
        if debt-reserved<=EPS:
            self.r299['ABANDON_NO_UNRESERVED_DEBT']+=1;self.r299Pending=None;return False

        chosen=None;source_px=float(p.get('sourcePrice') or 0.0)
        chosen=self._candidate_at_price(thesis,source_px,debt,reserved)
        if chosen is not None:
            self.r300['SOURCE_PRICE_REUSED']+=1
            self.r300Events.append({'t':int(t),'event':'R300_SOURCE_PRICE_REUSED','sourceKey':p['sourceKey'],'sourcePrice':source_px,'generation':int(self.scopeGeneration)})
        else:
            self.r300['SOURCE_PRICE_NOT_CURRENTLY_LEGAL']+=1
            self.r300Events.append({'t':int(t),'event':'R300_SOURCE_PRICE_UNAVAILABLE','sourceKey':p['sourceKey'],'sourcePrice':source_px,'generation':int(self.scopeGeneration)})
            used=self._used_prices(thesis)
            for raw in self._live_price_levels(thesis):
                px=float(v2.kprice(raw))
                if px in used or px<=EPS:continue
                cand=self._candidate_at_price(thesis,px,debt,reserved)
                if cand is not None:
                    chosen=cand;self.r300['FALLBACK_CURRENT_FRONTIER']+=1;break
        if chosen is None:
            self.r299['ABANDON_NO_COMPOSITE_AFTER_CANCEL']+=1
            self.r299Events.append({'t':int(t),'event':'R299_HANDOFF_ABANDON','reason':'NO_COMPOSITE_AFTER_CANCEL',**p});self.r299Pending=None;return False

        px,q,sp,venue,available=chosen;before_n=self.n
        self._r296CompositeContext=True
        try:ok=r264.ExecutionRepresentedPreRepairReexpandSim._submit_role_v8(self,t,thesis,role,px,q,float(sp['fullFloor']),sp)
        finally:self._r296CompositeContext=False
        if not ok:
            self.r299['COMPOSITE_SUBMIT_BLOCKED_AFTER_CANCEL']+=1;return False
        key=f'{thesis}_{before_n}'
        self.r299Keys.add(key);self.r296CompositeKeys.add(key)
        meta={'key':key,'submittedAt':int(t),'generation':int(self.scopeGeneration),'thesisSide':thesis,'scopeSideAtSubmit':self.scopeSide,
              'role':role,'price':float(px),'qty':float(q),'repairQtyAuthorized':float(sp['repairQty']),'overflowQtyAuthorized':float(sp['overflowQty']),
              'overflowRiskAuthorized':float(sp['overflowRisk']),'structuralAvailableDebt':available,'structuralVenueMinOverflowQty':venue,
              'sourceKey':p['sourceKey'],'sourcePrice':source_px,'sourcePriceReused':bool(abs(float(px)-source_px)<=1e-9),'sourceTerminalStatus':status,'cancelRequestedAt':p['requestedAt']}
        self.r296CompositeMeta[key]=meta
        if role=='SATELLITE_REPAIR':self.fanoutKeys.add(key)
        self.r296['COMPOSITE_SUBMIT']+=1;self.r299['COMPOSITE_SUBMIT_AFTER_HANDOFF']+=1;self.r300['COMPOSITE_SUBMIT']+=1
        self.r299Events.append({'t':int(t),'event':'R299_COMPOSITE_SUBMIT_AFTER_HANDOFF',**meta});self.r300Events.append({'t':int(t),'event':'R300_COMPOSITE_SUBMIT',**meta});self.slot_history.append({'t':int(t),'event':'R300_COMPOSITE_SUBMIT',**meta})
        self.r299Pending=None;return True

    def run_r300(self,winner):
        r=super().run_r299(winner)
        r.update({'r300Version':'MS4_R3_00_SOURCE_INTENT_PRESERVING_COMPOSITE_HANDOFF_V1','r300Stats':dict(self.r300),'r300Events':self.r300Events[:4000],
                  'r300SourcePriceReuseCount':int(self.r300.get('SOURCE_PRICE_REUSED',0)),'r300FallbackCount':int(self.r300.get('FALLBACK_CURRENT_FRONTIER',0)),
                  'r300CorrectnessPass':bool(r.get('r299CorrectnessPass'))})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r300_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            b=r299.SingleCarrierCompositeHandoffSim(tape,1,4)
            try:br=b.run_r299(w)
            finally:b.close()
            s=SourceIntentPreservingCompositeHandoffSim(tape,1,4)
            try:r=s.run_r300(w)
            finally:s.close()
            rows += [{'marketId':m,'cell':'R299_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R300_SOURCE_INTENT','winnerPostHocOnly':w,**r}]
            d={'marketId':m,'winnerPostHocOnly':w,'intentThesis':r.get('r296IntentThesisSide'),'thesisWinnerAligned':r.get('r296IntentThesisSide')==w,
               'sourcePriceReuseCount':int(r.get('r300SourcePriceReuseCount',0)),'fallbackCount':int(r.get('r300FallbackCount',0)),
               'cancelRequests':int(r.get('r299CancelRequests',0)),'compositeSubmits':int(r.get('r299CompositeSubmits',0)),'compositeFills':int(r.get('r299CompositeFills',0)),
               'compositeRepairQty':float(r.get('r299CompositeRepairQty',0)),'compositeOverflowQty':float(r.get('r299CompositeOverflowQty',0)),
               'pnlDeltaVsR299':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDeltaVsR299':float(r['best'])-float(br['best']),
               'floorDeltaVsR299':float(r['floor'])-float(br['floor']),'gapDeltaVsR299':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),
               'fillDeltaVsR299':int(r['fillEvents'])-int(br['fillEvents']),'submitDeltaVsR299':int(r['submits'])-int(br['submits']),
               'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),
               'activeFillQty':float(r.get('ms4R2ActiveRepairFillQty',0.0)),'correct':bool(r.get('r300CorrectnessPass')),
               'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        ctrl=[x for x in cmp if x['marketId']==1946784]
        out={'version':'MS4_R3_00_SOURCE_INTENT_PRESERVING_COMPOSITE_HANDOFF_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparisonVsR299':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'sourcePriceReuseExercised':any(x['sourcePriceReuseCount']>0 for x in cmp),
                      'compositeFillExercised':any(x['compositeFills']>0 for x in cmp),'confirmedOverflowExercised':any(x['compositeOverflowQty']>EPS for x in cmp),
                      'activeStillExercised':any(x['activeFillQty']>EPS for x in cmp),'noEffect1946784':all(abs(x[k])<=1e-9 for x in ctrl for k in ['pnlDeltaVsR299','bestDeltaVsR299','floorDeltaVsR299','gapDeltaVsR299','fillDeltaVsR299','submitDeltaVsR299']) if ctrl else None},
             'boundary':['execution-intent isolation only','same trigger/cancel-confirmed barrier as R299','source price reused only if still legal current Maker price','otherwise exact R299 current-frontier fallback','physical qty=current unreserved debt+one venue-min overflow','V8 Repair-first/overflow-second','Passive/Active inherited','max4/fanout1','<=180s unchanged','no winner/Target/future runtime input','realistic HFT','consumed smoke only']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'aggregateVsR299':{'pnlDelta':sum(x['pnlDeltaVsR299'] for x in cmp),'bestDelta':sum(x['bestDeltaVsR299'] for x in cmp),'floorDelta':sum(x['floorDeltaVsR299'] for x in cmp),'fillDelta':sum(x['fillDeltaVsR299'] for x in cmp)}},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
