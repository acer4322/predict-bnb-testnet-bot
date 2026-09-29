from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_70_decontaminated_v28_secondary_favorable_repair as r270
r257=r270.r257; v2=r270.v2; EPS=1e-9

class SameReceiptSecondaryRepairSim(r270.DecontaminatedV28SecondaryFavorableRepairSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r272=Counter(); self.r272Events=[]; self._r272SecondaryContext=False

    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if not self._r272SecondaryContext:
            return super()._submit_role_v8(t,side,role,p,q,proj,split)
        old=getattr(self,'_last_new_receipt',None)
        same=(old==int(t))
        if same:
            self._last_new_receipt=None
            self.r272['ONE_RECEIPT_BYPASS']+=1
        try:
            ok=super()._submit_role_v8(t,side,role,p,q,proj,split)
        finally:
            # Secondary does not consume the legacy one-option strategy throttle.
            # Keep whichever pre-existing receipt state existed so frozen scheduler may still act.
            self._last_new_receipt=old
        if ok:
            self.r272['SECONDARY_PARALLEL_SUBMIT']+=1
            self.r272Events.append({'t':int(t),'event':'R272_SECONDARY_REPAIR_PARALLEL_SUBMIT',
                                    'side':side,'price':float(p),'qty':float(q),'sameReceiptBypass':bool(same),
                                    'livePassiveSlotsAfter':len(self.slot_key),'activeKeys':len(self.activeKeys),
                                    'repairDebtAfterReservation':float(self._scope_debt_qty()),
                                    'repairReservedAfter':float(self._reserved_repair_quota(side))})
        return ok

    def _try_secondary(self,t,end):
        self._r272SecondaryContext=True
        try:return super()._try_secondary(t,end)
        finally:self._r272SecondaryContext=False

    def _open_one_option(self,t,qv,end):
        # Earlier bind point: if a primary Repair from a prior receipt is already live,
        # reserve the favorable secondary option before ordinary scheduling consumes quota.
        self._try_secondary(t,end)
        # Exact frozen R2.57 scheduler (avoid R270 post-hook).
        r257.RiskFillPassiveRepairObligationSim._open_one_option(self,t,qv,end)
        # If the primary was just born on this receipt, narrow same-receipt bypass may add secondary.
        self._try_secondary(t,end)

    def process(self,t):
        before={k:float(v.get('fillQty') or 0.0) for k,v in self.secondaryMeta.items()}
        super().process(t)
        for key,x in self.secondaryMeta.items():
            now=float(x.get('fillQty') or 0.0);old=float(before.get(key,0.0))
            if now>old+EPS:
                self.r272['SECONDARY_CONFIRMED_FILL']+=1
                self.r272Events.append({'t':int(t),'event':'R272_SECONDARY_REPAIR_CONFIRMED_FILL','key':key,
                                        'fillInc':now-old,'fillCum':now,'realizedPairEdge':float(x.get('realizedPairEdge') or 0.0)})

    def run_r272(self,w):
        r=super().run_r270(w)
        correct=bool(r.get('r270CorrectnessPass')) and float(r.get('repairQuotaExcessMax',0.0))<=EPS and float(r.get('unauthorizedOverflowQty',0.0))<=EPS and int(r.get('maxSimultaneousDistinctPrices') or 0)<=4
        r.update({'r272Version':'MS4_R2_72_SAME_RECEIPT_SECONDARY_REPAIR_DECONTAMINATION_V1',
                  'r272Stats':dict(self.r272),'r272Events':self.r272Events[:3000],
                  'r272SameReceiptBypasses':int(self.r272.get('ONE_RECEIPT_BYPASS',0)),
                  'r272SecondaryParallelSubmits':int(self.r272.get('SECONDARY_PARALLEL_SUBMIT',0)),
                  'r272SecondaryConfirmedFills':int(self.r272.get('SECONDARY_CONFIRMED_FILL',0)),
                  'r272CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r272_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];cmp=[]
        for m in mids:
            w=co[m]['winner'];tape=tmp/f'{m}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=SameReceiptSecondaryRepairSim(tape,1,4)
            try:c=sim.run_r272(w)
            finally:sim.close()
            rows += [{'marketId':m,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},{'marketId':m,'cell':'R272_SECONDARY_REPAIR','winnerPostHocOnly':w,**c}]
            d={'marketId':m,'secondarySubmits':c['r270SecondarySubmits'],'secondaryFills':c['r270SecondaryFills'],
               'sameReceiptBypasses':c['r272SameReceiptBypasses'],'parallelSubmits':c['r272SecondaryParallelSubmits'],
               'secondaryPairEdge':c['r270SecondaryRealizedPairEdge'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
               'bestGt2':float(c['best'])>2.0,'floorGtMinus1':float(c['floor'])>-1.0,
               'correct':bool(c['r272CorrectnessPass']),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_72_SAME_RECEIPT_SECONDARY_REPAIR_DECONTAMINATION_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'secondarySubmitExercised':any(x['secondarySubmits']>0 for x in cmp),
                      'secondaryFillExercised':any(x['secondaryFills']>0 for x in cmp)},
             'boundary':['only R270 secondary passive Repair bypasses ONE_NEW_OPTION_PER_RECEIPT','R2.57 ordinary scheduler frozen','V8 _repair_split authoritative before every secondary submit','aggregate Repair reservation remains within authoritative debt','no Active/shared-scheduler bypass','no secondary overflow authority','max4','<=180s','realistic HFT','no future/Target/winner runtime input','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
