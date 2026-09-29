from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_validation20_preregistered_v1.json'
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_instrumented_v1.json'
AUD=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_journal_audit_v1.json'
REP=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_repeatability_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_validation20_v1.json'

pre=json.loads(PRE.read_text(encoding='utf-8'))
src=json.loads(SRC.read_text(encoding='utf-8'))
aud=json.loads(AUD.read_text(encoding='utf-8'))
rep=json.loads(REP.read_text(encoding='utf-8'))
expected=[int(x) for x in pre['markets']]
actual=[int(r['marketId']) for r in src.get('rows',[])]
s=aud['summary']; g=pre['fixedGates']
checks={
 'cohortExact': actual==expected,
 'minimumMarkets': len(actual)>=int(g['minimumMarkets']),
 'stateReconstructionFailureCount': int(s.get('stateReconstructionFailureCount',-1))==0,
 'remainingObligationFailureCount': int(s.get('remainingObligationFailureCount',-1))==0,
 'fillLineageFailureCount': int(s.get('fillLineageFailureCount',-1))==0,
 'sequenceFailureCount': int(s.get('sequenceFailureCount',-1))==0,
 'receivedTimeFailureCount': int(s.get('receivedTimeFailureCount',-1))==0,
 'rootOpenFailureCount': int(s.get('rootOpenFailureCount',-1))==0,
 'parentChainFailureCount': int(s.get('parentChainFailureCount',-1))==0,
 'duplicateExecutionIdCount': int(s.get('duplicateExecutionIdCount',-1))==0,
 'orphanEventCount': int(s.get('orphanEventCount',-1))==0,
 'postCompletionCarrierCount': int(s.get('postCompletionCarrierCount',-1))==0,
 'preStressPrefixExactRate': float(s.get('preStressPrefixExactRate',0))==1.0,
 'branchContinuityRate': float(s.get('branchContinuityRate',0))==1.0,
 'cancelResolutionExactRate': float(s.get('cancelResolutionExactRate',0))==1.0,
 'minimumCheckableBranchContinuity': int(s.get('branchContinuityChecks',0))>=int(g['minimumCheckableBranchContinuity']),
 'minimumCancelResolutionChecks': int(s.get('cancelResolutionChecks',0))>=int(g['minimumCancelResolutionChecks']),
 'repeatability10of10': rep.get('status')=='REPEAT_PASS' and int(rep.get('exactRepeats',0))==10 and int(rep.get('repeats',0))==10,
}
status='P0A_PROVENANCE_CONTRACT_PASS' if all(checks.values()) else 'P0A_PROVENANCE_CONTRACT_FAIL'
out={
 'version':'R4_P0_PROVENANCE_VALIDATION20_V1','status':status,'preregistered':str(PRE.relative_to(ROOT)).replace('\\','/'),
 'checks':checks,'summary':s,'repeatability':{'status':rep.get('status'),'exactRepeats':rep.get('exactRepeats'),'repeats':rep.get('repeats'),'hash':rep.get('referenceHash')},
 'cohort':actual,
 'interpretation':'Immutable responsibility/intent event-journal instrumentation satisfies the fixed fresh-chronology provenance contract. This validates data provenance only; it is not strategy/action authority.' if status.endswith('PASS') else 'One or more fixed provenance gates failed.',
 'nextIfPass':'Materialize deterministic receipt-clock 1s/3s/5s per-intent confirmed-fill frontiers from baseline journal using frozen stress checkpoints; require immutable-key coverage and non-zero support before reopening continuous remaining-option belief.',
 'guards':pre['guards']
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'checks':checks},ensure_ascii=False))
