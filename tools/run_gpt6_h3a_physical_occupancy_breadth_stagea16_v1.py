"""H3a physical option-occupancy breadth audit on consumed H100 Stage-A16.

Branches from identical strict-past prefix:
 ZERO: no intervention.
 REPAIR_UNARMED: exact frozen Repair physical carrier, q_arm suppressed.

Research-only realistic HFT. No future/winner/PnL enters selection or treatment.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('h3trace_breadth', HERE/'run_gpt6_h3a_occupancy_suffix_first_divergence_smoke4_v1.py')
h3=importlib.util.module_from_spec(sp);sp.loader.exec_module(h3)
h4=h3.h4; EPS=h4.EPS
BRANCHES=('ZERO','REPAIR_UNARMED')

def r10(x): return round(float(x or 0.0),10)
def close(a,b,tol=1e-8): return abs(float(a)-float(b))<=tol

def carrier_view(c):
    if c is None:return None
    return {'side':str(c.get('side') or ''),'role':str(c.get('role') or ''),'price':r10(c.get('price')),'qty':r10(c.get('qty'))}

def sig_equal(a,b):
    return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))

class OccupancyBreadthFork(h3.TraceFork):
    def __init__(self,tape,state,branch):
        super().__init__(tape,state,branch); self.frozenTreatmentParity=None
    def _custom_intervention(self,t,qv):
        A=self.frozenCandidates.get('A'); exp=self.spec.get('h3aFrozenCarrier') or {}
        self.frozenTreatmentParity=(A is not None and str(A['side'])==str(exp.get('side')) and str(A['role'])==str(exp.get('role')) and close(A['price'],exp.get('price')) and close(A['qty'],exp.get('qty')))
        if self.branch=='ZERO': return []
        if A is None:return [self._submit_frozen(t,qv,A,'GPT6_H3A_OCC_BREADTH_V1')]
        U=copy.deepcopy(A);U['arm']=None
        rec=self._submit_frozen(t,qv,U,'GPT6_H3A_OCC_BREADTH_UNARMED_V1')
        if rec:rec['qArmUsed']=False
        if self.interventionLegs:self.interventionLegs[-1]['qArmUsed']=False
        return [rec]

def terminal_delta(z,u):
    return {k:float(u['terminal'][k])-float(z['terminal'][k]) for k in ('floor','best','gap','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    states=json.loads(Path(a.states).read_text(encoding='utf-8'))['states']; rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3a_occ_breadth_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';br={}
            for bn in BRANCHES:
                sim=OccupancyBreadthFork(tape,s,bn)
                try:
                    raw=sim.run_branch();term=h4.f.terminal(raw,s);lin=sim.lineage(term);ds=h3.direct_signature(lin)
                    br[bn]={'prefixT':int(s['t']),'prefixDigest':raw['prefixDigest'],'frozenTreatmentParity':bool(sim.frozenTreatmentParity),'frozenA':carrier_view(sim.frozenCandidates.get('A')),'expectedCarrier':carrier_view(s.get('h3aFrozenCarrier')),'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'directSignature':ds,'trace':sim.h3trace,'terminal':term,'lineage':lin,'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen']),'intervention':copy.deepcopy(sim.intervention)}
                finally:sim.close()
            Z=br['ZERO'];U=br['REPAIR_UNARMED'];digs={x['prefixDigest'] for x in br.values()}
            unarmed_no_ladder=(U['initialPostSubmitSnapshot'] or {}).get('qLadderRoute') is None
            checks={'prefixParity':len(digs)==1 and None not in digs,'allTriggered':all(x['triggered'] for x in br.values()),'allLedgerClean':all(x['ledgerClean'] for x in br.values()),'allMax4':all(x['max4'] for x in br.values()),'frozenTreatmentParity':all(x['frozenTreatmentParity'] for x in br.values()),'unarmedNoQLadder':unarmed_no_ladder}
            same_direct=sig_equal(Z['directSignature'],U['directSignature']);same_terminal=h3.terminal_equal(Z['terminal'],U['terminal']);fd=h3.first_divergence(Z,U)
            if same_direct and not same_terminal:cls='CLEAN_PHYSICAL_OCCUPANCY_SUFFIX_EFFECT'
            elif not same_direct:cls='EXECUTION_MEDIATED_PHYSICAL_OCCUPANCY_EFFECT'
            elif fd is not None:cls='TRANSIENT_PHYSICAL_OCCUPANCY_EFFECT_TERMINAL_CONVERGES'
            else:cls='NULL_PHYSICAL_OCCUPANCY_EFFECT'
            row={'marketId':mid,'t':int(s['t']),'checks':checks,'correctnessPass':all(checks.values()),'classification':cls,'sameDirectInterventionSignature':same_direct,'sameTerminal':same_terminal,'firstDivergence':fd,'deltaTerminal':terminal_delta(Z,U),'branches':br}
            rows.append(row);(outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'class':cls,'sameDirect':same_direct,'lagMs':None if fd is None else fd.get('lagMs'),'delta':row['deltaTerminal']},ensure_ascii=False),flush=True)
    from collections import Counter
    cc=Counter(r['classification'] for r in rows);clean=[r for r in rows if r['classification']=='CLEAN_PHYSICAL_OCCUPANCY_SUFFIX_EFFECT'];execm=[r for r in rows if r['classification']=='EXECUTION_MEDIATED_PHYSICAL_OCCUPANCY_EFFECT'];trans=[r for r in rows if r['classification']=='TRANSIENT_PHYSICAL_OCCUPANCY_EFFECT_TERMINAL_CONVERGES'];null=[r for r in rows if r['classification']=='NULL_PHYSICAL_OCCUPANCY_EFFECT']
    same_direct_rows=[r for r in rows if r['sameDirectInterventionSignature']]
    lags=[int(r['firstDivergence']['lagMs']) for r in rows if r.get('firstDivergence') is not None]
    summary={'markets':[r['marketId'] for r in rows],'n':len(rows),'correctMarkets':sum(r['correctnessPass'] for r in rows),'classificationCounts':dict(cc),'cleanEffectMarkets':[r['marketId'] for r in clean],'executionMediatedMarkets':[r['marketId'] for r in execm],'transientMarkets':[r['marketId'] for r in trans],'nullMarkets':[r['marketId'] for r in null],'sameDirectCount':len(same_direct_rows),'cleanEffectRateAll':len(clean)/len(rows) if rows else 0.0,'cleanEffectRateAmongSameDirect':len(clean)/len(same_direct_rows) if same_direct_rows else 0.0,'firstDivergenceLagMs':{'min':min(lags) if lags else None,'median':sorted(lags)[len(lags)//2] if lags else None,'max':max(lags) if lags else None}}
    breadth_support=all(r['correctnessPass'] for r in rows) and len(clean)>=4
    decision='PROMOTE_H3A_PHYSICAL_OCCUPANCY_TO_H100_BREADTH' if breadth_support else ('OCCUPANCY_EXISTS_BUT_STAGEA16_BREADTH_WEAK' if all(r['correctnessPass'] for r in rows) else 'INVALID_CORRECTNESS_FAILURE')
    payload={'version':'GPT6_H3A_PHYSICAL_OCCUPANCY_BREADTH_STAGEA16_V1_20260907','researchOnly':True,'runtimeAuthority':False,'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,'summary':summary,'decision':decision,'boundary':['first 16 chronological freeze-clean states from consumed H100 preflight','ZERO vs exact frozen Repair physical carrier with q_arm suppressed','realistic HFT/no dream fill','no future/winner/PnL/Target selection','no runtime policy authority','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':payload['allCorrectnessPass'],'decision':decision,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
