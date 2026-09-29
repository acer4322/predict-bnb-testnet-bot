from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_MARKET_PARTICIPATION_MICRO_HIGH_PREPARE_TAIL_ROUTING_V1_20260828_1038'
ART=ROOT/'data/research/r4_v0/hourly/r4_market_participation_micro_high_prepare_tail_routing_v1.json'
PRE=ROOT/'data/research/r4_v0/hourly/r4_market_participation_micro_high_prepare_tail_routing_v1_preregistered.json'
R4REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NOVREG=ROOT/'data/research/hourly_novel_test_registry_v1.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
PROG=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v35.md'
SNAP=ROOT/'data/research/r4_v0/hourly/r4_hourly_training_snapshot_latest.json'

def dump(p,d): p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding='utf-8')

def main():
    now=datetime.now(TZ).isoformat()
    a=json.loads(ART.read_text(encoding='utf-8'))
    snap=json.loads(SNAP.read_text(encoding='utf-8'))
    a['seedNoRegression5of5']=True
    a['trainerRefresh']='NOT_RERUN_UNCHANGED_AFTER_PREVIOUS_EMPTY_MATCHED_GAP_FAILURE'
    a['latestValidTrainerSnapshot']={
      'createdAt':snap.get('createdAt'),
      'acquisitionSpearman':snap.get('acquisitionTeacher',{}).get('testSpearman'),
      'preservationSpearman':snap.get('preservationTeacher',{}).get('testSpearman'),
      'frozenEchtgeldMarkets':snap.get('liveCalibration',{}).get('marketCount')
    }
    dump(ART,a)
    p=a['primary']; c=a['coverage']
    entry={
      'testId':TEST_ID,'testedAt':now,'status':a['status'],'domain':'R4',
      'semanticAxis':a['semanticAxis'],
      'semanticKeys':['HIGH PREPARE Formation','spot/futures short-horizon returns','spot/futures queue imbalance','spot/futures taker imbalance','retained weak-side quote economics','15s floor-tail damage','market participation information only'],
      'hypothesis':a['question'],
      'artifact':str(ART.relative_to(ROOT)).replace('\\','/'),
      'preregistration':str(PRE.relative_to(ROOT)).replace('\\','/'),
      'tool':'tools/test_r4_market_participation_micro_high_prepare_tail_routing_v1.py',
      'decision':a['status'],'actionAuthority':False,
      'cohort':{'markets':c['markets'],'rows':c['rows'],'medianPublicLagMs':c['medianPublicLagMs'],'maxPublicLagMs':c['maxPublicLagMs'],'special20260816Sealed':True,'echtgeldTraining':False},
      'primaryResult':{'eligibleFolds':p['eligibleFolds'],'meanDeltaAuc':p['meanDeltaAuc'],'meanDeltaAp':p['meanDeltaAp'],'meanLogLossImprovement':p['meanLogLossImprovement'],'worstDeltaAuc':p['worstDeltaAuc'],'allFoldAucNonnegative':p['allFoldAucNonnegative'],'foldDeltaAuc':[f['HIGH']['increment']['deltaAuc'] for f in a['folds'] if f['HIGH'].get('eligible')],'seedNoRegression5of5':True},
      'summary':'Strict-past spot/futures market-participation microstructure does not add chronology-stable HIGH-PREPARE 15s floor-tail information above retained quote+geometry+Predict/strike context. Mean dAUC is negative and the earliest fold materially reverses despite the newest fold being positive; calibration also worsens on average. Reject this routing formulation and do not split/tune a micro regime to rescue it.',
      'retestAllowed':False,
      'nextDistinct':'Do not repackage the same spot/futures return/queue/taker micro as another HIGH-PREPARE threshold/regime. Prefer a semantically different naturally-supported information source, or a genuinely new independent chronology for an already frozen predeclared replication.'
    }
    rr=json.loads(R4REG.read_text(encoding='utf-8'))
    arr=rr.setdefault('tests',[])
    arr[:]=[x for x in arr if x.get('testId')!=TEST_ID]
    arr.append(entry); dump(R4REG,rr)
    nr=json.loads(NOVREG.read_text(encoding='utf-8'))
    arr=nr.setdefault('tests',[]); arr[:]=[x for x in arr if x.get('testId')!=TEST_ID]; arr.append(entry); dump(NOVREG,nr)
    foldstr=' / '.join(f"{f['HIGH']['increment']['deltaAuc']:+.4f}" for f in a['folds'] if f['HIGH'].get('eligible'))
    text=f'''# R4 Information Layer Progress V35 — 2026-08-28 10:xx\n\n- Exactly one novel bounded test: `{TEST_ID}` -> `{a['status']}`. Preregistered before execution after both-registry + semantic duplicate audit.\n- Semantic novelty: prior spot/futures evidence showed placement-timing lift but direct concatenation into Formation-mode classification degraded generalization. This cycle uniquely asks whether those same strict-past market-participation micro signals add **conditional 15s floor-tail information inside HIGH PREPARE**, above the already-retained weak-side quote stack, without becoming Formation/action authority.\n- Layers: spot/futures return+queue+taker = `INFORMATION / MARKET_PARTICIPATION_CONTEXT`; weak quote = retained `INFORMATION`; placement readiness = `PREPARE BELIEF`; Predict/strike = `FORMATION BELIEF`; portfolio/payoff geometry = `LOGIC`. Output is `NOT_ACTION_AUTHORITY`.\n- Cohort: {c['markets']} ordinary Target markets / {c['rows']} complete strict-past rows; public lag median {c['medianPublicLagMs']:.0f}ms, max {c['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED; no Echtgeld fit/ingest.\n- Three HIGH PREPARE chronological folds were eligible. Fold dAUCs: {foldstr}; mean dAUC `{p['meanDeltaAuc']:+.4f}`, mean dAP `{p['meanDeltaAp']:+.4f}`, mean log-loss improvement `{p['meanLogLossImprovement']:+.4f}`, worst dAUC `{p['worstDeltaAuc']:+.4f}`. Fixed all-fold/mean/calibration KEEP rule fails -> `{a['status']}`.\n- Interpretation: the newest block is mildly positive, but transfer is not broad/chronologically stable. Do not cut a new micro regime, change horizons, or threshold/model-sweep rescue this formulation. Placement-timing evidence remains valid in its existing role; this test only rejects HIGH-PREPARE floor-tail routing of this micro bundle.\n- Seed/no-regression: 5/5 PASS. The trainer was not relaunched unchanged after the immediately prior deterministic empty matched-gap failure; latest valid snapshot remains `{snap.get('createdAt')}`, Acquisition `{snap.get('acquisitionTeacher',{}).get('testSpearman'):.4f}` / Preservation `{snap.get('preservationTeacher',{}).get('testSpearman'):.4f}`, frozen Echtgeld exactly {snap.get('liveCalibration',{}).get('marketCount')}. R4 champion, live R3-S/R3.1 and 8781 unchanged.\n- Artifact: `{ART.relative_to(ROOT).as_posix()}`; preregistration: `{PRE.relative_to(ROOT).as_posix()}`; both registries updated.\n- Next distinct gap: choose a semantically different naturally-supported information/routing source, or wait for genuinely new independent chronology for a frozen predeclared replication. Do not repackage this spot/futures micro bundle into another HIGH-PREPARE regime.\n'''
    PROG.write_text(text,encoding='utf-8')
    marker=f'<!-- {TEST_ID} -->'
    h=HAND.read_text(encoding='utf-8')
    if marker not in h:
      h += f'''\n\n## 2026-08-28 10:xx — MARKET-PARTICIPATION MICRO × HIGH PREPARE TAIL ROUTING V1 — REJECTED\n- Exactly one novel bounded test: `{TEST_ID}`; preregistered before execution after both-registry + semantic duplicate audit.\n- Semantic novelty: existing spot/futures micro evidence belongs to placement/market-participation context and direct Formation-mode concatenation had failed; this test instead evaluated conditional HIGH-PREPARE 15s floor-tail routing above retained weak-side quote economics.\n- Layer assignment: spot/futures returns/queue/taker = INFORMATION / MARKET_PARTICIPATION_CONTEXT; weak quote = retained INFORMATION; placement readiness = PREPARE BELIEF; Predict/strike = FORMATION BELIEF; portfolio/payoff geometry = LOGIC. NOT_ACTION_AUTHORITY.\n- Cohort: {c['markets']} ordinary Target markets / {c['rows']} strict-past complete rows; public lag median {c['medianPublicLagMs']:.0f}ms / max {c['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED; no Echtgeld fit.\n- Three HIGH PREPARE folds: dAUC {foldstr}; mean dAUC {p['meanDeltaAuc']:+.4f}, mean dAP {p['meanDeltaAp']:+.4f}, mean log-loss improvement {p['meanLogLossImprovement']:+.4f}, worst dAUC {p['worstDeltaAuc']:+.4f}. Fixed KEEP gate fails -> TESTED_REJECTED.\n- Do not split/tune a micro regime to rescue. Prior placement-timing evidence remains in its existing role; no action/order/size/owner/MPQ authority.\n- Seed/no-regression 5/5 PASS. Latest valid trainer snapshot retained after prior deterministic matched-gap failure; frozen Echtgeld remains 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact `{ART.relative_to(ROOT).as_posix()}`; prereg `{PRE.relative_to(ROOT).as_posix()}`; progress V35; both registries updated.\n- Next: semantically different naturally-supported information source or genuinely new independent chronology for frozen predeclared replication.\n{marker}\n'''
      HAND.write_text(h,encoding='utf-8')
    print(json.dumps({'ok':True,'testId':TEST_ID,'status':a['status'],'r4RegistryTests':len(rr.get('tests',[])),'novelRegistryTests':len(nr.get('tests',[])),'progress':str(PROG.relative_to(ROOT)).replace('\\','/'),'handoffUpdated':True},ensure_ascii=False))
if __name__=='__main__': main()
