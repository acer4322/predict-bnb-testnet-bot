"""H3a qLadder/reservation decomposition exact fork.

Three branches from identical consumed-H100 prefixes:
 ZERO: no intervention
 REPAIR_UNARMED: exact frozen armed physical Repair carrier, but q_arm suppressed
 REPAIR_ARMED: exact same carrier with current V3B q_arm semantics

Research-only realistic HFT. No selection uses future fill/winner/PnL.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('h3trace', HERE/'run_gpt6_h3a_occupancy_suffix_first_divergence_smoke4_v1.py')
h3=importlib.util.module_from_spec(sp);sp.loader.exec_module(h3)
h4=h3.h4; EPS=h4.EPS
BRANCHES=('ZERO','REPAIR_UNARMED','REPAIR_ARMED')

def r10(x): return round(float(x or 0.0),10)
def close(a,b,tol=1e-8): return abs(float(a)-float(b))<=tol

def carrier_view(c):
    if c is None:return None
    return {'side':str(c.get('side') or ''),'role':str(c.get('role') or ''),'price':r10(c.get('price')),'qty':r10(c.get('qty'))}

def physical_state(s):
    if s is None:return None
    return {'liveSlots':int(s['liveSlots']),'liveOrders':s['liveOrders'],'repairDebtUP':r10(s['repairDebtUP']),'repairDebtDOWN':r10(s['repairDebtDOWN']),
            'responsibilityCount':int(s['responsibilityCount']),'upQty':r10(s['upQty']),'downQty':r10(s['downQty']),'cost':r10(s['cost']),
            'floor':r10(s['floor']),'best':r10(s['best']),'gap':r10(s['gap'])}

def sig_equal(a,b): return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))

class DecompFork(h3.TraceFork):
    def __init__(self,tape,state,branch):
        super().__init__(tape,state,branch); self.frozenTreatmentParity=None
    def _custom_intervention(self,t,qv):
        A=self.frozenCandidates.get('A')
        expected=self.spec.get('h3aFrozenArmedCarrier') or {}
        self.frozenTreatmentParity=(A is not None and str(A['side'])==str(expected.get('side')) and str(A['role'])==str(expected.get('role')) and close(A['price'],expected.get('price')) and close(A['qty'],expected.get('qty')))
        if self.branch=='ZERO': return []
        if A is None:return [self._submit_frozen(t,qv,A,'GPT6_H3A_Q_LADDER_DECOMP_V1')]
        if self.branch=='REPAIR_ARMED':
            return [self._submit_frozen(t,qv,A,'GPT6_H3A_Q_LADDER_DECOMP_ARMED_V1')]
        if self.branch=='REPAIR_UNARMED':
            U=copy.deepcopy(A); U['arm']=None
            rec=self._submit_frozen(t,qv,U,'GPT6_H3A_Q_LADDER_DECOMP_UNARMED_V1')
            if rec: rec['qArmUsed']=False
            if self.interventionLegs:self.interventionLegs[-1]['qArmUsed']=False
            return [rec]
        raise ValueError(self.branch)

def first_div(a,b): return h3.first_divergence(a,b)

def contrast(left,right):
    same_direct=sig_equal(left['directSignature'],right['directSignature'])
    same_terminal=h3.terminal_equal(left['terminal'],right['terminal'])
    return {'sameDirectInterventionSignature':same_direct,'sameTerminal':same_terminal,'firstDivergence':first_div(left,right),
            'leftDirectSignature':left['directSignature'],'rightDirectSignature':right['directSignature'],
            'deltaTerminal':{k:float(right['terminal'][k])-float(left['terminal'][k]) for k in ('floor','best','gap','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    states=json.loads(Path(a.states).read_text(encoding='utf-8'))['states']; rows=[]; outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3a_qarm_decomp_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; br={}
            for bn in BRANCHES:
                sim=DecompFork(tape,s,bn)
                try:
                    raw=sim.run_branch();term=h4.f.terminal(raw,s);lin=sim.lineage(term);ds=h3.direct_signature(lin)
                    br[bn]={'prefixT':int(s['t']),'prefixDigest':raw['prefixDigest'],'frozenTreatmentParity':bool(sim.frozenTreatmentParity),
                            'frozenA':carrier_view(sim.frozenCandidates.get('A')),'expectedCarrier':carrier_view(s.get('h3aFrozenArmedCarrier')),
                            'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'directSignature':ds,'trace':sim.h3trace,'terminal':term,'lineage':lin,
                            'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen']),
                            'intervention':copy.deepcopy(sim.intervention)}
                finally:sim.close()
            A=br['REPAIR_ARMED'];U=br['REPAIR_UNARMED'];Z=br['ZERO']
            arm_phys_parity=physical_state(A['initialPostSubmitSnapshot'])==physical_state(U['initialPostSubmitSnapshot'])
            qarm_gate=A['initialPostSubmitSnapshot']['qLadderRoute']=='PASSIVE' and U['initialPostSubmitSnapshot']['qLadderRoute'] is None
            digs={x['prefixDigest'] for x in br.values()}
            checks={'prefixParity':len(digs)==1 and None not in digs,'allTriggered':all(x['triggered'] for x in br.values()),
                    'allLedgerClean':all(x['ledgerClean'] for x in br.values()),'allMax4':all(x['max4'] for x in br.values()),
                    'frozenTreatmentParity':all(x['frozenTreatmentParity'] for x in br.values()),
                    'armedUnarmedImmediatePhysicalParity':arm_phys_parity,'armedPassiveUnarmedNoLadder':qarm_gate}
            arm=contrast(U,A); occ=contrast(Z,U)
            if arm['sameDirectInterventionSignature'] and not arm['sameTerminal']:arm_class='CLEAN_MANAGEMENT_ARM_SUFFIX_EFFECT'
            elif not arm['sameDirectInterventionSignature']:arm_class='EXECUTION_MEDIATED_MANAGEMENT_ARM_EFFECT'
            elif arm['firstDivergence'] is not None:arm_class='TRANSIENT_MANAGEMENT_ARM_EFFECT_TERMINAL_CONVERGES'
            else:arm_class='NULL_MANAGEMENT_ARM_BEYOND_BOOKKEEPING'
            if occ['sameDirectInterventionSignature'] and not occ['sameTerminal']:occ_class='CLEAN_PHYSICAL_OCCUPANCY_SUFFIX_EFFECT'
            elif not occ['sameDirectInterventionSignature']:occ_class='EXECUTION_MEDIATED_PHYSICAL_OCCUPANCY_EFFECT'
            elif occ['firstDivergence'] is not None:occ_class='TRANSIENT_PHYSICAL_OCCUPANCY_EFFECT_TERMINAL_CONVERGES'
            else:occ_class='NULL_PHYSICAL_OCCUPANCY_EFFECT'
            row={'marketId':mid,'t':int(s['t']),'checks':checks,'correctnessPass':all(checks.values()),'branches':br,
                 'managementArmContrast':arm,'managementArmClassification':arm_class,
                 'physicalOccupancyContrast':occ,'physicalOccupancyClassification':occ_class}
            rows.append(row)
            (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'checks':checks,
                              'armClass':arm_class,'armSameDirect':arm['sameDirectInterventionSignature'],'armDelta':arm['deltaTerminal'],
                              'occClass':occ_class,'occSameDirect':occ['sameDirectInterventionSignature'],'occDelta':occ['deltaTerminal']},ensure_ascii=False),flush=True)
    clean_arm=[r for r in rows if r['managementArmClassification']=='CLEAN_MANAGEMENT_ARM_SUFFIX_EFFECT']
    exec_arm=[r for r in rows if r['managementArmClassification']=='EXECUTION_MEDIATED_MANAGEMENT_ARM_EFFECT']
    clean_occ=[r for r in rows if r['physicalOccupancyClassification']=='CLEAN_PHYSICAL_OCCUPANCY_SUFFIX_EFFECT']
    summary={'markets':[r['marketId'] for r in rows],'correctMarkets':sum(r['correctnessPass'] for r in rows),
             'cleanManagementArmEffectMarkets':[r['marketId'] for r in clean_arm],
             'executionMediatedManagementArmMarkets':[r['marketId'] for r in exec_arm],
             'cleanPhysicalOccupancyEffectMarkets':[r['marketId'] for r in clean_occ],
             'managementArmCausalSupport':len(clean_arm)>=2,'physicalOccupancyCausalSupport':len(clean_occ)>=2}
    if summary['managementArmCausalSupport'] and summary['physicalOccupancyCausalSupport']:decision='OPTION_INVENTORY_AND_RESERVATION_BOTH_CAUSAL'
    elif summary['managementArmCausalSupport']:decision='QLADDER_RESERVATION_CAUSAL'
    elif summary['physicalOccupancyCausalSupport']:decision='PHYSICAL_OPTION_OCCUPANCY_CAUSAL_QLADDER_UNRESOLVED'
    elif len(exec_arm)>=2:decision='QLADDER_EFFECT_EXECUTION_MEDIATED_NOT_PURE_SUFFIX'
    else:decision='DECOMPOSITION_INCONCLUSIVE'
    payload={'version':'GPT6_H3A_Q_LADDER_ARM_DECOMPOSITION_SMOKE3_V1_20260907','researchOnly':True,'runtimeAuthority':False,
             'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,'summary':summary,'decision':decision,
             'boundary':['3 outcome-blind qLadder-arm seams from consumed H100 preflight','ARMED/UNARMED same frozen physical side/role/price/qty','UNARMED suppresses q_arm only at intervention submit','realistic HFT/no dream fill','no winner/Target runtime input','no fixed-time policy rule','no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':payload['allCorrectnessPass'],'decision':decision,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
