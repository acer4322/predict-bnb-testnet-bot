"""H3F: decontaminated dual Repair+Expand lane at the first H3c recognition-induced divergence.

Research-only exact fork on consumed development states. This is NOT the older H4 joint-prefix
factorial. Each branch first recreates the same H3c prefix and initial ECONOMIC_CORE intervention.
At the preregistered first recognition-induced divergence receipt:
  IMMEDIATE_NATIVE: current pre-fill CORE reservation recognition semantics.
  ONFILL_NATIVE:    recognize the intervention CORE only after confirmed fill.
  DUAL_DECONTAMINATED: keep IMMEDIATE semantics and its native action, then (only if a real max4
                        slot remains) add the frozen alternate weak-side geometry as SATELLITE_REPAIR.

The DUAL extra carrier receives no new credit/authority and is not counted as the original H3c
intervention; exact FIFO accounting remains inherited. Fixed divergence timestamps are replay
identifiers only, never proposed runtime timing rules.
"""
from __future__ import annotations
import argparse, copy, importlib.util, json, math, os, tempfile, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('h3f_h3c', HERE/'run_gpt6_h3c_core_reservation_recognition_timing_smoke5_v1.py')
h3c=importlib.util.module_from_spec(sp);sp.loader.exec_module(h3c)
h3b=h3c.h3b; h3=h3c.h3; h4=h3c.h4; EPS=h3c.EPS
BRANCHES=('IMMEDIATE_NATIVE','ONFILL_NATIVE','DUAL_DECONTAMINATED')

def r10(x):return round(float(x or 0.0),10)
def close(a,b,tol=1e-8):return math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol)
def stable(x):return h3.stable(x)

def physical_snapshot(s):return h3c.physical_snapshot(s)

def action_view(sim,key):
    if not key or key not in sim.orders:return None
    o=sim.orders[key]
    return {'key':str(key),'side':str(o.get('side') or ''),'role':str(sim.key_role.get(key,'UNASSIGNED')),
            'price':r10(o.get('price')),'qty':r10(o.get('qty')),'cum':r10(o.get('cum')),
            'cancelRequested':bool(o.get('cancelRequested')),'status':str(o.get('status') or '')}

def action_matches(a,exp,role=None):
    if a is None or exp is None:return False
    ok=str(a.get('side'))==str(exp.get('side')) and close(a.get('price'),exp.get('price')) and close(a.get('qty'),exp.get('qty'))
    if role is not None:ok=ok and str(a.get('role'))==str(role)
    return bool(ok)

def fill_qty(sim,key):
    if not key:return 0.0
    return float(sum(float(x.get('confirmedQty') or 0.0) for x in sim.fill_accounting if str(x.get('key') or '')==str(key)))

def fill_split(sim,key):
    if not key:return {'confirmedQty':0.0,'repairQty':0.0,'overflowQty':0.0,'events':0}
    xs=[x for x in sim.fill_accounting if str(x.get('key') or '')==str(key)]
    return {'confirmedQty':sum(float(x.get('confirmedQty') or 0.0) for x in xs),
            'repairQty':sum(float(x.get('matchedRepairQty') or 0.0) for x in xs),
            'overflowQty':sum(float(x.get('overflowQty') or 0.0) for x in xs),'events':len(xs)}

class DualLaneFork(h3c.TimingFork):
    def __init__(self,tape,state,h3f_branch):
        self.h3fBranch=str(h3f_branch)
        parent_branch='CORE_ON_CONFIRMED_FILL_RESERVE' if self.h3fBranch=='ONFILL_NATIVE' else 'CORE_IMMEDIATE_RESERVE'
        super().__init__(tape,state,parent_branch)
        self.divergenceSeen=False;self.preDivergenceSnapshot=None;self.preDivergenceN=None
        self.nativeDivergenceKey=None;self.extraRepairKey=None;self.nativeDivergenceAction=None;self.extraRepairAction=None
        self.dualAttempt=None

    def _keys_created_since(self,before_n):
        out=[]
        for n in range(int(before_n),int(self.n)):
            for side in ('UP','DOWN'):
                key=f'{side}_{n}'
                if key in self.orders:out.append(key)
        return out

    def _submit_dual_extra(self,t,exp):
        side=str(exp['side']);p=float(exp['price']);q=float(exp['qty']);role='SATELLITE_REPAIR'
        rec={'attempted':True,'ok':False,'reason':None,'side':side,'role':role,'price':p,'qty':q,'key':None}
        if self.q_pending_active is not None:rec['reason']='PENDING_ACTIVE_PRESENT';return rec
        if len(self.slot_key)>=self.max_slots:rec['reason']='NO_REAL_FREE_SLOT';return rec
        if not (0.0<p<1.0 and q>0.0 and q<=12.0+EPS and p*q>=1.0-EPS):rec['reason']='VENUE_QUANTITY';return rec
        if any(abs(float(x)-p)<=EPS for x in self._used_prices(side)):rec['reason']='PRICE_ALREADY_USED';return rec
        if not self._pair_ok(side,p):rec['reason']='PAIR_ILLEGAL';return rec
        before_n=int(self.n);before_sub=int(self.submits);self.q_arm=None
        ok=bool(self._submit_role(int(t),side,role,p,q,None,'GPT6_H3F_DUAL_DECONTAMINATED_EXTRA_REPAIR_V1'))
        key=f'{side}_{before_n}' if ok and int(self.submits)>before_sub else None
        rec.update({'ok':bool(ok),'reason':None if ok else 'SUBMIT_FALSE','key':key})
        return rec

    def _open_one_option(self,t,qv,end):
        dt=int(self.spec['h3fDivergenceT'])
        # Initial H3c prefix intervention: unchanged parent behavior.
        if int(t)==int(self.spec['t']) and not self.seen:
            return super()._open_one_option(t,qv,end)
        if int(t)==dt and not self.divergenceSeen:
            self.divergenceSeen=True;self.preDivergenceSnapshot=self._snapshot(t);self.preDivergenceN=int(self.n)
            before_n=int(self.n);before_sub=int(self.submits)
            # Parent action is exact current branch behavior: IMMEDIATE for IMMEDIATE/DUAL, ON_FILL for ONFILL.
            out=super()._open_one_option(t,qv,end)
            keys=self._keys_created_since(before_n)
            self.nativeDivergenceKey=keys[0] if len(keys)==1 and int(self.submits)>before_sub else None
            self.nativeDivergenceAction=action_view(self,self.nativeDivergenceKey)
            if self.h3fBranch=='DUAL_DECONTAMINATED':
                exp=copy.deepcopy(self.spec['h3fAlternateRepairAction'])
                self.dualAttempt=self._submit_dual_extra(t,exp)
                self.extraRepairKey=str(self.dualAttempt.get('key') or '') or None
                self.extraRepairAction=action_view(self,self.extraRepairKey)
            return out
        return super()._open_one_option(t,qv,end)


def branch_payload(sim,raw,s):
    term=h4.f.terminal(raw,s);lin=sim.lineage(term)
    return {'prefixDigest':raw['prefixDigest'],'triggered':bool(raw['seen']),'divergenceSeen':bool(sim.divergenceSeen),
            'reservationKey':sim.reservationKey,'reservationRecognizedImmediately':sim.reservationRecognizedImmediately,
            'initialPostSubmitSnapshot':sim.initialPostSubmitSnapshot,'preDivergenceSnapshot':sim.preDivergenceSnapshot,
            'directSignature':h3.direct_signature(lin),'terminal':term,'lineage':lin,'trace':sim.h3trace,
            'nativeDivergenceKey':sim.nativeDivergenceKey,'nativeDivergenceAction':sim.nativeDivergenceAction,
            'extraRepairKey':sim.extraRepairKey,'extraRepairAction':sim.extraRepairAction,'dualAttempt':copy.deepcopy(sim.dualAttempt),
            'nativeFill':fill_split(sim,sim.nativeDivergenceKey),'extraRepairFill':fill_split(sim,sim.extraRepairKey),
            'ledgerClean':not bool(term['ledgerViolations']),'max4':int(term['maxSlots'])<=4,'maxSlots':int(term['maxSlots'])}

def term_delta(a,b):
    ks=('floor','best','gap','favoredPayoff','weakPayoff','upQty','downQty','buyNotional','fills','submits','alternations','activeSubmits','managedRepairQty','managedOverflowQty')
    return {k:float(b[k])-float(a[k]) for k in ks}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    specs=json.loads(Path(a.specs).read_text(encoding='utf-8'))['states'];rows=[];outdir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))
    with tempfile.TemporaryDirectory(prefix='h3f_dual_lane_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json').decode())['rows']}
            for s in specs:z.extract(f"tapes/{int(s['marketId'])}.json.xz",root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);tape=root/'tapes'/f'{mid}.json.xz';br={}
            for bn in BRANCHES:
                sim=DualLaneFork(tape,s,bn)
                try:
                    raw=sim.run_branch();br[bn]=branch_payload(sim,raw,s)
                finally:sim.close()
            I,F,D=(br[x] for x in BRANCHES);digs={x['prefixDigest'] for x in br.values()}
            pre=[physical_snapshot(x['preDivergenceSnapshot']) for x in (I,F,D)]
            immediate_exp=s['h3fImmediateAction'];repair_exp=s['h3fAlternateRepairAction']
            # OnFill native action is a geometry control only; its role may be ECONOMIC_CORE because that branch hides the pending Core.
            checks={
              'prefixParity':len(digs)==1 and None not in digs,
              'allInitialTriggered':all(x['triggered'] for x in br.values()),
              'allDivergenceTriggered':all(x['divergenceSeen'] for x in br.values()),
              'allLedgerClean':all(x['ledgerClean'] for x in br.values()),
              'allMax4':all(x['max4'] for x in br.values()),
              'originalDirectInterventionParity':h3b.sig_equal(I['directSignature'],F['directSignature']) and h3b.sig_equal(I['directSignature'],D['directSignature']),
              'preDivergencePhysicalParity':pre[0]==pre[1]==pre[2],
              'immediateNativeExact':action_matches(I['nativeDivergenceAction'],immediate_exp),
              'onFillAlternateGeometryExact':action_matches(F['nativeDivergenceAction'],repair_exp),
              'dualNativeExact':action_matches(D['nativeDivergenceAction'],immediate_exp),
              'dualExtraRepairExactAndRole':action_matches(D['extraRepairAction'],repair_exp,'SATELLITE_REPAIR'),
              'dualExtraSubmitSucceeded':bool((D.get('dualAttempt') or {}).get('ok')),
              'dualHasTwoDistinctDivergenceKeys':bool(D['nativeDivergenceKey'] and D['extraRepairKey'] and D['nativeDivergenceKey']!=D['extraRepairKey']),
            }
            winner=str(cohort[mid]['winner']).upper()
            for x in br.values():
                t=x['terminal'];x['winnerPnlPostHoc']=float(t['upQty'] if winner=='UP' else t['downQty'])-float(t['buyNotional'])
                x['shapePostHoc']=float(t['best'])>2.0 and float(t['floor'])>-1.0
            row={'marketId':mid,'prefixT':int(s['t']),'divergenceT':int(s['h3fDivergenceT']),'winnerPostHocOnly':winner,'stateSpec':s,
                 'checks':checks,'correctnessPass':all(checks.values()),'branches':br,
                 'dualVsImmediateTerminal':term_delta(I['terminal'],D['terminal']),'dualVsOnFillTerminal':term_delta(F['terminal'],D['terminal']),
                 'dualBothDivergenceKeysConfirmed':D['nativeFill']['confirmedQty']>EPS and D['extraRepairFill']['confirmedQty']>EPS}
            rows.append(row);(outdir/'rows.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in rows)+'\n',encoding='utf-8')
            print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'correct':row['correctnessPass'],'checks':checks,
                              'native':D['nativeDivergenceAction'],'extra':D['extraRepairAction'],'nativeFill':D['nativeFill'],'extraFill':D['extraRepairFill'],
                              'bothFilled':row['dualBothDivergenceKeysConfirmed'],'pnl':{b:br[b]['winnerPnlPostHoc'] for b in BRANCHES},
                              'dualVsImmediate':row['dualVsImmediateTerminal']},ensure_ascii=False),flush=True)
    allpass=all(r['correctnessPass'] for r in rows);exercised=sum(r['dualBothDivergenceKeysConfirmed'] for r in rows)
    payload={'version':'GPT6_H3F_DUAL_LANE_FIRST_DIVERGENCE_SMOKE3_V1_20260907','researchOnly':True,'runtimeAuthority':False,'allCorrectnessPass':allpass,'rows':rows,
             'summary':{'markets':[r['marketId'] for r in rows],'n':len(rows),'correctMarkets':sum(r['correctnessPass'] for r in rows),'dualBothConfirmedMarkets':exercised,
                        'dualPnlDeltaVsImmediate':sum(r['branches']['DUAL_DECONTAMINATED']['winnerPnlPostHoc']-r['branches']['IMMEDIATE_NATIVE']['winnerPnlPostHoc'] for r in rows),
                        'dualPnlDeltaVsOnFill':sum(r['branches']['DUAL_DECONTAMINATED']['winnerPnlPostHoc']-r['branches']['ONFILL_NATIVE']['winnerPnlPostHoc'] for r in rows),
                        'dualShapeCount':sum(r['branches']['DUAL_DECONTAMINATED']['shapePostHoc'] for r in rows)},
             'decision':('H3F_DUAL_PHYSICALLY_EXERCISED_REVIEW_VALUE' if allpass and exercised>0 else ('H3F_CORRECT_BUT_BOTH_FILL_NOT_EXERCISED' if allpass else 'INVALID_CORRECTNESS_FAILURE')),
             'boundary':['exact H3c first recognition-induced divergence receipt; timestamp is replay identity only','DUAL retains IMMEDIATE recognition and adds frozen alternate weak-side geometry as SATELLITE_REPAIR only if real max4 slot remains','no new authority/credit/objective/Active trigger','pending/unfilled zero economic payment/protection/credit','exact FIFO inherited','consumed development only','sealed H3 Holdout25 unopened','realistic HFT/no dream fill/no 8781/no NEW24-B']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':payload['decision'],'summary':payload['summary'],'allCorrectnessPass':allpass},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
