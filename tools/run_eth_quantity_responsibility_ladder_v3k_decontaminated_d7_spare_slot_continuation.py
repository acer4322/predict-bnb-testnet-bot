from __future__ import annotations
import argparse,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

v3=v3b.v3;base=v3b.base;EPS=v3b.EPS

class DecontaminatedD7SpareSlotContinuation(v3b.FifoAggregateResponsibilityLadderV3B):
    """Pair-Core decontamination of historical D7 same-receipt parallelism.

    The inherited protected Active is submitted first, unchanged.  Only when that
    Active is a pure-PROBE-origin cross-lot ECONOMIC_CORE carrier and a real max4
    slot remains do we allow one frozen ordinary Pair-Core open in the same receipt.
    No old quota/Floor/recoverability/scope gate is restored and max_slots is never raised.
    """
    def __init__(self,tape):
        super().__init__(tape)
        self.dk_checks=0;self.dk_spare=0;self.dk_ordinary_submits=0;self.dk_events=[]

    def _qualifying_context(self,L):
        if L is None or str(L.get('role'))!='ECONOMIC_CORE':return None
        try: lot=self._lot_by_id(int(L['originResponsibilityId']))
        except Exception:return None
        mix=(lot or {}).get('sourceRoleMix') or {}
        support={str(k) for k,v in mix.items() if float(v)>EPS}
        old=float(L.get('oldestRemainingAtSubmit') or 0.0)
        qty=float(L.get('passiveQty') or 0.0)
        agg=float(L.get('targetOutstandingAtSubmit') or 0.0)
        if support!={'PROBE_CORE'} or not (old+EPS<qty<=agg+EPS):return None
        return {'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'repairSide':str(L['side']),'role':str(L['role']),'oldestRemainingAtSubmit':old,'passiveQty':qty,'aggregateAtSubmit':agg}

    def _open_one_option(self,t,qv,end):
        if self.q_pending_active is None:
            return super()._open_one_option(t,qv,end)

        L=self.q_ladder
        ctx=self._qualifying_context(L)
        # Reproduce inherited V3 pending-Active timing exactly.
        if int(end)-int(t)<=base.v2.NO_NEW_EXPOSURE_MS:
            self._complete_carrier(t,'ACTIVE_BLOCKED_LATE_180S');self.q_pending_active=None
        elif self._submit_protected_active_qty(t,qv):
            self.q_pending_active=None
            if ctx is not None:
                self.dk_checks+=1
                slots_after=len(self.slot_key);spare=max(0,self.max_slots-slots_after)
                ev={'event':'DECONTAM_D7_POST_ACTIVE_CAPACITY','t':int(t),**ctx,'slotsAfterActive':slots_after,'spareAfterActive':spare,'maxSlots':self.max_slots,'activeKey':(self.q_ladder or {}).get('activeKey')}
                if spare>0:
                    self.dk_spare+=1
                    before_sub=int(self.submits);before_hist=len(self.slot_history)
                    # Intentionally call the frozen Pair-Core ordinary scheduler, not the quantity-ladder armer.
                    base.MinimalPairRoleSim._open_one_option(self,t,qv,end)
                    delta=int(self.submits)-before_sub
                    new_submit=next((x for x in self.slot_history[before_hist:] if x.get('event')=='ROLE_SLOT_SUBMIT'),None)
                    if delta>0:self.dk_ordinary_submits+=delta
                    ev['ordinarySubmitDelta']=delta
                    if new_submit is not None:
                        ev['ordinary']={k:new_submit.get(k) for k in ['key','role','side','price','qty','slotId','source']}
                    ev['slotsAfterOrdinary']=len(self.slot_key)
                self.dk_events.append(ev)
            return
        # If Active could not submit (e.g. no free slot), preserve inherited V3B behavior exactly.
        return super()._open_one_option(t,qv,end)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['decontaminatedD7SameReceipt']=True
        r['decontamD7']={'checks':self.dk_checks,'spareChecks':self.dk_spare,'ordinarySubmits':self.dk_ordinary_submits,'events':self.dk_events}
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def parity(a,b):
    for k in ['submits','fillEvents','fillSideAlternations','upQty','downQty','buyNotional','floor','pnlDiagnosticOnly']:
        if abs(float(a.get(k) or 0)-float(b.get(k) or 0))>1e-10:return False
    return True

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3k_decontam_d7_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(cohort[mid]['winner']).upper()
            market_rows=[]
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_DECONTAM_D7_SAME_RECEIPT_SPARE',DecontaminatedD7SpareSlotContinuation)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
                row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row);market_rows.append(row)
            A,B=market_rows
            print(json.dumps({'progress':i,'marketId':mid,'parity':parity(A,B),'A':{'pnl':A['pnlDiagnosticOnly'],'floor':A['floor'],'fills':A['fillEvents'],'submits':A['submits'],'alts':A['fillSideAlternations']},'B':{'pnl':B['pnlDiagnosticOnly'],'floor':B['floor'],'fills':B['fillEvents'],'submits':B['submits'],'alts':B['fillSideAlternations'],'maxSlots':B['maxSimultaneousSlots'],'decontam':B.get('decontamD7')},'ledger':B['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    A={r['marketId']:r for r in rows if r['cell']=='A_V3B_FIFO_AGGREGATE'};B={r['marketId']:r for r in rows if r['cell']=='B_DECONTAM_D7_SAME_RECEIPT_SPARE'}
    cmp=[]
    for mid in mids:
        x=A[mid];y=B[mid];cmp.append({'marketId':mid,'parity':parity(x,y),'pnlDelta':float(y['pnlDiagnosticOnly'])-float(x['pnlDiagnosticOnly']),'floorDelta':float(y['floor'])-float(x['floor']),'fillDelta':int(y['fillEvents'])-int(x['fillEvents']),'submitDelta':int(y['submits'])-int(x['submits']),'alternationDelta':int(y['fillSideAlternations'])-int(x['fillSideAlternations']),'maxSlotsCandidate':int(y['maxSimultaneousSlots']),'checks':int((y.get('decontamD7') or {}).get('checks') or 0),'spareChecks':int((y.get('decontamD7') or {}).get('spareChecks') or 0),'ordinarySubmits':int((y.get('decontamD7') or {}).get('ordinarySubmits') or 0)})
    out={'version':'PAIR_CORE_DECONTAMINATED_D7_SAME_RECEIPT_SPARE_SLOT_SMOKE_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_DECONTAM_D7_SAME_RECEIPT_SPARE':agg(rows,'B_DECONTAM_D7_SAME_RECEIPT_SPARE')},'boundary':['historical D7/V83 mechanism decontamination, not novel architecture','protected Active first and unchanged','one frozen ordinary Pair-Core open only when actual max4 slot remains','max4 never increased','Pair economics retained','exact FIFO accounting retained','no old Floor/recoverability/quota/scopeGeneration/monetary-credit strategy gates restored','winner/PnL posthoc only','realistic HFT/no dream fill/no 8781/no NEW24-B']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'comparison':cmp},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
