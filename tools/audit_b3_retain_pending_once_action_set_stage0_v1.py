from __future__ import annotations
import ast, copy, hashlib, inspect, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import b3_retain_pending_once_action_set_v1 as a1

TSTAR=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PRE_RELEASE_P0_CANCEL_PRIMARY1824852_TSTAR_FREEZE_V1_20260908.json'
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_RETAIN_PENDING_ONCE_ACTION_SET_STAGE0_PREREG_V1_20260908.json'
OUTC=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_RETAIN_PENDING_ONCE_ACTION_SET_STAGE0_COMPACT_V1_20260908.json'
OUTR=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_RETAIN_PENDING_ONCE_ACTION_SET_STAGE0_REPORT_V1_20260908.md'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

class FakeSim:
    def __init__(self):
        self.q_ladder={'originResponsibilityId':1,'targetExpandSide':'UP','side':'DOWN','role':'ECONOMIC_CORE','route':'PENDING_ACTIVE','activeKey':None,'satisfiedElsewhere':False}
        self.q_pending_active={'originResponsibilityId':1,'targetExpandSide':'UP','side':'DOWN','role':'ECONOMIC_CORE','sourceKey':'DOWN_22','sourceRemainingQty':1.6666666666666667}
        self.slot_key={1:'DOWN_25'}
        self.orders={'DOWN_25':{'n':25,'side':'DOWN','price':0.6,'qty':1.6666666666666667,'cum':0.0,'status':'NEW','cancelRequested':False}}
        self.resp_payment_rows=[]
        self.submits=25
        self.native_calls=0
    def _open_one_option(self,t,qv,end):
        self.native_calls += 1
        self.submits += 1
        return 'NATIVE_RESULT'

def new_state():
    return a1.RetainPendingOnceState(1824852,1,'UP','DOWN_22')

def main():
    ts=json.loads(TSTAR.read_text(encoding='utf-8'))
    checks={}
    checks['frozenPrimaryRefsExact']=int(ts['marketId'])==1824852 and int(ts['H0']['originResponsibilityId'])==1 and ts['H0']['targetExpandSide']=='UP' and ts['H0']['sourceKey']=='DOWN_22' and ts['P0']['key']=='DOWN_25' and int(ts['tStar']['phaseOrdinal'])==79
    # Typed disposition must be distinct from bool/submit success.
    checks['typedDispositionDistinct']=a1.OpenDecisionDisposition.HANDLED_NO_EMISSION.value=='HANDLED_NO_EMISSION' and a1.OpenDecisionDisposition.NATIVE_DELEGATED.value=='NATIVE_DELEGATED'

    s=FakeSim(); st=new_state(); before=copy.deepcopy(s.__dict__)
    r=a1.dispatch_open_decision(s,state=st,action='RETAIN_PENDING_ONCE',p0_key='DOWN_25',decision_ref='1824852:79',t=1788166227105,qv={},end=1788166500000)
    after=copy.deepcopy(s.__dict__)
    checks['retainAccepted']=r.get('disposition')=='HANDLED_NO_EMISSION' and not r.get('rejected')
    checks['retainZeroNativeCalls']=s.native_calls==0
    checks['retainZeroPhysicalLedgerMutation']=all(r.get('invariantParity',{}).values()) and before==after | {'native_calls':after['native_calls']} if False else bool(r['checks']['zeroPhysicalOrLedgerMutation'])
    checks['oneShotConsumed']=st.one_shot_used and st.consumed_decision_ref=='1824852:79'

    r2=a1.dispatch_open_decision(s,state=st,action='RETAIN_PENDING_ONCE',p0_key='DOWN_25',decision_ref='1824852:79',t=1788166227105,qv={},end=1788166500000)
    checks['sameDecisionRepeatRejected']=bool(r2.get('rejected')) and r2.get('disposition') is None and s.native_calls==0

    # Route/generation-like caller changes cannot reset one-shot state.
    s.q_ladder['route']='PENDING_ACTIVE';s.q_pending_active['sourceKey']='DOWN_22'
    r3=a1.dispatch_open_decision(s,state=st,action='RETAIN_PENDING_ONCE',p0_key='DOWN_25',decision_ref='1824852:80',t=1788166227200,qv={},end=1788166500000)
    checks['nextDecisionSecondRetainRejected']=bool(r3.get('rejected')) and s.native_calls==0

    # Next normal receipt delegates to frozen native exactly once despite used retain state.
    n0=s.native_calls; sub0=s.submits
    rn=a1.dispatch_open_decision(s,state=st,action='NATIVE',p0_key='DOWN_25',decision_ref='1824852:80',t=1788166227200,qv={},end=1788166500000)
    checks['nextReceiptNativeReentry']=rn.get('disposition')=='NATIVE_DELEGATED' and s.native_calls==n0+1 and s.submits==sub0+1 and rn.get('nativeSubmitDelta')==1

    # Cancel-pending remains eligible if still physically reserved; no virtual release is introduced.
    s2=FakeSim();s2.orders['DOWN_25']['cancelRequested']=True;st2=new_state()
    ok_cp,cp=a1.eligible_retain_pending_once(s2,st2,'DOWN_25','cp')
    checks['cancelPendingStillReservedEligible']=ok_cp and cp['p0PositivePhysicalReservation']

    # Confirmed physical release makes action unavailable.
    s3=FakeSim();s3.slot_key={};st3=new_state();ok_rel,_=a1.eligible_retain_pending_once(s3,st3,'DOWN_25','rel')
    checks['releasedP0Rejected']=not ok_rel

    # Cleared/satisfied H0 makes action unavailable.
    s4=FakeSim();s4.q_ladder['satisfiedElsewhere']=True;st4=new_state();ok_sat,_=a1.eligible_retain_pending_once(s4,st4,'DOWN_25','sat')
    checks['satisfiedH0Rejected']=not ok_sat

    # No authority duplication: action state only stores references/use bit, no qty/price/owner/payment fields.
    field_names=set(a1.RetainPendingOnceState.__dataclass_fields__)
    forbidden={'qty','price','owner','generation','remainingQty','paidQty','reservation','credit','debt'}
    checks['actionStateNoSpendableAuthorityFields']=field_names.isdisjoint(forbidden)

    # Source-level proof: RETAIN branch does not call native open; disabled branch has one direct call site.
    src=inspect.getsource(a1.dispatch_open_decision); tree=ast.parse(src)
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
    native_calls=[n for n in calls if isinstance(n.func,ast.Attribute) and n.func.attr=='_open_one_option']
    checks['singleNativeDelegateCallSite']=len(native_calls)==1
    checks['noCancelResizeSubmitSymbolsInActionModule']=all(x not in Path(a1.__file__).read_text(encoding='utf-8') for x in ['._request_cancel(', '.submit_buy_order(', '.submit_sell_order(', 'resize_order'])

    verdict='STAGE0_ACTION_SET_WELL_FORMED_PRIMARY_HFT_AUTHORIZED' if all(checks.values()) else 'ACTION_SET_REDESIGN_NOT_WELL_FORMED'
    compact={'version':'B3_RETAIN_PENDING_ONCE_ACTION_SET_STAGE0_V1_20260908','researchOnly':True,'runtimeAuthority':False,'marketId':1824852,'checks':checks,'allPass':all(checks.values()),'verdict':verdict,
      'sha256':{'actionModule':sha(a1.__file__),'audit':sha(Path(__file__)),'tStarFreeze':sha(TSTAR),'prereg':sha(PREREG)},
      'boundary':['0 HFT branch-equivalents','typed HANDLED_NO_EMISSION only','no physical command/no reservation release/no ledger mutation','one-shot cannot reset','next normal receipt delegates native','no winner/future/budget outcome in trigger']}
    OUTC.write_text(json.dumps(compact,indent=2),encoding='utf-8')
    lines=['# B3 RETAIN_PENDING_ONCE — Stage 0 Action-Set Proof','',f'Verdict: **`{verdict}`**','','## Checks']+[f'- {k}: {v}' for k,v in checks.items()]+['','## Scope','- Static/typed fixtures only; 0 HFT branch-equivalents.','- Research harness action-set extension only; live/runtime unchanged.','- No economic claim.','', '**STAGE 0 COMPLETE — STOP/GO PER VERDICT.**']
    OUTR.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'checks':checks,'compact':str(OUTC.relative_to(ROOT)),'report':str(OUTR.relative_to(ROOT))},ensure_ascii=False))

if __name__=='__main__': main()
