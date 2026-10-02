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
v3b=v3f.v3b;v3c=v3f.v3c

class ProbeCoreFirstHandbackOneShotV3I(v3f.InitialProbeCoverageCoreHandbackV3F):
    def __init__(self,tape):
        super().__init__(tape);self.oneshot_consumed=False
    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        eligible=bool((not self.oneshot_consumed) and pnd is not None and L is not None and L.get('crossLotAtSubmit') and str(L.get('role'))=='ECONOMIC_CORE' and self._origin_is_pure_probe(L))
        if eligible:
            self.oneshot_consumed=True
            self.q_counter['probeCoreFirstHandbackOneShot']+=1
            self.q_events.append({'event':'QTY_FIFO_PROBE_CORE_FIRST_HANDBACK_ONESHOT','t':int(t),'originResponsibilityId':int(L['originResponsibilityId']),'targetExpandSide':str(L['targetExpandSide'])})
            return v3c.CrossLotActiveHandbackV3C._submit_protected_active_qty(self,t,qv)
        return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)
    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['quantityResponsibilityLadderV3I']='PROBE_CORE_FIRST_HANDBACK_ONESHOT';r['quantityLadderVersion']='V3I_PROBE_CORE_FIRST_HANDBACK_ONESHOT';r['probeCoreFirstHandbackConsumed']=self.oneshot_consumed;return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3i_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3I_PROBE_CORE_FIRST_HANDBACK_ONESHOT',ProbeCoreFirstHandbackOneShotV3I)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'oneshot':row.get('probeCoreFirstHandbackConsumed'),'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3I_PROBE_CORE_FIRST_HANDBACK_ONESHOT','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3I_PROBE_CORE_FIRST_HANDBACK_ONESHOT':agg(rows,'B_V3I_PROBE_CORE_FIRST_HANDBACK_ONESHOT')},'boundary':['one earliest pure Probe-origin cross-lot ECONOMIC_CORE handback per market','all later behavior exact V3B','causal stratification only; no runtime threshold','ordinary Pair-Core immediate; no HOLD/cooldown','exact FIFO unchanged','Pair economics only hard safety','activity-density review mandatory','no NEW24-B/no 8781/no dream fill']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
