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
v3b=v3c.v3b;v1=v3c.v1

class EconomicCoreCrossLotActiveHandbackV3D(v3c.CrossLotActiveHandbackV3C):
    """Role-isolated falsification: V3C handback only for cross-lot ECONOMIC_CORE.

    SATELLITE_REPAIR (and every non-CORE role) uses exact frozen V3B Active behavior.
    """
    def _submit_protected_active_qty(self,t,qv):
        L=self.q_ladder
        if L is not None and bool(L.get('crossLotAtSubmit')) and str(L.get('role'))!='ECONOMIC_CORE':
            return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)
        return super()._submit_protected_active_qty(t,qv)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3D']='ECONOMIC_CORE_CROSSLOT_ACTIVE_HANDBACK'
        r['quantityLadderVersion']='V3D_CORE_CROSSLOT_ACTIVE_HANDBACK'
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];op=Path(a.output)
    if op.exists():ap.error('do not overwrite')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3d_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3D_CORE_CROSSLOT_ACTIVE_HANDBACK',EconomicCoreCrossLotActiveHandbackV3D)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
                row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'roles':row.get('roleFills'),'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3D_CORE_CROSSLOT_ACTIVE_HANDBACK','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
         'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3D_CORE_CROSSLOT_ACTIVE_HANDBACK':agg(rows,'B_V3D_CORE_CROSSLOT_ACTIVE_HANDBACK')},
         'boundary':['only cross-lot ECONOMIC_CORE Passive zero-fill handbacks before Active','SATELLITE_REPAIR Active is exact frozen V3B path','one actual ordinary submit ends handback; no HOLD/fixed wait/cooldown','exact FIFO accounting unchanged','Pair economics only hard strategy safety','inside-spread Passive/max4/<=180s/250ms risk queue unchanged','winner/PnL posthoc only','consumed development only','no NEW24-B/no 8781/no dream fill']}
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
