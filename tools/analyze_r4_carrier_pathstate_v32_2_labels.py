from pathlib import Path
import json,math
ROOT=Path(__file__).resolve().parents[1]
U=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_pathstate_v32_2_features_unlabeled.json'
L=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_summary.json'
O=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_pathstate_v32_2_labeled_anatomy.json'
u=json.loads(U.read_text(encoding='utf-8'));l=json.loads(L.read_text(encoding='utf-8'))
labels={int(r['marketId']):r for r in l['rows'] if r.get('label') in {'BENEFICIAL','HARMFUL'}}
rows=[]
for x in u['rows']:
    mid=int(x['marketId'])
    if mid not in labels: continue
    y=dict(x);y['label']=labels[mid]['label'];y['cohort']=labels[mid].get('cohort');y['deltaPnl']=labels[mid].get('deltaPnl');rows.append(y)
def auc(vals):
    p=[v for v,y in vals if y==1];n=[v for v,y in vals if y==0]
    if not p or not n:return None
    s=0
    for a in p:
        for b in n:s+=1 if a>b else .5 if a==b else 0
    return s/(len(p)*len(n))
features=['priorSubmitCount','priorRepriceSubmitCount','priorOptionSubmitCount','priorMainSubmitCount','priorDistinctPriceCount','priceMigrationTicks','timeSinceLastSubmitMs','priorFillEventCount','priorFillShares','timeSinceLastFillMs','logicalFilledQty','secondsPastNeed']
fa={}
for f in features:
    vals=[]
    for r in rows:
        v=r.get(f)
        if v is None:continue
        try:v=float(v)
        except:continue
        vals.append((v,1 if r['label']=='BENEFICIAL' else 0))
    a=auc(vals);fa[f]={'n':len(vals),'auc':a,'aucAbs':None if a is None else max(a,1-a),'benefitWhenHigher':None if a is None else a>=.5}
# cohort directional check
for f in features:
    by={}
    for c in ['A','B','C']:
        vals=[]
        for r in rows:
            if r.get('cohort')!=c or r.get(f) is None:continue
            vals.append((float(r[f]),1 if r['label']=='BENEFICIAL' else 0))
        by[c]=auc(vals)
    fa[f]['cohortAuc']=by
# key counterexamples + strongest preregistered lifecycle axis
watch=[1807343,1810070,1806147,1803520,1804514,1804896,1806352,1806967]
watchrows=[{k:r.get(k) for k in ['marketId','label','cohort','priorSubmitCount','priorDistinctPriceCount','priceMigrationTicks','timeSinceLastSubmitMs','priorFillEventCount','priorFillShares','timeSinceLastFillMs','logicalFilledQty','secondsPastNeed']} for r in rows if int(r['marketId']) in watch]
rep={'version':'R4_CARRIER_PATHSTATE_V32_2_LABELED_ANATOMY','researchOnly':True,'labelsJoinedAfterUnlabeledArtifactFreeze':True,'unlabeledArtifact':str(U.relative_to(ROOT)),'labelSource':str(L.relative_to(ROOT)),'n':len(rows),'beneficial':sum(r['label']=='BENEFICIAL' for r in rows),'harmful':sum(r['label']=='HARMFUL' for r in rows),'featureAuc':fa,'watchRows':watchrows,'rows':rows}
O.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(O),'n':len(rows),'featureAuc':fa,'watchRows':watchrows},ensure_ascii=False))
