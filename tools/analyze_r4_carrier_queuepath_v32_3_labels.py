from __future__ import annotations
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
U=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_queuepath_v32_3_features_unlabeled.json'
L=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_summary.json'
O=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_queuepath_v32_3_labeled_anatomy.json'
def auc(vals):
    p=[v for v,y in vals if y==1];n=[v for v,y in vals if y==0]
    if not p or not n:return None
    s=0.0
    for a in p:
        for b in n:s+=1.0 if a>b else .5 if a==b else 0.0
    return s/(len(p)*len(n))
u=json.loads(U.read_text(encoding='utf-8'));l=json.loads(L.read_text(encoding='utf-8'))
lab={int(r['marketId']):r for r in l['rows'] if r.get('label') in {'BENEFICIAL','HARMFUL'}}
rows=[]
for x in u['rows']:
    mid=int(x['marketId'])
    if mid not in lab:continue
    y=dict(x);y['label']=lab[mid]['label'];y['cohort']=lab[mid].get('cohort');y['deltaPnl']=lab[mid].get('deltaPnl');rows.append(y)
metrics=['queueStartShares','queueSeamShares','queueChangeShares','queueChangeNorm','matchDepletionShares','matchDepletionNorm','netProgressShares','netProgressNorm']
fa={}
for pr in ['original','planned']:
  for lb in ['1000','3000']:
    for m in metrics:
      key=f'{pr}_{lb}_{m}';vals=[];by={}
      for r in rows:
        v=r['queuePath'][pr][lb].get(m)
        if v is None:continue
        vals.append((float(v),1 if r['label']=='BENEFICIAL' else 0))
      a=auc(vals)
      for c in ['A','B','C']:
        vv=[]
        for r in rows:
          if r.get('cohort')!=c:continue
          v=r['queuePath'][pr][lb].get(m)
          if v is None:continue
          vv.append((float(v),1 if r['label']=='BENEFICIAL' else 0))
        by[c]=auc(vv)
      fa[key]={'n':len(vals),'auc':a,'aucAbs':None if a is None else max(a,1-a),'benefitWhenHigher':None if a is None else a>=.5,'cohortAuc':by}
watch={1807343,1810070,1806147,1803520,1804514,1804896,1806352,1806967}
watchrows=[]
for r in rows:
    if int(r['marketId']) not in watch:continue
    z={'marketId':r['marketId'],'label':r['label'],'cohort':r['cohort'],'originalPx':r['originalPx'],'plannedSubmitPx':r['plannedSubmitPx']}
    for pr in ['original','planned']:
      for lb in ['1000','3000']:
        q=r['queuePath'][pr][lb]
        z[f'{pr}_{lb}_qStart']=q['queueStartShares'];z[f'{pr}_{lb}_qChange']=q['queueChangeShares'];z[f'{pr}_{lb}_depletion']=q['matchDepletionShares'];z[f'{pr}_{lb}_netProgress']=q['netProgressShares'];z[f'{pr}_{lb}_netProgressNorm']=q['netProgressNorm']
    watchrows.append(z)
ranked=sorted([(k,v) for k,v in fa.items() if v['aucAbs'] is not None],key=lambda kv:(kv[1]['aucAbs'],kv[1]['n']),reverse=True)
rep={'version':'R4_CARRIER_QUEUEPATH_V32_3_LABELED_ANATOMY','researchOnly':True,'labelsJoinedAfterUnlabeledArtifactFreeze':True,'unlabeledArtifact':str(U.relative_to(ROOT)),'labelSource':str(L.relative_to(ROOT)),'n':len(rows),'beneficial':sum(r['label']=='BENEFICIAL' for r in rows),'harmful':sum(r['label']=='HARMFUL' for r in rows),'featureAuc':fa,'topByAucAbs':ranked[:16],'watchRows':watchrows,'rows':rows}
O.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(O),'n':len(rows),'top':ranked[:12],'watchRows':watchrows},ensure_ascii=False))
