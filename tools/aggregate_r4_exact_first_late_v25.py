from __future__ import annotations
import glob,json,sqlite3,math
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_exact_first_late_v25_summary.json'

def load_rows(pattern):
    out={}
    for p in glob.glob(str(RET/pattern)):
        rp=Path(p)/'result.json'
        if not rp.exists(): continue
        d=json.loads(rp.read_text(encoding='utf-8'))
        for r in d.get('rows',[]): out[int(r['marketId'])]=r
    return out

def pnl(r,w):
    f=r['final']; return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])

cand=load_rows('r4-exactlate-v25-*')
h2={}
for pat in ['r4-cont-h2s-chal24-*','r4-cont-h2s-chalb-*','r4-profit-v2c-pair-*']:
    for p in glob.glob(str(RET/pat)):
        rp=Path(p)/'result.json'
        if not rp.exists():continue
        d=json.loads(rp.read_text(encoding='utf-8'))
        for r in d.get('rows',[]):
            if r.get('config')=='CONT_STATE_H2_SUSPEND':h2[int(r['marketId'])]=r
ids=sorted(set(cand)&set(h2))
q=','.join('?'*len(ids)) if ids else 'NULL'
db=sqlite3.connect(str(ROOT/'data/target_wallet_official_v1.db'))
wins={int(a):str(b) for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids) if b in ('UP','DOWN')} if ids else {}
db.close()
ext=json.loads((ROOT/'data/research/r4_v0/p0_provenance_v1/r4_external_value_state_v1.json').read_text(encoding='utf-8'))
ext_by={int(r['marketId']):r for r in ext.get('rows',[])}
rows=[]
for m in ids:
    if m not in wins: continue
    hp=pnl(h2[m],wins[m]); cp=pnl(cand[m],wins[m]); delta=cp-hp
    er=ext_by.get(m,{})
    rows.append({'marketId':m,'winner':wins[m],'h2Pnl':hp,'exactPnl':cp,'deltaPnl':delta,'label':'BENEFICIAL' if delta>1e-9 else 'HARMFUL' if delta<-1e-9 else 'NEUTRAL','counts':cand[m].get('counts',{}),'features':er.get('features',{}),'cohort':er.get('cohort')})
features=['rv10sMeanBps','alphaSupportMean','directionAligned','predictAligned','spotQIAligned','futuresQIAligned','spotTaker1sAligned','futuresTaker1sAligned','spotRet5sAligned','futuresRet5sAligned','pairEdge','floor','upside','absNet']
auc={}
lab=[r for r in rows if r['label']!='NEUTRAL']
for f in features:
    xy=[]
    for r in lab:
        try:x=float(r['features'].get(f))
        except:continue
        if math.isfinite(x):xy.append((x,1 if r['label']=='BENEFICIAL' else 0,r.get('cohort')))
    def calc(z):
        if len(z)<4 or len({y for _,y,_ in z})<2:return None
        y=[y for _,y,_ in z];x=[x for x,_,_ in z];a=float(roc_auc_score(y,x));return {'n':len(z),'auc':a,'aucAbs':max(a,1-a),'benefitWhenHigher':a>=.5}
    auc[f]={'ALL':calc(xy)}
    for c in ['A','B','C']: auc[f][c]=calc([z for z in xy if z[2]==c])
summary={'version':'R4_EXACT_FIRST_LATE_V25_SUMMARY','researchOnly':True,'eventLevelCausal':True,'n':len(rows),'beneficial':sum(r['label']=='BENEFICIAL' for r in rows),'harmful':sum(r['label']=='HARMFUL' for r in rows),'neutral':sum(r['label']=='NEUTRAL' for r in rows),'h2Pnl':sum(r['h2Pnl'] for r in rows),'exactPnl':sum(r['exactPnl'] for r in rows),'deltaPnl':sum(r['deltaPnl'] for r in rows),'rows':rows,'featureAuc':auc}
OUT.write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({k:summary[k] for k in ['n','beneficial','harmful','neutral','h2Pnl','exactPnl','deltaPnl']},ensure_ascii=False))
for f in features:
    print(f,auc[f])
print('artifact',OUT)
