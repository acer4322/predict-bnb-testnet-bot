import json, glob, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_stale_reprice_wave24_synthesis_v1.json'
NOTE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_testbed_progress_20260830_maker_stale_reprice_wave24_v1.md'

stale_files=sorted(RET.glob('r4-makeronly-stalereprice-*-v1/result.json'))
prearm_files=sorted(RET.glob('r4-makeronly-prearmcancel-*-v1/result.json'))
if len(stale_files)!=8:
    raise SystemExit(f'expected 8 stale artifacts, got {len(stale_files)}')
if len(prearm_files)!=8:
    raise SystemExit(f'expected 8 prearm artifacts, got {len(prearm_files)}')

rows=[]
for p in stale_files:
    d=json.loads(p.read_text(encoding='utf-8'))
    if d.get('guards',{}).get('noDreamFill') is not True or d.get('guards',{}).get('noTaker') is not True:
        raise SystemExit(f'guard mismatch: {p}')
    rows.extend(d.get('rows',[]))

by_mid={}
for r in rows:
    by_mid.setdefault(int(r['marketId']),{})[r['policy']]=r

policies=['ROLL_REPRICE_STALE_A500','ROLL_REPRICE_STALE_A1000','ROLL_REPRICE_STALE_A1500']
comparisons={}
for pol in policies:
    vals=[]
    for mid,m in sorted(by_mid.items()):
        if 'ROLL_KEEP' not in m or pol not in m: continue
        b=m['ROLL_KEEP']; x=m[pol]
        vals.append({
            'marketId':mid,
            'deltaFinalFloor':x['final']['floor']-b['final']['floor'],
            'deltaFinalAbsNet':x['final']['absNet']-b['final']['absNet'],
            'deltaMakerFilledShares':x['makerFilledShares']-b['makerFilledShares'],
            'everSafeBase':bool(b['everSafe']), 'everSafePolicy':bool(x['everSafe']),
            'repriceRequests':int(x.get('counts',{}).get('repriceRequests',0)),
            'cancelRequests':int(x.get('counts',{}).get('cancelRequests',0)),
        })
    floors=[v['deltaFinalFloor'] for v in vals]
    absnets=[v['deltaFinalAbsNet'] for v in vals]
    fills=[v['deltaMakerFilledShares'] for v in vals]
    comparisons[pol]={
        'markets':len(vals),
        'meanDeltaFinalFloor':statistics.fmean(floors) if floors else None,
        'medianDeltaFinalFloor':statistics.median(floors) if floors else None,
        'floorImprovedMarkets':sum(v>1e-9 for v in floors),
        'floorWorsenedMarkets':sum(v<-1e-9 for v in floors),
        'floorEqualMarkets':sum(abs(v)<=1e-9 for v in floors),
        'meanDeltaFinalAbsNet':statistics.fmean(absnets) if absnets else None,
        'meanDeltaMakerFilledShares':statistics.fmean(fills) if fills else None,
        'everSafeGained':sum((not v['everSafeBase']) and v['everSafePolicy'] for v in vals),
        'everSafeLost':sum(v['everSafeBase'] and (not v['everSafePolicy']) for v in vals),
        'repriceRequests':sum(v['repriceRequests'] for v in vals),
        'cancelRequests':sum(v['cancelRequests'] for v in vals),
        'rows':vals,
    }

# Test whether 500/1000/1500 thresholds produced distinct terminal outcomes at all.
identical=0; comparable=0
for mid,m in by_mid.items():
    if all(p in m for p in policies):
        comparable+=1
        sig=[(round(m[p]['final']['floor'],10),round(m[p]['final']['absNet'],10),round(m[p]['makerFilledShares'],10),int(m[p].get('counts',{}).get('repriceRequests',0))) for p in policies]
        if len(set(sig))==1: identical+=1

# Prearm artifacts exist, but audit whether they can answer the win-rate-first question.
prearm_rows=[]; prearm_aggregate_market_counts=[]; prearm_guard_ok=True
for p in prearm_files:
    d=json.loads(p.read_text(encoding='utf-8'))
    g=d.get('guards',{})
    prearm_guard_ok &= (g.get('dreamFill') is False and g.get('realisticHFT') is True and g.get('noTaker') is True)
    rr=d.get('rows',[]); prearm_rows.extend(rr)
    prearm_aggregate_market_counts.extend(int(v.get('markets',0) or 0) for v in d.get('aggregate',{}).values())
null_winner=sum(r.get('winner') is None for r in prearm_rows)
null_score=sum(r.get('score') is None for r in prearm_rows)

rep={
  'version':'R4_MAKER_STALE_REPRICE_WAVE24_SYNTHESIS_V1',
  'date':'2026-08-30',
  'status':'DEVELOPMENT_ANATOMY_EVIDENCE_ONLY',
  'researchOnly':True,'actionAuthority':False,
  'artifactAudit':{
      'staleRequiredArtifactsVerified':len(stale_files),
      'prearmRequiredArtifactsVerified':len(prearm_files),
      'duplicateLaunches':0,
  },
  'scientificBoundary':{'strictPast':True,'realisticHFT':True,'dreamFill':False,'freshPromotionEvidence':False,'liveMutation':False},
  'staleReprice':{
      'uniqueMarkets':len(by_mid),
      'comparisonVsRollKeep':comparisons,
      'thresholdOutcomeIdentity':{'comparableMarkets':comparable,'identicalAcross500_1000_1500':identical,'fractionIdentical':identical/comparable if comparable else None},
      'interpretation':'Age-only stale/far repricing is not supported as a win-rate-first Maker-flexibility primitive. It adds cancellation/reprice churn and, on this consumed anatomy cohort, terminal economics are often worse than passive ROLL_KEEP. The tested 500/1000/1500ms thresholds are effectively non-identifying because they produce the same terminal signature on nearly/all comparable markets; lifecycle arbitration needs state/reachability/economic context rather than an age threshold sweep.'
  },
  'prearmCancelAudit':{
      'requiredArtifacts':len(prearm_files),'rowsPresent':len(prearm_rows),'guardsOk':bool(prearm_guard_ok),
      'rowsWithNullWinner':null_winner,'rowsWithNullScore':null_score,
      'allAggregateMarketCountsZero':all(v==0 for v in prearm_aggregate_market_counts),
      'status':'ARTIFACT_PRESENT_BUT_WINRATE_UNSCORABLE' if prearm_rows and null_winner==len(prearm_rows) else 'SCORABLE',
      'interpretation':'Return code/artifact existence is not sufficient for the WIN RATE FIRST question: these prearm artifacts contain mechanism rows but no terminal winner/score labels and their aggregate market counts are zero. Treat as incomplete for primary win-rate evidence; do not count as completed win-rate research.'
  },
  'conclusion':'Do not promote global age-triggered stale reprice. Keep structural lifecycle eligibility and move arbitration toward carrier reachability/economics plus explicit winner-preservation; prearm-cancel wave must be repaired/rescored before it can contribute to win-rate-first evidence.'
}
OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')

p500=comparisons['ROLL_REPRICE_STALE_A500']
note=f'''# R4 Maker Flexibility First — stale-reprice wave24 synthesis v1\n\n## Artifact-first audit\nEight stale-reprice and eight prearm-cancel LAN return directories already existed with `result.json`; no jobs were relaunched.\n\n## Stale age-only reprice result\nConsumed realistic-HFT/no-dream-fill anatomy cohort: {len(by_mid)} markets. Against `ROLL_KEEP`, A500 mean terminal-floor delta = {p500['meanDeltaFinalFloor']:.4f} USDT, median = {p500['medianDeltaFinalFloor']:.4f}; improved/worsened/equal = {p500['floorImprovedMarkets']}/{p500['floorWorsenedMarkets']}/{p500['floorEqualMarkets']}. Total reprice requests = {p500['repriceRequests']}.\n\nAcross comparable markets, {identical}/{comparable} produced identical terminal signatures for 500/1000/1500ms stale thresholds. This means the age threshold sweep is not identifying the lifecycle decision. More churn is not the missing flexibility primitive.\n\n## Win-rate-first boundary\nThis stale-reprice wave did not use winner at runtime and is retained only as consumed anatomy evidence. It does **not** establish a market win-rate improvement versus frozen R3+R3.1. The result argues against global/age-only churn and supports stateful lifecycle arbitration around carrier reachability/economics.\n\n## Prearm-cancel artifact validity\nEight required artifacts exist, but {null_winner}/{len(prearm_rows)} rows have `winner=null`, {null_score}/{len(prearm_rows)} have `score=null`, and aggregate market counts are zero. Therefore these outputs are `ARTIFACT_PRESENT_BUT_WINRATE_UNSCORABLE`; process success/artifact presence must not be mistaken for completed win-rate evidence.\n\n## Next boundary\nContinue with strict-past structural eligibility and explicit winner-preservation. A prearm/cancel result must be repaired or post-episode rescored before it enters WIN RATE FIRST comparison. No fresh cohort was consumed, no live R3/R3.1/8781 mutation occurred, and no learned action authority was added.\n'''
NOTE.write_text(note,encoding='utf-8')
print(json.dumps({'out':str(OUT.relative_to(ROOT)),'note':str(NOTE.relative_to(ROOT)),'markets':len(by_mid),'a500':{k:v for k,v in p500.items() if k!='rows'},'thresholdIdentical':[identical,comparable],'prearmRows':len(prearm_rows),'prearmNullWinner':null_winner},ensure_ascii=False))
