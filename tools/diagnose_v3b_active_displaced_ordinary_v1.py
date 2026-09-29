from __future__ import annotations
import argparse,json,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

base=v3b.base

class DiagnosticV3B(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape)
        self.active_displacement=[]
    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active
        if pnd is not None:
            side,role,require_pair,require_budget=base.MinimalPairRoleSim._role_decision(self,qv)
            cand=base.MinimalPairRoleSim._candidate_from_levels(self,side,require_pair,require_budget)
            live=[]
            for sid,key in sorted(self.slot_key.items()):
                o=self.orders.get(key)
                if o:
                    live.append({'slotId':sid,'key':key,'role':self.key_role.get(key),'side':o.get('side'),'price':o.get('price'),'qty':o.get('qty'),'cum':o.get('cum'),'cancelRequested':o.get('cancelRequested')})
            dq=self.resp_queues[str(pnd['targetExpandSide'])]; agg_out=self._aggregate_outstanding_expand_side(pnd['targetExpandSide']); oldest=dq[0] if dq else None
            rec={'t':int(t),'pendingRepairSide':pnd.get('side'),'pendingRepairRole':pnd.get('role'),'targetExpandSide':pnd.get('targetExpandSide'),
                 'ordinaryRole':role,'ordinarySide':side,'ordinaryCandidate':({'price':float(cand[0]),'qty':float(cand[1])} if cand else None),
                 'freeSlotsBeforeActive':self.max_slots-len(self.slot_key),'liveSlotsBeforeActive':live,
                 'inventoryBeforeActive':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},'costBeforeActive':float(self.cost),
                 'aggregateOutstandingTarget':agg_out,'fifoLotCount':len(dq),'oldestRemainingQty':float(oldest['remainingQty']) if oldest else None,
                 'oldestAgeMs':int(t)-int(oldest['bornAt']) if oldest else None,'oldestShare':(float(oldest['remainingQty'])/agg_out if oldest and agg_out>1e-9 else None),
                 'passiveRouteAgeMs':(int(t)-int(self.q_ladder.get('passiveSubmittedAt'))) if self.q_ladder and self.q_ladder.get('passiveSubmittedAt') is not None else None}
            self.active_displacement.append(rec)
        ok=super()._submit_protected_active_qty(t,qv)
        if pnd is not None and self.active_displacement:
            self.active_displacement[-1]['activeSubmitted']=bool(ok)
            self.active_displacement[-1]['freeSlotsAfterAttempt']=self.max_slots-len(self.slot_key)
        return ok
    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['activeDisplacementDiagnostic']=self.active_displacement;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3b_disp_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for mid in mids:
            sim=DiagnosticV3B(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_qty('__UNSCORED__')
            finally:sim.close()
            winner=str(co[mid]['winner']).upper();r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
            rows.append({'marketId':mid,'winnerPostHocOnly':winner,**r})
            print(json.dumps({'marketId':mid,'pnl':r['pnlDiagnosticOnly'],'activeDisplacementDiagnostic':r['activeDisplacementDiagnostic']},ensure_ascii=False),flush=True)
    out={'version':'V3B_ACTIVE_DISPLACED_ORDINARY_DIAGNOSTIC_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,
         'boundary':['diagnostic only','does not change V3B action path','ordinary Pair-Core role/side/candidate sampled strict-past immediately before Active submit','no winner/PnL/future input to action','consumed markets only','no NEW24-B/no 8781']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
