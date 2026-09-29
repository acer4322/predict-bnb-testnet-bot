from __future__ import annotations
import argparse,json,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

v3=v3b.v3;v1=v3b.v1;EPS=v3b.EPS

class CrossLotActiveHandbackV3C(v3b.FifoAggregateResponsibilityLadderV3B):
    """Minimal causal mutation: cross-lot enhanced Passive miss does not immediately relay Active.

    Exact FIFO responsibility remains outstanding. Enhanced execution authority is handed back to
    frozen ordinary Pair-Core until one actual ordinary submit succeeds. This is Repair-vs-Continue,
    never Repair-vs-Hold. Non-cross-lot behavior is inherited unchanged from V3B.
    """
    def __init__(self,tape):
        super().__init__(tape)
        self.q_handback=None

    def _submit_role(self,t,side,role,p,q,proj,source):
        a=self.q_arm
        before_n=self.n
        cross=False
        if a is not None and 'passivePrice' in a and side==a.get('side') and role==a.get('role'):
            old=float(a.get('oldestRemainingAtSubmit') or 0.0)
            agg=float(a.get('aggregateRemainingAtSubmit') or 0.0)
            qty=float(a.get('passiveQty') or q)
            cross=bool(old+EPS<qty<=agg+EPS)
        hb=self.q_handback
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        key=f'{side}_{before_n}'
        if ok and self.q_ladder is not None and self.q_ladder.get('passiveKey')==key:
            self.q_ladder['crossLotAtSubmit']=bool(cross)
            if cross:
                self.q_counter['crossLotManagedPassiveSubmits']+=1
                self.q_events.append({'event':'QTY_FIFO_CROSSLOT_MANAGED_PASSIVE_MARK','t':int(t),'originResponsibilityId':self.q_ladder['originResponsibilityId'],'targetExpandSide':self.q_ladder['targetExpandSide'],'key':key,'side':side,'role':role,'price':float(p),'qty':float(q)})
        # During handback q_arm is intentionally None, so this is a genuine frozen ordinary submit.
        if ok and hb is not None and self.q_arm is None and self.q_handback is hb:
            self.q_counter['ordinaryHandbackSubmits']+=1
            self.q_events.append({'event':'QTY_FIFO_ORDINARY_HANDBACK_SUBMIT','t':int(t),'originResponsibilityId':hb['originResponsibilityId'],'targetExpandSide':hb['targetExpandSide'],'side':side,'role':role,'price':float(p),'qty':float(q),'handbackAgeMs':int(t)-int(hb['createdAt'])})
            self.q_handback=None
        return ok

    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        if pnd is not None and L is not None and bool(L.get('crossLotAtSubmit')):
            target=self._aggregate_outstanding_expand_side(L['targetExpandSide'])
            hb={'createdAt':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'repairSide':str(L['side']),'targetRemainingAtHandback':float(target),'sourcePassiveKey':str(pnd.get('sourceKey'))}
            self.q_counter['crossLotActiveHandbacks']+=1
            self.q_events.append({'event':'QTY_FIFO_CROSSLOT_ACTIVE_HANDBACK','t':int(t),**hb})
            self.q_handback=hb
            self._complete_carrier(t,'CROSSLOT_PASSIVE_ZERO_FILL_HAND_BACK_TO_ORDINARY')
            return False
        return super()._submit_protected_active_qty(t,qv)

    def _arm_for_open_qty(self,t,qv):
        hb=self.q_handback
        if hb is not None:
            target=self._aggregate_outstanding_expand_side(hb['targetExpandSide'])
            if target<=EPS:
                self.q_counter['handbackTargetSatisfiedBeforeOrdinarySubmit']+=1
                self.q_events.append({'event':'QTY_FIFO_HANDBACK_TARGET_SATISFIED','t':int(t),'originResponsibilityId':hb['originResponsibilityId'],'targetExpandSide':hb['targetExpandSide']})
                self.q_handback=None
            else:
                # Only enhanced arming is suppressed; parent _open_one_option still runs frozen ordinary Pair-Core.
                self.q_counter['handbackEnhancedArmingSuppressedReceipts']+=1
                return None
        return super()._arm_for_open_qty(t,qv)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3C']='CROSSLOT_ACTIVE_HANDBACK'
        r['quantityLadderVersion']='V3C_CROSSLOT_ACTIVE_HANDBACK'
        r['openHandbackAtEnd']=self.q_handback
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];op=Path(a.output)
    if op.exists():ap.error('do not overwrite')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3c_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3C_CROSSLOT_ACTIVE_HANDBACK',CrossLotActiveHandbackV3C)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
                row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3C_CROSSLOT_ACTIVE_HANDBACK','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
         'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3C_CROSSLOT_ACTIVE_HANDBACK':agg(rows,'B_V3C_CROSSLOT_ACTIVE_HANDBACK')},
         'boundary':['single mutation: cross-lot enhanced Passive terminal zero-fill does not immediately relay Active','authority handed back until one actual frozen ordinary Pair-Core submit succeeds','no HOLD/no fixed wait/no cooldown','exact FIFO accounting unchanged','non-cross-lot Active unchanged','Pair economics only hard strategy safety','max4/<=180s/250ms risk-queue unchanged','winner/PnL posthoc only','consumed development only','no NEW24-B/no 8781/no dream fill']}
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
