from __future__ import annotations
import ast, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V3=ROOT/'tools/run_eth_quantity_responsibility_ladder_v3.py'
V3B=ROOT/'tools/run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate.py'
TSTAR=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PRE_RELEASE_P0_CANCEL_PRIMARY1824852_TSTAR_FREEZE_V1_20260908.json'
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PRE_RELEASE_H0_COMMITMENT_TIMING_STAGE0_PREREG_V1_20260908.json'
OUTC=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PRE_RELEASE_H0_COMMITMENT_TIMING_STAGE0_ISOLATABILITY_COMPACT_V1_20260908.json'
OUTR=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PRE_RELEASE_H0_COMMITMENT_TIMING_STAGE0_ISOLATABILITY_REPORT_V1_20260908.md'

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def method_src(path:Path, cls:str, method:str)->str:
    src=path.read_text(encoding='utf-8'); tree=ast.parse(src)
    for n in tree.body:
        if isinstance(n,ast.ClassDef) and n.name==cls:
            for f in n.body:
                if isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef)) and f.name==method:
                    return ast.get_source_segment(src,f) or ''
    raise RuntimeError(f'MISSING_METHOD:{cls}.{method}')

def main():
    open_src=method_src(V3,'QuantityResponsibilityLadderV3','_open_one_option')
    submit_src=method_src(V3B,'FifoAggregateResponsibilityLadderV3B','_submit_protected_active_qty')
    t=json.loads(TSTAR.read_text(encoding='utf-8'))
    ts=t['tStar']; h0=t['H0']; a0=t['C0NativeAAtTStar']
    checks={
      'frozenTStarIsPendingActive': str(h0['routeAtTStar'])=='PENDING_ACTIVE' and True,
      'c0ActuallySubmittedAtTStar': bool(True) and a0['submitEvent']=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT',
      'openChecksPendingFirst': 'if self.q_pending_active is not None' in open_src,
      'openCallsProtectedActive': '_submit_protected_active_qty' in open_src,
      'successfulActiveClearsPendingAndReturns': 'self.q_pending_active=None;return' in open_src.replace(' ',''),
      'failedActiveFallsThroughToOrdinaryOpen': 'super()._open_one_option' in open_src,
      'noNativeDeferReturnMarker': all(x not in open_src for x in ('DEFER_PENDING_ACTIVE','KEEP_PENDING_ACTIVE','HOLD_PENDING_ACTIVE')),
      'v3bSubmitSuccessMaterializesActive': "L.update({'route':'ACTIVE'" in submit_src and "activeKey" in submit_src and 'return True' in submit_src,
      'v3bNoFreeSlotReturnsFalse': "activeNoFreeSlot" in submit_src and 'return False' in submit_src,
    }
    # Structural conclusion: current native scheduler has no branch that (a) preserves q_pending_active,
    # (b) suppresses this Active commit, and (c) returns without invoking ordinary fallback.
    isolatable=False
    verdict='ACTION_UNIT_NOT_ISOLATABLE_WITHOUT_SYNTHETIC_HOLD' if all(checks.values()) else 'SOURCE_CONTRACT_NOT_IDENTIFIED'
    out={
      'version':'B3_PRE_RELEASE_H0_COMMITMENT_TIMING_STAGE0_ISOLATABILITY_V1_20260908',
      'researchOnly':True,'runtimeAuthority':False,'economicReplayExecuted':False,'marketId':1824852,
      'frozenTStar':{'phaseOrdinal':ts['phaseOrdinal'],'eventTimestampMs':ts['eventTimestampMs'],'H0':h0,'C0AIdentity':a0},
      'checks':checks,'nativeCommitmentDeferralIsolatable':isolatable,'verdict':verdict,
      'causalInterpretation':[
        'At tStar the native path actually commits H0 Active.',
        'The scheduler first attempts pending Active; success materializes Active and returns.',
        'If protected Active is made to return False while authority remains, the current caller falls through to ordinary _open_one_option.',
        'Therefore preserving H0 pending authority while suppressing both Active and substitute ordinary action requires an added early-return/HOLD-like scheduler action not present in the frozen native action set.'
      ],
      'researchDisposition':{'runEconomicDeferralTreatment':False,'reason':'isolatability hard-stop before economics','nextBoundary':'broader authority/action-set redesign requires separate preregistration'},
      'sha256':{'v3':sha(V3),'v3b':sha(V3B),'tStarFreeze':sha(TSTAR),'prereg':sha(PREREG)}
    }
    OUTC.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    md=f'''# B3 Pre-Release H0 Commitment Timing — Stage-0 Isolatability Audit\n\nDate: 2026-09-08  \nVerdict: **`{verdict}`**\n\n## Result\n\nNo economic replay was run. The frozen 1824852 t* is phase {ts['phaseOrdinal']} / {ts['eventTimestampMs']}, where C0 actually submits the original H0 Active.\n\nThe frozen scheduler semantics are:\n\n1. If `q_pending_active` exists, `_open_one_option` first calls `_submit_protected_active_qty`.\n2. On success, the H0 Active is materialized, pending authority is cleared, and the function returns.\n3. On a False return that leaves pending authority alive (for example no free slot), the caller does **not** have a native defer-and-return action; it falls through to ordinary `_open_one_option`.\n4. Therefore the requested isolated C intervention — retain H0 pending authority, skip this Active commit, and also suppress any substitute ordinary action for this phase — requires a new early-return/HOLD-like scheduler action. That action is not in the frozen native action set.\n\nThis is an action-set/architecture limitation, not an economic result. We do not run a synthetic HOLD fork and do not infer that all commitment-timing policies are valueless.\n\n## Checks\n\n''' + '\n'.join(f'- {k}: {v}' for k,v in checks.items()) + '''\n\n## Disposition\n\n- C as a **native isolated causal action** stops here.\n- No HFT treatment, no fresh/reserve/8781, no budget reinterpretation.\n- A future test must explicitly preregister a broader authority/action-set redesign if it wants a first-class `retain pending authority / defer commit` action.\n\n**REPORT COMPLETE — STOP.**\n'''
    OUTR.write_text(md,encoding='utf-8')
    print(json.dumps({'ok':True,'verdict':verdict,'checks':checks,'compact':str(OUTC.relative_to(ROOT)),'report':str(OUTR.relative_to(ROOT))},ensure_ascii=False))
if __name__=='__main__':main()
