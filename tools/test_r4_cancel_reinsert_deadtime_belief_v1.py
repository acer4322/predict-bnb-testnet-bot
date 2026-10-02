from __future__ import annotations
import json, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cancel_reinsert_deadtime_belief_v1.json'
TZ=ZoneInfo('Asia/Taipei')
FEATURES=['orderAgeMs','quoteOffsetTicks','spreadTicks','partialFillRatio','recoveryDeficit','secondsLeft']

def f(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else 0.0
    except Exception:return 0.0

def mae(y,p): return float(np.mean(np.abs(np.asarray(y)-np.asarray(p)))) if y else None

def main():
    d=json.load(open(SRC,encoding='utf-8'))
    rows=[]
    for r in d.get('rows',[]):
        if not r.get('eligible'): continue
        ev=r.get('reinsertEvent') or {}; q=r.get('queueFeatures') or {}
        if ev.get('cancelRequestedAtMs') is None or ev.get('reinsertedAtMs') is None: continue
        y=float(ev['reinsertedAtMs'])-float(ev['cancelRequestedAtMs'])
        if y<=0: continue
        rows.append({'marketId':int(r['marketId']),'candidateAtMs':int(r.get('candidateAtMs') or ev['cancelRequestedAtMs']),
                     'x':[f(q.get(k)) for k in FEATURES],'y':y})
    rows.sort(key=lambda z:z['candidateAtMs'])
    min_train=12; preds=[]
    for i in range(min_train,len(rows)):
        tr=rows[:i]; te=rows[i]
        X=np.asarray([z['x'] for z in tr],float); y=np.asarray([z['y'] for z in tr],float)
        base=float(np.median(y))
        model=make_pipeline(StandardScaler(),Ridge(alpha=10.0)).fit(X,y)
        pred=float(model.predict(np.asarray([te['x']],float))[0])
        preds.append({'marketId':te['marketId'],'actualMs':te['y'],'baselineMs':base,'candidateMs':pred})
    ya=[z['actualMs'] for z in preds]; pb=[z['baselineMs'] for z in preds]; pc=[z['candidateMs'] for z in preds]
    bm=mae(ya,pb); cm=mae(ya,pc); imp=(bm-cm)/bm if bm and bm>0 else None
    sp=float(spearmanr(ya,pc).statistic) if len(preds)>=3 else None
    h=len(preds)//2
    blocks=[]
    for name,a in [('EARLY',preds[:h]),('LATE',preds[h:])]:
        y=[z['actualMs'] for z in a]; b=[z['baselineMs'] for z in a]; c=[z['candidateMs'] for z in a]
        bma=mae(y,b); cma=mae(y,c); blocks.append({'name':name,'n':len(a),'baselineMaeMs':bma,'candidateMaeMs':cma,'maeImprovementFraction':((bma-cma)/bma if bma else None)})
    support=len(preds)>=15
    keep=bool(support and imp is not None and imp>=.05 and sp is not None and sp>=.25 and all(z['maeImprovementFraction'] is not None and z['maeImprovementFraction']>=0 for z in blocks))
    reject=bool(support and not keep and ((imp is not None and imp<=0) or (sp is not None and sp<=0)))
    decision='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED' if reject else 'TESTED_INCONCLUSIVE'
    rep={'version':'R4_CANCEL_REINSERT_DEADTIME_BELIEF_V1','createdAt':datetime.now(TZ).isoformat(),'decision':decision,
         'semanticNovelty':'Predict cancel-request -> replacement-submit dead-time itself; prior work used fixed ~2.3s constant or studied queue quality/fill shares.',
         'layerAssignment':{'strictPastOrderMarketState':'EXECUTION_INFORMATION','predictedDeadtime':'EXECUTION_LIFECYCLE_BELIEF_CANDIDATE','authority':'NOT_ACTION_AUTHORITY'},
         'cohort':{'eligibleRows':len(rows),'oosPredictions':len(preds),'source':'R2_QUEUE_OPTION_COUNTERFACTUAL_V1','noEchtgeldFit':True,'sealed20260816':True},
         'features':FEATURES,'label':'future reinsertedAtMs-cancelRequestedAtMs offline only','baselineMaeMs':bm,'candidateMaeMs':cm,'maeImprovementFraction':imp,'spearman':sp,'blocks':blocks,
         'gate':{'minOos':15,'maeImprovementRequired':.05,'spearmanRequired':.25,'allBlockMaeImprovementNonnegative':True,'supportPass':support,'keep':keep},
         'guards':{'noThresholdSweep':True,'noActionAuthority':True,'noLiveR3Change':True,'no8781Change':True},'predictions':preds}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({k:rep[k] for k in ['decision','cohort','baselineMaeMs','candidateMaeMs','maeImprovementFraction','spearman','blocks','gate']},ensure_ascii=False))
if __name__=='__main__': main()
