from __future__ import annotations
import json, math
from pathlib import Path
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_summary.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_reservation_guard_v28_deterministic.json'
d=json.loads(SRC.read_text(encoding='utf-8'))
rows=[]
weights={'predictAligned':0.30,'spotQIAligned':0.20,'futuresQIAligned':0.20,'spotTaker1sAligned':0.15,'futuresTaker1sAligned':0.15}
for r in d.get('rows',[]):
    if r.get('label')=='NEUTRAL': continue
    f=r.get('features') or {}
    vals={}
    ok=True
    for k,w in weights.items():
        try: v=float(f.get(k))
        except: ok=False; break
        if not math.isfinite(v): ok=False; break
        vals[k]=v
    if not ok: continue
    score=sum(weights[k]*vals[k] for k in weights)
    y=1 if r['label']=='BENEFICIAL' else 0
    pred=1 if score>=0.0 else 0
    rows.append({'marketId':r['marketId'],'cohort':r.get('cohort'),'label':r['label'],'deltaPnl':r['deltaPnl'],'score':score,'allow':bool(pred),'features':vals})
def metrics(z):
    if not z: return None
    y=[1 if r['label']=='BENEFICIAL' else 0 for r in z]; s=[r['score'] for r in z]; p=[1 if r['allow'] else 0 for r in z]
    auc=float(roc_auc_score(y,s)) if len(set(y))>1 else None
    ba=float(balanced_accuracy_score(y,p)) if len(set(y))>1 else None
    allowed=[r for r in z if r['allow']]
    return {'n':len(z),'beneficial':sum(x==1 for x in y),'harmful':sum(x==0 for x in y),'auc':auc,'balancedAccuracyAtZero':ba,'allowN':len(allowed),'allowBeneficial':sum(r['label']=='BENEFICIAL' for r in allowed),'allowHarmful':sum(r['label']=='HARMFUL' for r in allowed),'allowedDeltaPnl':sum(float(r['deltaPnl']) for r in allowed),'allDeltaPnl':sum(float(r['deltaPnl']) for r in z)}
rep={'version':'R4_RESERVATION_GUARD_V28_DETERMINISTIC','researchOnly':True,'strictPastInputsOnly':True,'futureOutcomeRuntimeInput':False,'formula':{'score':'0.30*predictAligned + 0.20*spotQIAligned + 0.20*futuresQIAligned + 0.15*spotTaker1sAligned + 0.15*futuresTaker1sAligned','allow':'score >= 0','fitToOutcome':False},'all':metrics(rows),'byCohort':{c:metrics([r for r in rows if r.get('cohort')==c]) for c in ['A','B','C']},'rows':rows,'interpretationBoundary':'Information/anatomy only; no learned or deterministic action authority. Causal HFT requires preregistered integration and realistic-HFT.'}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'all':rep['all'],'byCohort':rep['byCohort'],'artifact':str(OUT.relative_to(ROOT))},ensure_ascii=False))
