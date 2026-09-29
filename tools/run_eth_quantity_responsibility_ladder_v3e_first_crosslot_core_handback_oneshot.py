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

class FirstCrossLotCoreHandbackOneShotV3E(v3c.CrossLotActiveHandbackV3C):
    """Causal diagnostic: only first cross-lot carrier can be handback-eligible, and only if CORE."""
    def __init__(self,tape):
        super().__init__(tape)
        self.firstCrossLotSeen=False
        self.firstCrossLotRole=None
        self.firstCrossLotHandbackConsumed=False

    def _submit_role(self,t,side,role,p,q,proj,source):
        before_n=self.n
        ok=super()._submit_role(t,side,role,p,q,proj,source)
        key=f'{side}_{before_n}'
        L=self.q_ladder
        if ok and L is not None and L.get('passiveKey')==key and bool(L.get('crossLotAtSubmit')):
            is_first=not self.firstCrossLotSeen
            if is_first:
                self.firstCrossLotSeen=True;self.firstCrossLotRole=str(L.get('role'))
                self.q_counter['firstCrossLotManagedCarriers']+=1
                self.q_events.append({'event':'QTY_FIFO_FIRST_CROSSLOT_ROLE','t':int(t),'role':self.firstCrossLotRole,'originResponsibilityId':L['originResponsibilityId'],'targetExpandSide':L['targetExpandSide'],'key':key})
            L['firstCrossLotAtSubmit']=bool(is_first)
        return ok

    def _submit_protected_active_qty(self,t,qv):
        L=self.q_ladder
        eligible=bool(L is not None and L.get('crossLotAtSubmit') and L.get('firstCrossLotAtSubmit') and str(L.get('role'))=='ECONOMIC_CORE' and not self.firstCrossLotHandbackConsumed)
        if eligible:
            self.firstCrossLotHandbackConsumed=True
            self.q_counter['firstCrossLotCoreHandbackAttempts']+=1
            return super()._submit_protected_active_qty(t,qv)
        # All SAT-first and all later cross-lot events use exact V3B Active behavior.
        return v3b.FifoAggregateResponsibilityLadderV3B._submit_protected_active_qty(self,t,qv)

    def run_qty(self,winner='__UNSCORED__'):
        r=super().run_qty(winner)
        r['quantityResponsibilityLadderV3E']='FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT'
        r['quantityLadderVersion']='V3E_FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT'
        r['firstCrossLotSeen']=self.firstCrossLotSeen;r['firstCrossLotRole']=self.firstCrossLotRole;r['firstCrossLotHandbackConsumed']=self.firstCrossLotHandbackConsumed
        return r

def agg(rows,cell):return v3b.agg(rows,cell)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];op=Path(a.output)
    if op.exists():ap.error('do not overwrite')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='qty_v3e_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            for cell,Cls in [('A_V3B_FIFO_AGGREGATE',v3b.FifoAggregateResponsibilityLadderV3B),('B_V3E_FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT',FirstCrossLotCoreHandbackOneShotV3E)]:
                sim=Cls(tape)
                try:r=sim.run_qty('__UNSCORED__')
                finally:sim.close()
                r['pnlDiagnosticOnly']=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional']);row={'marketId':mid,'cell':cell,'winnerPostHocOnly':winner,**r};rows.append(row)
                print(json.dumps({'progress':i,'marketId':mid,'cell':cell,'pnl':row['pnlDiagnosticOnly'],'floor':row['floor'],'fills':row['fillEvents'],'submits':row['submits'],'alts':row['fillSideAlternations'],'firstCrossLotRole':row.get('firstCrossLotRole'),'firstHandback':row.get('firstCrossLotHandbackConsumed'),'counters':row['quantityLadderCounters'],'ledger':row['quantityLedgerSummary']['invariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'ETH_QUANTITY_RESPONSIBILITY_LADDER_V3E_FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
         'summary':{'A_V3B_FIFO_AGGREGATE':agg(rows,'A_V3B_FIFO_AGGREGATE'),'B_V3E_FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT':agg(rows,'B_V3E_FIRST_CROSSLOT_CORE_HANDBACK_ONESHOT')},
         'boundary':['only first cross-lot managed carrier is eligible for mutation','eligible only when first cross-lot role=ECONOMIC_CORE and Passive terminal zero-fill','one ordinary submit ends handback','SAT-first and all later cross-lot events exact V3B','no HOLD/fixed wait/cooldown','exact FIFO/Pair-only/max4/<=180s/250ms risk queue unchanged','winner/PnL posthoc only','consumed development only','no NEW24-B/no 8781/no dream fill']}
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
