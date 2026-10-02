from __future__ import annotations
import argparse,importlib.util,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback.py'
if _STAGED.exists():
    spec=importlib.util.spec_from_file_location('staged_v3f',_STAGED);v3f=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3f)
else:
    import tools.run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback as v3f
v3b=v3f.v3b;v3c=v3f.v3c;EPS=v3f.EPS
REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

class LiveRepairCapacityDedupV3G(v3f.InitialProbeCoverageCoreHandbackV3F):
    """Only dedup enhanced Probe-origin Core Active when same-side live Repair capacity already exists.

    This is execution-capacity arbitration, not a strategy safety gate. Ordinary Pair-Core continues
    immediately through the inherited handback mechanism. Zero-coverage decisions preserve V3B Active.
    """
    def _live_same_side_repair_coverage(self,repair_side):
        rows=[]
        for sid,key in list(self.slot_key.items()):
            o=self.orders.get(key)
            if o is None:continue
            if str(o.get('side'))!=str(repair_side):continue
            role=str(self.key_role.get(key) or '')
            if role not in REPAIR_ROLES:continue
            rem=max(0.0,float(o.get('qty') or 0)-float(o.get('cum') or 0))
            if rem<=EPS:continue
            rows.append({'slotId':int(sid),'key':str(key),'role':role,'side':str(repair_side),'price':float(o.get('price') or 0),'remainingQty':rem,'cancelRequested':bool(o.get('cancelRequested'))})
        return rows

    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        eligible=bool(L is not None and L.get('crossLotAtSubmit') and str(L.get('role'))=='ECONOMIC_CORE' and self._origin_is_pure_probe(L))
        if eligible and pnd is not None:
            cover=self._live_same_side_repair_coverage(pnd['side'])
            if cover:
                self.q_counter['liveRepairCapacityDedupAttempts']+=1
                self.q_counter['liveRepairCapacityCoverageCarriers']+=len(cover)
                self.q_counter['liveRepairCapacityCoverageQtyMilli']+=int(round(sum(x['remainingQty'] for x in cover)*1000))
                self.q_events.append({'event':'QTY_FIFO_LIVE_REPAIR_CAPACITY_DEDUP_ELIGIBLE','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide']),'repairSide':str(pnd['side']),'coverageQty':sum(x['remainingQty'] for x in cover),'coverageCarriers':cover})
                # Use the previously isolated handback mechanism only for this execution-coverage state.
                return v3c.CrossLotActiveHandbackV3C._submit_protected_active_qty(self,t,qv)
        # No live coverage: exact V3B Active route, bypass broad V3F semantic handback.
        return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3G']='LIVE_REPAIR_CAPACITY_DEDUP'
        r['quantityLadderVersion']='V3G_LIVE_REPAIR_CAPACITY_DEDUP'
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3g_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3G_LIVE_REPAIR_CAPACITY_DEDUP',LiveRepairCapacityDedupV3G)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3G_LIVE_REPAIR_CAPACITY_DEDUP','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3G_LIVE_REPAIR_CAPACITY_DEDUP':agg(rows,'B_V3G_LIVE_REPAIR_CAPACITY_DEDUP')},'boundary':['execution-capacity dedup only; not strategy safety','eligible only: pure PROBE_CORE origin + cross-lot ECONOMIC_CORE + live same-side ECONOMIC_CORE/SATELLITE_REPAIR remaining coverage','ordinary Pair-Core handback until one actual ordinary submit','zero live coverage exact V3B Active','no HOLD/time/count/PnL/Floor/winner threshold','exact FIFO accounting unchanged','Pair economics only hard safety','no NEW24-B/no 8781/no dream fill']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
