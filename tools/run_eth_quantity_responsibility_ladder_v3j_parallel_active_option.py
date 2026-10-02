from __future__ import annotations
import argparse,importlib.util,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED_I=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3i_probe_core_first_handback_oneshot.py'
if _STAGED_I.exists():
    spec=importlib.util.spec_from_file_location('staged_v3i',_STAGED_I);v3i=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3i)
else:
    import tools.run_eth_quantity_responsibility_ladder_v3i_probe_core_first_handback_oneshot as v3i
v3f=v3i.v3f;v3b=v3i.v3b;EPS=v3f.EPS

class ParallelActiveOptionV3J(v3b.FifoAggregateResponsibilityLadderV3B):
    """Give one ordinary capacity turn without destroying pending Active option.

    Eligible only for pure-Probe-origin cross-lot ECONOMIC_CORE pending Active. If a frozen ordinary
    Pair-Core submit succeeds on the arbitration receipt, pending Active remains live for later receipts.
    If ordinary cannot submit, immediately fall through to original V3B Active on the same receipt.
    """
    def _lot_by_id_local(self,rid):
        return next((x for x in self.quantity_responsibilities if int(x['id'])==int(rid)),None) if hasattr(self,'quantity_responsibilities') else self._lot_by_id(int(rid))
    def _origin_is_pure_probe(self,L):
        lot=self._lot_by_id(int(L['originResponsibilityId']))
        if lot is None:return False
        mix=lot.get('sourceRoleMix') or {};support={str(k) for k,v in mix.items() if float(v)>EPS}
        return support=={'PROBE_CORE'}
    def _eligible_parallel_turn(self):
        L=self.q_ladder;pnd=self.q_pending_active
        if L is None or pnd is None:return False
        if str(L.get('role'))!='ECONOMIC_CORE' or not self._origin_is_pure_probe(L):return False
        old=float(L.get('oldestRemainingAtSubmit') or 0);qty=float(L.get('passiveQty') or 0);agg=float(L.get('targetOutstandingAtSubmit') or 0)
        return old+EPS<qty<=agg+EPS
    def _open_one_option(self,t,qv,end):
        L=self.q_ladder;pnd=self.q_pending_active
        if pnd is not None and L is not None and self._eligible_parallel_turn() and not bool(L.get('parallelOrdinaryTurnGranted')):
            if int(end)-int(t)<=v3b.base.v2.NO_NEW_EXPOSURE_MS:
                return super()._open_one_option(t,qv,end)
            before=int(self.submits);save_arm=self.q_arm;self.q_arm=None
            try:
                ret=v3b.base.MinimalPairRoleSim._open_one_option(self,t,qv,end)
            finally:
                self.q_arm=save_arm
            if int(self.submits)>before:
                L['parallelOrdinaryTurnGranted']=True;L['parallelOrdinaryTurnAt']=int(t)
                self.q_counter['parallelOrdinaryTurnsGranted']+=1
                self.q_events.append({'event':'QTY_FIFO_PARALLEL_ORDINARY_TURN_GRANTED','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'repairSide':str(L['side']),'pendingSourceKey':str(pnd.get('sourceKey')),'submitsBefore':before,'submitsAfter':int(self.submits)})
                return ret
            self.q_counter['parallelOrdinaryTurnNoSubmit']+=1
            self.q_events.append({'event':'QTY_FIFO_PARALLEL_ORDINARY_TURN_NO_SUBMIT_FALLTHROUGH','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide'])})
        return super()._open_one_option(t,qv,end)
    def _submit_protected_active_qty(self,t,qv):
        L=self.q_ladder
        before=int(self.submits);ok=super()._submit_protected_active_qty(t,qv)
        if ok and L is not None and bool(L.get('parallelOrdinaryTurnGranted')):
            self.q_counter['parallelPreservedActiveSubmits']+=1
            self.q_events.append({'event':'QTY_FIFO_PARALLEL_PRESERVED_ACTIVE_SUBMIT','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'activeKey':L.get('activeKey'),'submitsBefore':before,'submitsAfter':int(self.submits),'ordinaryTurnAt':L.get('parallelOrdinaryTurnAt')})
        return ok
    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['quantityResponsibilityLadderV3J']='PARALLEL_ACTIVE_OPTION_PRESERVATION';r['quantityLadderVersion']='V3J_PARALLEL_ACTIVE_OPTION_PRESERVATION';return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3j_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        cells=[('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3I_DESTRUCTIVE_ONESHOT_HANDBACK',v3i.ProbeCoreFirstHandbackOneShotV3I),('C_V3J_PARALLEL_ACTIVE_OPTION',ParallelActiveOptionV3J)]
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in cells:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3J_PARALLEL_ACTIVE_OPTION','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{c:agg(rows,c) for c,_ in cells},'boundary':['one ordinary capacity turn can precede pending Active without destroying it','if ordinary cannot submit, same-receipt V3B Active remains','later Active still conditional on debt persistence/free slot/late boundary','no HOLD/cooldown/time threshold','exact FIFO unchanged','Pair economics only hard safety','activity-density primary mechanism gate','no NEW24-B/no 8781/no dream fill']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
