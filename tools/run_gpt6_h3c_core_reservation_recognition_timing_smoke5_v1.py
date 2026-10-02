"""H3c: isolate ECONOMIC_CORE reservation-recognition timing from physical order and role semantics.

Branches keep the intervention physical carrier and key_role ECONOMIC_CORE identical, q_arm suppressed.
Only whether _core_for_side() recognizes that intervention as a live service reservation differs:
 IMMEDIATE, ON_CONFIRMED_FILL, NEVER.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('h3b_base', HERE/'run_gpt6_h3b_core_role_reservation_semantic_fork_smoke5_v1.py')
h3b=importlib.util.module_from_spec(sp); sp.loader.exec_module(h3b)
h3=h3b.h3; h4=h3b.h4; EPS=h3b.EPS
BRANCHES=('CORE_IMMEDIATE_RESERVE','CORE_ON_CONFIRMED_FILL_RESERVE','CORE_NEVER_RESERVE')

def r10(x): return round(float(x or 0.0),10)
def close(a,b,tol=1e-8): return abs(float(a)-float(b))<=tol

def physical_snapshot(s):
    if s is None:return None
    orders=[]
    for o in s.get('liveOrders') or []:
        orders.append({k:o.get(k) for k in ('side','role','price','qty','remaining','status','cancelRequested','active')})
    orders=sorted(orders,key=lambda x:json.dumps(x,sort_keys=True,separators=(',',':')))
    return {'liveSlots':int(s.get('liveSlots') or 0),'liveOrders':orders,'qLadderRoute':s.get('qLadderRoute'),'pendingActive':bool(s.get('pendingActive')),
            'repairDebtUP':r10(s.get('repairDebtUP')),'repairDebtDOWN':r10(s.get('repairDebtDOWN')),'responsibilityCount':int(s.get('responsibilityCount') or 0),
            'upQty':r10(s.get('upQty')),'downQty':r10(s.get('downQty')),'cost':r10(s.get('cost')),'floor':r10(s.get('floor')),'best':r10(s.get('best')),'gap':r10(s.get('gap'))}

def sigeq(a,b): return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))

def next_submit(fd,which,weak):
    if fd is None:return None
    for e in fd.get('leftEvents' if which=='left' else 'rightEvents') or []:
        if e.get('kind')!='NEW_SUBMIT':continue
        det=e.get('details') or []
        if isinstance(det,dict):det=[det]
        if det:
            side=str(det[0].get('side') or '')
            return {'side':side,'relative':'WEAK_REPAIR_SIDE' if side==weak else 'EXPAND_SIDE','price':r10(det[0].get('price')),'qty':r10(det[0].get('qty'))}
    return None

class TimingFork(h3.TraceFork):
    def __init__(self,tape,state,branch):
        self.reservationKey=None; self.reservationRecognizedImmediately=None; self.frozenTreatmentParity=None
        super().__init__(tape,state,branch)

    def _core_for_side(self,side:str):
        rows=self._live_role_rows('ECONOMIC_CORE',side)
        for row in rows:
            sid,key,o,role=row
            if self.reservationKey is not None and key==self.reservationKey:
                if self.branch=='CORE_NEVER_RESERVE':
                    continue
                if self.branch=='CORE_ON_CONFIRMED_FILL_RESERVE' and float(o.get('cum') or 0.0)<=EPS:
                    continue
            return row
        return None

    def _custom_intervention(self,t,qv):
        A=self.frozenCandidates.get('A'); exp=self.spec.get('h3aFrozenCarrier') or {}
        self.frozenTreatmentParity=(A is not None and str(A['side'])==str(exp.get('side')) and close(A['price'],exp.get('price')) and close(A['qty'],exp.get('qty')))
        if A is None:return [self._submit_frozen(t,qv,A,'GPT6_H3C_RESERVATION_TIMING_V1')]
        C=copy.deepcopy(A); C['arm']=None; C['role']='ECONOMIC_CORE'
        rec=self._submit_frozen(t,qv,C,'GPT6_H3C_RESERVATION_TIMING_V1')
        if rec:
            rec['qArmUsed']=False; self.reservationKey=str(rec.get('key') or '')
        if self.interventionLegs:self.interventionLegs[-1]['qArmUsed']=False
        # Telemetry must measure whether the intervention key itself is eligible for
        # reservation lookup, not whether it happens to be the first core returned
        # when an older core already exists on the same side. Branch behavior is
        # unchanged; this only fixes the recognition-intent gate.
        o=self.orders.get(str(self.reservationKey),{}) if self.reservationKey else {}
        live=bool(o) and not bool(o.get('cancelRequested'))
        if self.branch=='CORE_IMMEDIATE_RESERVE':
            eligible=live
        elif self.branch=='CORE_ON_CONFIRMED_FILL_RESERVE':
            eligible=live and float(o.get('cum') or 0.0)>EPS
        else:
            eligible=False
        self.reservationRecognizedImmediately=bool(eligible)
        return [rec]

def compare(L,R,weak):
    sd=sigeq(L['directSignature'],R['directSignature']); st=h3.terminal_equal(L['terminal'],R['terminal']); fd=h3.first_divergence(L,R)
    if sd and not st: cls='CLEAN_RESERVATION_RECOGNITION_EFFECT'
    elif not sd: cls='EXECUTION_MEDIATED_RESERVATION_RECOGNITION_EFFECT'
    elif fd is not None: cls='TRANSIENT_RESERVATION_RECOGNITION_EFFECT_TERMINAL_CONVERGES'
    else: cls='NULL_RESERVATION_RECOGNITION_EFFECT'
    return {'classification':cls,'sameDirectInterventionSignature':sd,'sameTerminal':st,'firstDivergence':fd,
            'leftNextSubmit':next_submit(fd,'left',weak),'rightNextSubmit':next_submit(fd,'right',weak),
            'leftDirectSignature':L['directSignature'],'rightDirectSignature':R['directSignature'],
            'deltaTerminal':{k:float(R['terminal'][k])-float(L['terminal'][k]) for k in ('floor','best','gap','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--states',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    states=json.loads(Path(a.states).read_text(encoding='utf-8'))['states']; rows=[]; outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3c_reserve_timing_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for s in states:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(states,1):
            mid=int(s['marketId']); tape=root/'tapes'/f'{mid}.json.xz'; br={}
            for bn in BRANCHES:
                sim=TimingFork(tape,s,bn)
                try:
                    raw=sim.run_branch(); term=h4.f.terminal(raw,s); lin=sim.lineage(term)
                    br[bn]={'prefixT':int(s['t']),'prefixDigest':raw['prefixDigest'],'frozenTreatmentParity':bool(sim.frozenTreatmentParity),
                            'reservationKey':sim.reservationKey,'reservationRecognizedImmediately':sim.reservationRecognizedImmediately,
                            'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'directSignature':h3.direct_signature(lin),'trace':sim.h3trace,'terminal':term,'lineage':lin,
                            'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'triggered':bool(raw['seen']),'intervention':copy.deepcopy(sim.intervention)}
                finally:sim.close()
            I=br['CORE_IMMEDIATE_RESERVE'];F=br['CORE_ON_CONFIRMED_FILL_RESERVE'];N=br['CORE_NEVER_RESERVE']
            snaps=[physical_snapshot(x['initialPostSubmitSnapshot']) for x in (I,F,N)]; digs={x['prefixDigest'] for x in br.values()}
            checks={'prefixParity':len(digs)==1 and None not in digs,'allTriggered':all(x['triggered'] for x in br.values()),'allLedgerClean':all(x['ledgerClean'] for x in br.values()),
                    'allMax4':all(x['max4'] for x in br.values()),'frozenTreatmentParity':all(x['frozenTreatmentParity'] for x in br.values()),
                    'initialPhysicalAndRoleParity':snaps[0]==snaps[1]==snaps[2],
                    'allQLadderNull':all((x['initialPostSubmitSnapshot'] or {}).get('qLadderRoute') is None for x in br.values()),
                    'recognitionIntentCorrect':I['reservationRecognizedImmediately'] is True and F['reservationRecognizedImmediately'] is False and N['reservationRecognizedImmediately'] is False}
            cIF=compare(I,F,str(s['weakSide'])); cIN=compare(I,N,str(s['weakSide'])); cFN=compare(F,N,str(s['weakSide']))
            row={'marketId':mid,'t':int(s['t']),'weakSide':str(s['weakSide']),'expandSide':str(s['expandSide']),'checks':checks,'correctnessPass':all(checks.values()),'branches':br,
                 'immediateVsOnFill':cIF,'immediateVsNever':cIN,'onFillVsNever':cFN}
            rows.append(row); (outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(states),'marketId':mid,'correct':row['correctnessPass'],'IvsF':cIF['classification'],'IvsN':cIN['classification'],'FvsN':cFN['classification'],'IvsFNext':[cIF['leftNextSubmit'],cIF['rightNextSubmit']],'IvsFDelta':cIF['deltaTerminal']},ensure_ascii=False),flush=True)
    cleanIF=[r for r in rows if r['immediateVsOnFill']['classification']=='CLEAN_RESERVATION_RECOGNITION_EFFECT']; cleanIN=[r for r in rows if r['immediateVsNever']['classification']=='CLEAN_RESERVATION_RECOGNITION_EFFECT']
    support=all(r['correctnessPass'] for r in rows) and len(set([r['marketId'] for r in cleanIF+cleanIN]))>=2
    summary={'markets':[r['marketId'] for r in rows],'n':len(rows),'correctMarkets':sum(r['correctnessPass'] for r in rows),'cleanImmediateVsOnFill':[r['marketId'] for r in cleanIF],'cleanImmediateVsNever':[r['marketId'] for r in cleanIN],
             'recognitionCausalSupport':support}
    decision='SUPPORT_PREFILL_CORE_RESERVATION_RECOGNITION_CAUSALITY' if support else ('RECOGNITION_TIMING_NOT_CLEANLY_CAUSAL' if all(r['correctnessPass'] for r in rows) else 'INVALID_CORRECTNESS_FAILURE')
    out={'version':'GPT6_H3C_CORE_RESERVATION_RECOGNITION_TIMING_SMOKE5_V1_20260907','researchOnly':True,'runtimeAuthority':False,'allCorrectnessPass':all(r['correctnessPass'] for r in rows),'rows':rows,'summary':summary,'decision':decision,
         'boundary':['same physical carrier and ECONOMIC_CORE role in all branches','q_arm suppressed all branches','only _core_for_side recognition timing differs for intervention key','pending/unfilled zero economic payment/protection/credit','development-consumed smoke5','realistic HFT/no dream fill/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allCorrectnessPass':out['allCorrectnessPass'],'decision':decision,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
