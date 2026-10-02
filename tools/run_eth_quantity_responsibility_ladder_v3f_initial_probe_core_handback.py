from __future__ import annotations
import argparse,importlib.util,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3c_crosslot_active_handback.py'
if _STAGED.exists():
    spec=importlib.util.spec_from_file_location('staged_v3c_handback',_STAGED);v3c=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3c)
else:
    import tools.run_eth_quantity_responsibility_ladder_v3c_crosslot_active_handback as v3c
v3b=v3c.v3b;EPS=v3c.EPS

class InitialProbeCoverageCoreHandbackV3F(v3c.CrossLotActiveHandbackV3C):
    """Semantic role refinement: only Probe-origin cross-lot ECONOMIC_CORE gives Active authority back."""
    def _origin_is_pure_probe(self,L):
        if L is None:return False
        lot=self._lot_by_id(int(L['originResponsibilityId']))
        if lot is None:return False
        mix=lot.get('sourceRoleMix') or {}
        support={str(k) for k,v in mix.items() if float(v)>EPS}
        return support=={'PROBE_CORE'}

    def _submit_protected_active_qty(self,t,qv):
        L=self.q_ladder
        eligible=bool(L is not None and L.get('crossLotAtSubmit') and str(L.get('role'))=='ECONOMIC_CORE' and self._origin_is_pure_probe(L))
        if eligible:
            self.q_counter['semanticInitialProbeCoreHandbackAttempts']+=1
            self.q_events.append({'event':'QTY_FIFO_INITIAL_PROBE_CORE_ACTIVE_HANDBACK_ELIGIBLE','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'sourceRoleMix':dict((self._lot_by_id(int(L['originResponsibilityId'])) or {}).get('sourceRoleMix') or {})})
            return super()._submit_protected_active_qty(t,qv)
        return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3F']='INITIAL_PROBE_COVERAGE_CORE_HANDBACK'
        r['quantityLadderVersion']='V3F_INITIAL_PROBE_COVERAGE_CORE_HANDBACK'
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];op=Path(a.output)
    if op.exists():ap.error('do not overwrite')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3f_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3F_INITIAL_PROBE_CORE_HANDBACK',InitialProbeCoverageCoreHandbackV3F)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'roles':row.get('roleFills'),'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3F_INITIAL_PROBE_CORE_HANDBACK','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
         'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3F_INITIAL_PROBE_CORE_HANDBACK':agg(rows,'B_V3F_INITIAL_PROBE_CORE_HANDBACK')},
         'boundary':['eligible only when cross-lot ECONOMIC_CORE origin responsibility sourceRoleMix support == {PROBE_CORE}','continuation-generated CORE and all SATELLITE_REPAIR Active exact V3B','one actual ordinary submit ends eligible handback','no HOLD/time/count/price/PnL threshold','exact FIFO accounting unchanged','Pair economics only hard safety','max4/<=180s/250ms risk queue unchanged','winner/PnL posthoc only','consumed development only','no NEW24-B/no 8781/no dream fill']}
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
