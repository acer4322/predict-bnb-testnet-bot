"""H3b semantic weak-side ECONOMIC_CORE reservation exact fork.

From identical strict-past prefixes and identical physical Repair carriers:
  ZERO: no intervention.
  CORE_UNARMED: exact frozen Repair carrier labelled ECONOMIC_CORE, q_arm suppressed.
  SATELLITE_LABEL_UNARMED: exact same side/price/qty labelled SATELLITE_REPAIR, q_arm suppressed.

The primary contrast CORE vs SATELLITE isolates key_role/service-reservation semantics from
physical slot occupancy and from economic payment. Research-only realistic HFT, consumed data.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('h3_trace_role', HERE/'run_gpt6_h3a_occupancy_suffix_first_divergence_smoke4_v1.py')
h3=importlib.util.module_from_spec(sp);sp.loader.exec_module(h3)
h4=h3.h4; EPS=h4.EPS
BRANCHES=('ZERO','CORE_UNARMED','SATELLITE_LABEL_UNARMED')

def r10(x):return round(float(x or 0.0),10)
def close(a,b,tol=1e-8):return abs(float(a)-float(b))<=tol

def carrier_view(c):
    if c is None:return None
    return {'side':str(c.get('side') or ''),'role':str(c.get('role') or ''),'price':r10(c.get('price')),'qty':r10(c.get('qty'))}

def physical_snapshot_no_role(s):
    if s is None:return None
    orders=[]
    for o in s.get('liveOrders') or []:
        orders.append({'side':str(o.get('side') or ''),'price':r10(o.get('price')),'qty':r10(o.get('qty')),'remaining':r10(o.get('remaining')),'status':str(o.get('status') or ''),'cancelRequested':bool(o.get('cancelRequested')),'active':bool(o.get('active'))})
    orders=sorted(orders,key=lambda x:json.dumps(x,sort_keys=True,separators=(',',':')))
    return {'liveSlots':int(s.get('liveSlots') or 0),'liveOrders':orders,'pendingActive':bool(s.get('pendingActive')),'repairDebtUP':r10(s.get('repairDebtUP')),'repairDebtDOWN':r10(s.get('repairDebtDOWN')),'responsibilityCount':int(s.get('responsibilityCount') or 0),'upQty':r10(s.get('upQty')),'downQty':r10(s.get('downQty')),'cost':r10(s.get('cost')),'floor':r10(s.get('floor')),'best':r10(s.get('best')),'gap':r10(s.get('gap'))}

def sig_equal(a,b):return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))

def next_submit_side(fd,which,weak):
    if fd is None:return None
    evs=fd.get('leftEvents' if which=='left' else 'rightEvents') or []
    for e in evs:
        if e.get('kind')!='NEW_SUBMIT':continue
        det=e.get('details') or []
        if isinstance(det,dict):det=[det]
        if det:
            side=str(det[0].get('side') or '')
            return {'side':side,'relative':'WEAK_REPAIR_SIDE' if side==weak else 'EXPAND_SIDE','price':r10(det[0].get('price')),'qty':r10(det[0].get('qty'))}
    return None

class RoleSemanticFork(h3.TraceFork):
    def __init__(self,tape,state,branch):
        super().__init__(tape,state,branch);self.frozenTreatmentParity=None
    def _custom_intervention(self,t,qv):
        A=self.frozenCandidates.get('A'); exp=self.spec.get('h3aFrozenCarrier') or {}
        self.frozenTreatmentParity=(A is not None and str(A['side'])==str(exp.get('side')) and close(A['price'],exp.get('price')) and close(A['qty'],exp.get('qty')))
        if self.branch=='ZERO':return []
        if A is None:return [self._submit_frozen(t,qv,A,'GPT6_H3B_ROLE_SEMANTIC_V1')]
        C=copy.deepcopy(A);C['arm']=None
        if self.branch=='CORE_UNARMED':C['role']='ECONOMIC_CORE'
        elif self.branch=='SATELLITE_LABEL_UNARMED':C['role']='SATELLITE_REPAIR'
        else:raise ValueError(self.branch)
        rec=self._submit_frozen(t,qv,C,'GPT6_H3B_ROLE_SEMANTIC_V1')
        if rec:rec['qArmUsed']=False
        if self.interventionLegs:self.interventionLegs[-1]['qArmUsed']=False
        return [rec]

def contrast(L,R,weak):
    same_direct=sig_equal(L['directSignature'],R['directSignature']);same_terminal=h3.terminal_equal(L['terminal'],R['terminal']);fd=h3.first_divergence(L,R)
    if same_direct and not same_terminal:cls='CLEAN_CORE_RESERVATION_SEMANTIC_EFFECT'
    elif not same_direct:cls='EXECUTION_MEDIATED_ROLE_SEMANTIC_EFFECT'
    elif fd is not None:cls='TRANSIENT_ROLE_SEMANTIC_EFFECT_TERMINAL_CONVERGES'
    else:cls='NULL_ROLE_SEMANTIC_EFFECT'
    return {'classification':cls,'sameDirectInterventionSignature':same_direct,'sameTerminal':same_terminal,'firstDivergence':fd,'leftNextSubmit':next_submit_side(fd,'left',weak),'rightNextSubmit':next_submit_side(fd,'right',weak),'leftDirectSignature':L['directSignature'],'rightDirectSignature':R['directSignature'],'deltaTerminal':{k:float(R['terminal'][k])-float(L['terminal'][k]) for k in ('floor','best','gap','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--states',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    states=json.loads(Path(a.states).read_text(encoding='utf-8'))['states'];rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3b_core_role_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';br={}
            for bn in BRANCHES:
                sim=RoleSemanticFork(tape,s,bn)
                try:
                    raw=sim.run_branch();term=h4.f.terminal(raw,s);lin=sim.lineage(term)
                    br[bn]={'prefixT':int(s['t']),'prefixDigest':raw['prefixDigest'],'frozenTreatmentParity':bool(sim.frozenTreatmentParity),'frozenA':carrier_view(sim.frozenCandidates.get('A')),'expectedCarrier':carrier_view(s.get('h3aFrozenCarrier')),'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'directSignature':h3.direct_signature(lin),'trace':sim.h3trace,'terminal':term,'lineage':lin,'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen']),'intervention':copy.deepcopy(sim.intervention)}
                finally:sim.close()
            C=br['CORE_UNARMED'];S=br['SATELLITE_LABEL_UNARMED'];Z=br['ZERO'];digs={x['prefixDigest'] for x in br.values()}
            phys_parity=physical_snapshot_no_role(C['initialPostSubmitSnapshot'])==physical_snapshot_no_role(S['initialPostSubmitSnapshot'])
            role_core=any(o.get('role')=='ECONOMIC_CORE' and o.get('side')==str(s['weakSide']) and close(o.get('price'),s['h3aFrozenCarrier']['price']) for o in (C['initialPostSubmitSnapshot'] or {}).get('liveOrders') or [])
            role_sat=any(o.get('role')=='SATELLITE_REPAIR' and o.get('side')==str(s['weakSide']) and close(o.get('price'),s['h3aFrozenCarrier']['price']) for o in (S['initialPostSubmitSnapshot'] or {}).get('liveOrders') or [])
            checks={'prefixParity':len(digs)==1 and None not in digs,'allTriggered':all(x['triggered'] for x in br.values()),'allLedgerClean':all(x['ledgerClean'] for x in br.values()),'allMax4':all(x['max4'] for x in br.values()),'frozenTreatmentParity':all(x['frozenTreatmentParity'] for x in br.values()),'coreSatelliteImmediatePhysicalParityIgnoringRole':phys_parity,'bothInterventionQLadderNull':(C['initialPostSubmitSnapshot'] or {}).get('qLadderRoute') is None and (S['initialPostSubmitSnapshot'] or {}).get('qLadderRoute') is None,'intentionalRoleLabelsPresent':role_core and role_sat}
            primary=contrast(C,S,str(s['weakSide']));context_zero_core=contrast(Z,C,str(s['weakSide']));context_zero_sat=contrast(Z,S,str(s['weakSide']))
            row={'marketId':mid,'t':int(s['t']),'weakSide':str(s['weakSide']),'expandSide':str(s['expandSide']),'checks':checks,'correctnessPass':all(checks.values()),'branches':br,'coreVsSatellite':primary,'zeroVsCore':context_zero_core,'zeroVsSatellite':context_zero_sat}
            rows.append(row);(outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'primaryClass':primary['classification'],'sameDirect':primary['sameDirectInterventionSignature'],'lagMs':None if primary['firstDivergence'] is None else primary['firstDivergence'].get('lagMs'),'coreNext':primary['leftNextSubmit'],'satNext':primary['rightNextSubmit'],'delta':primary['deltaTerminal']},ensure_ascii=False),flush=True)
    clean=[r for r in rows if r['coreVsSatellite']['classification']=='CLEAN_CORE_RESERVATION_SEMANTIC_EFFECT'];execm=[r for r in rows if r['coreVsSatellite']['classification']=='EXECUTION_MEDIATED_ROLE_SEMANTIC_EFFECT'];trans=[r for r in rows if r['coreVsSatellite']['classification']=='TRANSIENT_ROLE_SEMANTIC_EFFECT_TERMINAL_CONVERGES'];null=[r for r in rows if r['coreVsSatellite']['classification']=='NULL_ROLE_SEMANTIC_EFFECT']
    support=all(r['correctnessPass'] for r in rows) and len(clean)>=2
    summary={'markets':[r['marketId'] for r in rows],'n':len(rows),'correctMarkets':sum(r['correctnessPass'] for r in rows),'cleanSemanticEffectMarkets':[r['marketId'] for r in clean],'executionMediatedMarkets':[r['marketId'] for r in execm],'transientMarkets':[r['marketId'] for r in trans],'nullMarkets':[r['marketId'] for r in null],'semanticReservationCausalSupport':support}
    decision='SUPPORT_ECONOMIC_CORE_RESERVATION_SEMANTICS' if support else ('ROLE_SEMANTICS_PRESENT_BUT_NOT_CLEANLY_CAUSAL_YET' if all(r['correctnessPass'] for r in rows) else 'INVALID_CORRECTNESS_FAILURE')
    out={'version':'GPT6_H3B_CORE_ROLE_RESERVATION_SEMANTIC_FORK_SMOKE5_V1_20260907','researchOnly':True,'runtimeAuthority':False,'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,'summary':summary,'decision':decision,'boundary':['first five chronological freeze-clean states with prefix liveSlots<=2','CORE vs SATELLITE identical physical side/price/qty and slot count; role label only','q_arm suppressed both','pending/unfilled gives zero economic payment/credit','realistic HFT/consumed H100','no fixed-time policy rule/no NEW24-B/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'decision':decision,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
