from __future__ import annotations
import argparse,json,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

v3=v3b.v3;base=v3b.base;qshadow=v3b.qshadow;EPS=v3b.EPS;TICK=v3b.TICK

class BoundaryCrossingResponsibilityLadderV4(v3b.FifoAggregateResponsibilityLadderV3B):
    """Exact FIFO responsibility ledger + execution carrier may cross the current debt boundary.

    The carrier quantity is not truncated merely because current FIFO outstanding is smaller.
    Existing responsibility is paid first by the already-validated atomic ledger; any same-side
    residual becomes a new Expand responsibility. This tests responsibility/carrier separation,
    not a new PnL/Floor threshold.
    """
    def _candidate_from_levels(self,side,require_pair=True,require_budget=False):
        original=base.MinimalPairRoleSim._candidate_from_levels(self,side,require_pair,require_budget);a=self.q_arm
        if original is None or a is None or side!=a['side']:return original
        p0,q0,proj=original;qv=a['qv'];bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);price=round(ask-TICK,10)
        used={round(float(x),10) for x in self._used_prices(side)}
        if not (EPS<bid<price<ask-EPS and price not in used and price>float(p0)+EPS):
            self.q_counter['noInsideSpreadImprovement']+=1;return original
        qty=1.0/price;aggregate=self._aggregate_for_repair_side(side);oldest=float(self._oldest_for_repair_side(side)['remainingQty']) if self._oldest_for_repair_side(side) else 0.0
        if aggregate<=EPS:return original
        if qty>12.0+EPS:self.q_counter['managedQtyAbove12Fallback']+=1;return original
        crossing=max(0.0,qty-aggregate)
        if crossing>EPS:
            self.q_counter['boundaryCrossingPassiveAdmissions']+=1
            self.q_events.append({'event':'QTY_BOUNDARY_CROSSING_PASSIVE_ADMISSION','t':int(a['t']),'originResponsibilityId':a['responsibilityId'],'repairSide':side,'targetExpandSide':a['expandSide'],'aggregateDebtQty':aggregate,'carrierQty':qty,'prospectiveResidualQty':crossing})
        elif oldest+EPS<qty:self.q_counter['crossLotCarrierAdmissions']+=1
        a.update({'inheritedPrice':float(p0),'inheritedQty':float(q0),'passivePrice':float(price),'passiveQty':float(qty),'bidAtDecision':bid,'askAtDecision':ask,'oldestRemainingAtSubmit':oldest,'aggregateRemainingAtSubmit':aggregate,'passivePairSumOldest':float(a['expandPrice']+price),'prospectiveBoundaryResidualQty':crossing})
        return float(price),float(qty),proj

    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        if pnd is None or L is None:return False
        target=self._aggregate_outstanding_expand_side(pnd['targetExpandSide'])
        if target<=EPS:
            self.q_counter['activeSuppressedQueueSatisfied']+=1;self._complete_carrier(t,'QUEUE_SATISFIED_BEFORE_ACTIVE');return False
        side=pnd['side'];role=pnd['role'];ask=float(qv[side]['ask']);bid=float(qv[side]['bid']);limit=round(min(.99,ask+TICK),10)
        source_remaining=float(pnd['sourceRemainingQty']);qty=source_remaining
        if qty<=EPS:self._complete_carrier(t,'ZERO_ACTIVE_SOURCE_REMAINDER');return False
        if len(self.slot_key)>=self.max_slots:self.q_counter['activeNoFreeSlot']+=1;return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:self.q_counter['activeNoFreeSlot']+=1;return False
        crossing=max(0.0,qty-target)
        if crossing>EPS:
            self.q_counter['boundaryCrossingActiveAdmissions']+=1
            self.q_events.append({'event':'QTY_BOUNDARY_CROSSING_ACTIVE_ADMISSION','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'repairSide':side,'targetExpandSide':pnd['targetExpandSide'],'aggregateDebtQty':target,'carrierQty':qty,'prospectiveResidualQty':crossing})
        n=self.n;self.n+=1;ex=base.v2.base.ex;native_side,native_price=ex.native_order(side,limit)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,qty,ex.hbt.GTC,ex.LIMIT,False))
        except Exception:self.q_counter['activeSubmitException']+=1;return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':limit,'qty':qty,'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.placeHist.append((int(t),side,qty,limit));self.submits+=1;self.slot_key[int(free)]=key;self.key_role[key]=role;self.role_submits[role]+=1
        credit,spend=self._pair_edge(side,limit,qty);self.marginal_pair_credit[role]+=credit;self.marginal_pair_risk_spend[role]+=spend
        L.update({'route':'ACTIVE','activeKey':key,'activeSubmittedAt':int(t)});self.q_active_remainder_cancel=False;self.q_counter['managedActiveSubmits']+=1
        self.q_events.append({'event':'QTY_FIFO_MANAGED_ACTIVE_SUBMIT','t':int(t),'originResponsibilityId':pnd['originResponsibilityId'],'targetExpandSide':pnd['targetExpandSide'],'sourceKey':pnd['sourceKey'],'key':key,'side':side,'role':role,'decisionAsk':ask,'limitPrice':limit,'qty':qty,'targetAggregateRemaining':target,'prospectiveBoundaryResidualQty':crossing,'submitRc':rc})
        return True
    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['quantityResponsibilityLadderV4']='BOUNDARY_CROSSING';r['quantityLadderVersion']='V4_BOUNDARY_CROSSING';return r

def agg(rows,cell):return v3b.agg(rows,cell)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    op=Path(a.output)
    if op.exists():ap.error('do not overwrite')
    with tempfile.TemporaryDirectory(prefix='qty_v4_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_NO_CROSSING',v3b.FifoAggregateResponsibilityLadderV3B),('B_V4_BOUNDARY_CROSSING',BoundaryCrossingResponsibilityLadderV4)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'alts':row['fillSideAlternations'],'ledger':row['quantityLedgerSummary'],'counters':row['quantityLadderCounters']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V4_BOUNDARY_CROSSING','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
         'summary':{'A_V3B_NO_CROSSING':agg(rows,'A_V3B_NO_CROSSING'),'B_V4_BOUNDARY_CROSSING':agg(rows,'B_V4_BOUNDARY_CROSSING')},
         'boundary':['exact FIFO responsibility lots unchanged','existing debt paid first; carrier residual becomes new Expand responsibility via atomic ledger','carrier not truncated solely at current responsibility boundary','V3B execution/retention/terminal barrier otherwise unchanged','Pair-only hard strategy safety remains inherited','no PnL/Floor/winner threshold','consumed smoke only','no NEW24-B/no 8781/no dream fill']}
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False))
if __name__=='__main__':main()
