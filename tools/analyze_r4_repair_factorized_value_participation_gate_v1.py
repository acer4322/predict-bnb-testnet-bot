from __future__ import annotations

import json, math
from collections import Counter
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from tools.evaluate_r4_repair_exact_pathstate_ab_v1 import (
    load_teacher, load_path, enriched_values, impute, BASE_FEATURES
)

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
OVERLAY=P/'r4_repair_participation_aware_teacher_overlay_v1.json'
OUT=P/'r4_repair_factorized_value_participation_gate_probe_v1.json'

ECON_FEATURES=BASE_FEATURES+['quoteOffsetGapTicks']
COLLAPSE_FEATURES=BASE_FEATURES


def model():
    return make_pipeline(StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',max_iter=2000,random_state=20260830))


def fit_prob(rows, features, positive_label, eligible_labels, held_mid):
    train=[r for r in rows if r['mid']!=held_mid and r['label'] in eligible_labels]
    test=next(r for r in rows if r['mid']==held_mid)
    X=np.asarray([[r['v'].get(f,math.nan) for f in features] for r in train],float)
    y=np.asarray([1 if r['label']==positive_label else 0 for r in train],int)
    Xt=np.asarray([[test['v'].get(f,math.nan) for f in features]],float)
    if len(set(y.tolist()))<2:
        return math.nan
    X,Xt=impute(X,Xt)
    m=model(); m.fit(X,y)
    return float(m.predict_proba(Xt)[0,1])


def safe_rate(n,d): return float(n/d) if d else math.nan


def main():
    teacher=load_teacher(); path=load_path()
    overlay=json.loads(OVERLAY.read_text(encoding='utf-8'))
    labels={int(r['marketId']):str(r['participationAwareTeacherClass']) for r in overlay['rows']}
    rows=[]
    for mid in sorted(set(teacher)&set(path)&set(labels)):
        label=labels[mid]
        if label not in {'BENEFICIAL_PARTICIPATION_RETAINED','BENEFICIAL_BUT_PARTICIPATION_COLLAPSE','PARETO_HARMFUL','TRADEOFF','NO_EFFECT'}:
            continue
        # EXACT_STATE_PATH computes quoteOffsetGapTicks from exact pre-decision path summary.
        v=enriched_values(teacher[mid],path[mid],'EXACT_STATE_PATH')
        rows.append({'mid':mid,'label':label,'v':v})

    econ_eligible={'BENEFICIAL_PARTICIPATION_RETAINED','PARETO_HARMFUL'}
    collapse_eligible={'BENEFICIAL_PARTICIPATION_RETAINED','BENEFICIAL_BUT_PARTICIPATION_COLLAPSE'}
    scored=[]
    for r in rows:
        mid=r['mid']
        pe=fit_prob(rows,ECON_FEATURES,'BENEFICIAL_PARTICIPATION_RETAINED',econ_eligible,mid)
        pc=fit_prob(rows,COLLAPSE_FEATURES,'BENEFICIAL_BUT_PARTICIPATION_COLLAPSE',collapse_eligible,mid)
        econ_approve=bool(pe>=0.5) if math.isfinite(pe) else False
        factor_approve=bool(pe>=0.5 and pc<0.5) if math.isfinite(pe) and math.isfinite(pc) else False
        scored.append({'marketId':mid,'class':r['label'],'pEconomicRetainedBenefit':pe,'pParticipationCollapseConditionalBenefit':pc,'economicOnlyApprove050':econ_approve,'factorizedApprove050':factor_approve})

    def metrics(key):
        a=[x for x in scored if x[key]]
        cnt=Counter(x['class'] for x in a); allc=Counter(x['class'] for x in scored)
        retained=cnt['BENEFICIAL_PARTICIPATION_RETAINED']; harmful=cnt['PARETO_HARMFUL']; collapse=cnt['BENEFICIAL_BUT_PARTICIPATION_COLLAPSE']
        return {
            'approvedN':len(a), 'approvedClassCounts':dict(cnt),
            'retainedBeneficialRecall':safe_rate(retained,allc['BENEFICIAL_PARTICIPATION_RETAINED']),
            'harmfulFalseApproveRate':safe_rate(harmful,allc['PARETO_HARMFUL']),
            'collapseFalseApproveRate':safe_rate(collapse,allc['BENEFICIAL_BUT_PARTICIPATION_COLLAPSE']),
            'approvePrecisionRetainedBeneficial':safe_rate(retained,len(a)),
            'nonRetainedApproveRate':safe_rate(len(a)-retained,len(a)),
        }

    result={
      'version':'R4_REPAIR_FACTORIZED_VALUE_PARTICIPATION_GATE_PROBE_V1',
      'date':'2026-08-30','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'freshCohortConsumed':False,
      'question':'Does a factorized strict-past economic-value head plus conditional participation-collapse risk head reduce false adaptive success versus economic-value prediction alone?',
      'joinedMarkets':len(rows),'classCounts':dict(Counter(r['label'] for r in rows)),
      'representations':{
        'economicHead':{'features':ECON_FEATURES,'positive':'BENEFICIAL_PARTICIPATION_RETAINED','negative':'PARETO_HARMFUL','sourceRationale':'reuse prior consumed-development EXACT_STATE+quote representation; no new threshold search'},
        'participationCollapseHead':{'features':COLLAPSE_FEATURES,'positive':'BENEFICIAL_BUT_PARTICIPATION_COLLAPSE','negative':'BENEFICIAL_PARTICIPATION_RETAINED','sourceRationale':'reuse prior consumed-development best EXACT_STATE representation; conditional teacher only'},
      },
      'validation':'market-level leave-one-out for each head; fixed 0.50/0.50 architecture audit; no threshold sweep; counterfactual outcomes used only as post-episode labels',
      'economicOnly050':metrics('economicOnlyApprove050'),
      'factorized050':metrics('factorizedApprove050'),
      'rows':scored,
      'interpretationBoundary':'Consumed-development architecture probe only. It may justify keeping separate heads, but cannot authorize REPAIR_NOW/WAIT or count as fresh promotion evidence.',
      'guards':['strict-past features only','realistic-HFT exact-seam teacher labels only','no dream fill','Protection deterministic and unchanged','no live R3/R3.1/8781 mutation','no <=180s new exposure authority','Passive Maker/Formation participation is a required success dimension']
    }
    OUT.write_text(json.dumps(result,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['joinedMarkets','classCounts','economicOnly050','factorized050']},indent=2,allow_nan=True))

if __name__=='__main__': main()
