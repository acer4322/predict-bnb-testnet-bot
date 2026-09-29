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

class ProbeCoreOneHandbackPerResponsibilityV3H(v3f.InitialProbeCoverageCoreHandbackV3F):
    """Each pure Probe-origin responsibility gets at most one ordinary-continuation handback.

    After one handback has been consumed for an origin responsibility, later cross-lot Core Passive
    misses on that same responsibility use the exact V3B protected-Active path. This is lifecycle
    state, not a time/count throttle on market activity.
    """
    def __init__(self,tape):
        super().__init__(tape);self.probe_handback_used=set()
    def _submit_protected_active_qty(self,t,qv):
        pnd=self.q_pending_active;L=self.q_ladder
        eligible=bool(pnd is not None and L is not None and L.get('crossLotAtSubmit') and str(L.get('role'))=='ECONOMIC_CORE' and self._origin_is_pure_probe(L))
        if eligible:
            rid=int(L['originResponsibilityId'])
            if rid not in self.probe_handback_used:
                self.probe_handback_used.add(rid)
                self.q_counter['probeResponsibilityFirstHandbackAttempts']+=1
                self.q_events.append({'event':'QTY_FIFO_PROBE_RESPONSIBILITY_FIRST_HANDBACK','t':int(t),'originResponsibilityId':rid,'targetExpandSide':str(L['targetExpandSide'])})
                return v3c.CrossLotActiveHandbackV3C._submit_protected_active_qty(self,t,qv)
            self.q_counter['probeResponsibilityRepeatActivePreserved']+=1
            self.q_events.append({'event':'QTY_FIFO_PROBE_RESPONSIBILITY_REPEAT_ACTIVE_PRESERVED','t':int(t),'originResponsibilityId':rid,'targetExpandSide':str(L['targetExpandSide'])})
        return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)
    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner);r['quantityResponsibilityLadderV3H']='PROBE_CORE_ONE_HANDBACK_PER_RESPONSIBILITY';r['quantityLadderVersion']='V3H_PROBE_CORE_ONE_HANDBACK_PER_RESPONSIBILITY';r['probeHandbackUsedResponsibilityIds']=sorted(self.probe_handback_used);return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3h_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3H_PROBE_ONE_HANDBACK_PER_RESP',ProbeCoreOneHandbackPerResponsibilityV3H)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'counters':row['quantityLadderCounters'],'used':row.get('probeHandbackUsedResponsibilityIds'),'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3H_PROBE_CORE_ONE_HANDBACK_PER_RESPONSIBILITY','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3H_PROBE_ONE_HANDBACK_PER_RESP':agg(rows,'B_V3H_PROBE_ONE_HANDBACK_PER_RESP')},'boundary':['one handback max per pure PROBE_CORE origin responsibility','later same-responsibility Active exact V3B','ordinary Pair-Core immediate handback; no HOLD/cooldown','no PnL/Floor/winner/price/age threshold','exact FIFO unchanged','Pair economics only hard safety','activity-density review mandatory','no NEW24-B/no 8781/no dream fill']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
