from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT=Path('.')
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1'
paths={
  1824758: ROOT/'data/research/lan_worker_returns/b3-plvac-post-release-locus-context1824758-20260908-v2/result.json',
  1824852: ROOT/'data/research/lan_worker_returns/b3-plvac-post-release-locus-primary1824852-20260908-v2/result.json',
  1825962: ROOT/'data/research/lan_worker_returns/b3-plvac-post-release-locus-context1825962-20260908-v1/result.json',
  1825994: ROOT/'data/research/lan_worker_returns/b3-plvac-post-release-locus-context1825994-20260908-v1/result.json',
}
formal=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PROSPECTIVE_LAYERED_CONTRACT_CHALLENGE_SMOKE4_V1_COMPACT_20260908.json'
prereg=ROOT/'data/research/r4_v0/p0_provenance_v1/B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_PREREG_20260908.json'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
base=json.loads(formal.read_text(encoding='utf-8'))
rows=[]
for mid,p in paths.items():
    d=json.loads(p.read_text(encoding='utf-8')); tr=d['traces']['P_TRACE']; rho=tr.get('rho'); tau=tr.get('tau'); sig=tr.get('sigmas') or {}; census=tr.get('menuCensus') or {}
    formalrow=next(x for x in base['rows'] if int(x['marketId'])==mid)
    if tau is not None:
        cash_lb=float(tau['prefix']['cashAtRiskPeak']); lb_source='TAU_MINUS_PREFIX_RUNNING_PEAK'
    else:
        cash_lb=float(formalrow['PNumeric']['cashAtRiskPeak']); lb_source='ORIGINAL_P_FINAL_NO_POST_RELEASE_H0_LEVER'
    rows.append({
      'marketId':mid,'sourceJobResult':str(p),'sourceSha256':sha(p),'correctnessPass':bool(d.get('correctnessPass')),
      'formalBudgetStatus':formalrow['budgetStatus'],'formalTailFailures':formalrow['tailFailures'],
      'rho':rho,'tau':tau,'sigmas':sig,'verdict':d.get('verdict'),
      'postReleaseMenuCensus':{'phasesChecked':census.get('phasesChecked'),'checkTrueCounts':census.get('checkTrueCounts'),'reasonCombinationCounts':census.get('reasonCombinationCounts')},
      'cashAtRiskPeakPostReleaseClassLowerBound':cash_lb,'cashAtRiskPeakLowerBoundSource':lb_source,
      'formalPCashAtRiskPeak':formalrow['PNumeric']['cashAtRiskPeak'],
      'formalDeltaU':formalrow['deltaU'],'formalDeltaD':formalrow['deltaD'],
      'traceParityPass':bool(tr.get('parityPass')),
    })

pool=float(base['cohortPool']['cashAtRiskPeak']['frozenPool'])
lb=sum(r['cashAtRiskPeakPostReleaseClassLowerBound'] for r in rows)
all_correct=all(r['correctnessPass'] and r['traceParityPass'] for r in rows)
primary=next(r for r in rows if r['marketId']==1824852)
large=next(r for r in rows if r['marketId']==1825994)
# Fixed audit interpretation: primary branch falsified if no post-release same-H0 lever; context 1825994 independently matches mechanism.
if not all_correct:
    final='CORRECTNESS_OR_PROVENANCE_STOP'
elif primary['verdict']=='POST_RELEASE_RESCUE_FALSIFIED_NO_NATIVE_HANDOFF_LEVER':
    final='POST_RELEASE_RESCUE_FALSIFIED_NO_NATIVE_HANDOFF_LEVER'
else:
    final=primary['verdict']
compact={
 'version':'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_COMPACT_20260908',
 'researchOnly':True,'runtimeAuthority':False,'allCorrectnessAndTraceParityPass':all_correct,
 'primaryMarket':1824852,'primaryVerdict':primary['verdict'],'finalAuditVerdict':final,
 'contextIndependentMatchingFailureMarket':1825994 if large['verdict']=='POST_RELEASE_RESCUE_FALSIFIED_NO_NATIVE_HANDOFF_LEVER' else None,
 'cashAtRiskPeakPostReleaseClassPoolLowerBound':lb,'frozenCashAtRiskPeakPool':pool,'poolLowerBoundExceedsFrozenPool':lb>pool+1e-7,'poolHeadroomAtNecessaryLowerBound':pool-lb,
 'poolVerdict':'POST_RELEASE_CLASS_POOL_RESCUE_IMPOSSIBLE' if lb>pool+1e-7 else 'POST_RELEASE_CLASS_POOL_NOT_FALSIFIED_BY_PREFIX_LOWER_BOUND',
 'rows':rows,
 'branchEquivalentAccounting':{'successfulTraceBranchEquivalents':10,'primaryRepeat':4,'contextControls':6,'failedImportPreReplayExcluded':True,'limit':10},
 'sourceHashes':{str(mid):sha(p) for mid,p in paths.items()},'formalPLVACCompactSha256':sha(formal),'preregSha256':sha(prereg),
 'interpretation':[
   '1824852 budget breaches occur after selected P carrier release, but original H0 has no legal post-release native lever across the remaining horizon.',
   '1825994 independently shows the same no-post-release-H0-lever mechanism with formal budget breaches.',
   '1824758 is a negative/context control where rho and tau coincide, proving the scanner can identify an immediate legal lever.',
   '1825962 has no post-release H0 lever but no formal budget breach, so no-lever alone is not classified as harmful.',
   'The aggregate cashAtRiskPeak necessary lower bound does not itself exceed the frozen Pool; the decisive falsification is per-market absence of the hypothesized legal lever, not the Pool certificate.',
 ],
 'stopping':['no new behavior treatment','no action redesign executed','no belief model','no fresh/reserve/8781','report complete stop']
}
cp=OUT/'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_COMPACT_20260908.json'
cp.write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf-8')

def fmt_rho(r):
    x=r['rho']; return 'NONE' if x is None else f"phase {x['phaseOrdinal']} / {x['eventTimestampMs']}"
def fmt_tau(r):
    x=r['tau']; return 'NONE' if x is None else f"phase {x['phaseOrdinal']} / {x['eventTimestampMs']}"
lines=[]
lines += ['# B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1 — Formal Report','', 'Date: 2026-09-08  ', f'Final audit verdict: **`{final}`**  ', f'Pool necessary-condition verdict: **`{compact["poolVerdict"]}`**', '', '## 1. Correctness / scope','', f'- trace correctness + formal parity 4/4: {all_correct}', '- successful instrumentation replay: 10/10 permitted branch-equivalents (primary N/P repeated twice = 4; three context markets N/P = 6)', '- one failed 1824758 import attempt stopped before replay and is excluded from branch-equivalent accounting', '- no new economic action, no new market, no fresh/reserve/8781, no belief or runtime modification', '', '## 2. Four-row locus result','', '| Market | Formal budget | rho | tau | Locus verdict |', '|---|---|---|---|---|']
for r in rows:
    lines.append(f"| {r['marketId']} | {r['formalBudgetStatus']} | {fmt_rho(r)} | {fmt_tau(r)} | `{r['verdict']}` |")
lines += ['', '## 3. Primary witness 1824852','', 'The selected P carrier releases at phase 94 / 1788166231906. At release, none of the three formal failed metrics has crossed its frozen Tail.', '', '- R0LotBurdenTail crosses at phase 164 / 1788166251293 (+19.387s after rho).', '- grossPeak crosses at phase 336 / 1788166301889 (+69.983s after rho).', '- grossIntegral crosses at phase 820 / 1788166475902 (+243.996s after rho).', '', 'However, the post-release native menu census checks 738 pre-action phases and finds `sameH0Authority=true` exactly 0 times. Therefore tau does not exist. The hypothesis that the original P seed can be preserved and repaired only by reallocating post-release continuation within the same H0 is falsified.', '', 'This is specifically `POST_RELEASE_RESCUE_FALSIFIED_NO_NATIVE_HANDOFF_LEVER`, not pre-release budget commitment. The original H0 active continuation had already materialized before rho (DOWN_26 submit/fill before selected P release), and by rho the original H0 authority is no longer present.', '', '## 4. Context controls','', '- **1824758:** rho and tau coincide at phase 38. No formal Tail failure. This proves the legal-lever detector is capable of finding a valid immediate tau.', '- **1825962:** tau=NONE, but there is no formal budget failure. This prevents the audit from equating no-lever with economic harm.', '- **1825994:** tau=NONE and the formal cashAtRiskPeak/newBurden_UP/newPeak_UP breaches all occur after rho. This independently matches the primary no-post-release-H0-lever mechanism on a second budget-fail market.', '', '## 5. Cash-at-risk Pool necessary lower bound','', f'- post-release-class necessary lower-bound sum = **{lb:.12f}**', f'- frozen Pool = **{pool:.12f}**', f'- headroom at necessary lower bound = **{pool-lb:.12f}**', f'- verdict = **`{compact["poolVerdict"]}`**', '', 'The Pool lower-bound certificate does not by itself falsify the whole post-release class. The decisive failure is local: in the two formal budget-fail witnesses, the hypothesized same-H0 post-release lever does not exist.', '', '## 6. Research implication','', 'The next causal boundary, if studied, must move **earlier than selected-carrier confirmed release** and cannot be framed as “keep the P seed unchanged, then fix its same-H0 suffix after release.” A future redesign would need its own preregistration around earlier authority / reservation / occupancy allocation. This audit does not prove any such redesign will preserve the 1824852 bilateral endpoint gain.', '', '- `B5_NO_GENERALIZABLE_EDGE_REMAINS_PLAUSIBLE` remains.', '- `NO_ALPHA_PROMOTION` remains.', '- `REAL_NET_COST_UNRESOLVED` remains.', '- Belief/selector training remains unauthorized.', '', '**REPORT COMPLETE — STOP.**']
rp=OUT/'B3_PLVAC_POST_RELEASE_BUDGET_LOCUS_FALSIFICATION_AUDIT4_V1_REPORT_20260908.md'
rp.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'finalAuditVerdict':final,'poolVerdict':compact['poolVerdict'],'poolLB':lb,'pool':pool,'headroom':pool-lb,'compact':str(cp),'report':str(rp)},ensure_ascii=False,indent=2))
