from __future__ import annotations
import bisect, glob, json, math, sqlite3, statistics
from pathlib import Path
from typing import Any
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
PUBLIC=ROOT/'data/public_source_snapshot_archive_v2.db'
WINDB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_hold_vs_complete_value_v1.json'
COHORTS={
 'A':('r4-cont-h2s-chal24-*','r4-cont-latecomp-v17-a-*','r4-cont-latetrace-v18-a-*'),
 'B':('r4-cont-h2s-chalb-*','r4-cont-latecomp-v17-b-*','r4-cont-latetrace-v18-b-*'),
 'C':('r4-profit-v2c-pair-*','r4-profit-c-late-v17-*','r4-latetrace-v18-c-*'),
}

def load(pattern):
 out=[]
 for p in glob.glob(str(RET/pattern)):
  rp=Path(p)/'result.json'
  if not rp.exists():continue
  try:out.extend((json.loads(rp.read_text(encoding='utf-8')).get('rows') or []))
  except Exception:pass
 return out

def cfgmap(rows,cfg):return {int(r['marketId']):r for r in rows if r.get('config')==cfg}
def pnl(r,w):
 f=r['final'];return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])
def num(x):
 try:
  z=float(x);return z if math.isfinite(z) else None
 except Exception:return None

def strict_bundle(db,mid,t,window=10000):
 rr=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? and sampled_at_ms>=? and sampled_at_ms<? order by sampled_at_ms',(int(mid),int(t-window),int(t))).fetchall();out=[]
 for ts,raw in rr:
  try:out.append((int(ts),json.loads(raw)))
  except Exception:pass
 return out

def rv_bps(bundle,key):
 vals=[num(z.get(key)) for _,z in bundle];vals=[x for x in vals if x is not None and x>0]
 if len(vals)<3:return None
 rr=[math.log(vals[i]/vals[i-1])*1e4 for i in range(1,len(vals)) if vals[i-1]>0 and vals[i]>0]
 return statistics.pstdev(rr) if len(rr)>=2 else None

def auc(y,x):
 a=[(yy,xx) for yy,xx in zip(y,x) if xx is not None and math.isfinite(float(xx))]
 if len(a)<4 or len({z[0] for z in a})<2:return None
 yy=[z[0] for z in a];xx=[z[1] for z in a];v=roc_auc_score(yy,xx)
 return {'n':len(a),'auc':float(v),'aucAbs':float(max(v,1-v)),'benefitWhenHigher':bool(v>=.5)}
def med(xs):
 z=[float(x) for x in xs if x is not None and math.isfinite(float(x))];return None if not z else float(statistics.median(z))

H={};V={};T={};CO={}
for tag,(hp,vp,tp) in COHORTS.items():
 h=cfgmap(load(hp),'CONT_STATE_H2_SUSPEND');v=cfgmap(load(vp),'CONT_STATE_H2_SUSPEND_LATE_COMPLETE_TOUCH1');t=cfgmap(load(tp),'CONT_STATE_H2_SUSPEND_LATE_COMPLETE_TOUCH1')
 for m,r in h.items():H[m]=r;CO[m]=tag
 V.update(v);T.update(t)
ids=sorted(set(H)&set(V)&set(T));q=','.join('?'*len(ids))
wins={}
with sqlite3.connect(WINDB) as db:
 for m,w in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids):
  if w in ('UP','DOWN'):wins[int(m)]=str(w)

rows=[]
with sqlite3.connect(PUBLIC) as db:
 for mid in ids:
  tr=T[mid].get('lateActionTrace') or []
  if not tr or mid not in wins:continue
  e=tr[0];t=int(e['t']);pre=e.get('pre') or {};up=num(pre.get('up')) or 0.;dn=num(pre.get('down')) or 0.;cu=num(pre.get('cu')) or 0.;cd=num(pre.get('cd')) or 0.
  dom='UP' if up>dn else 'DOWN' if dn>up else None
  if dom is None:continue
  bun=strict_bundle(db,mid,t,10000)
  if not bun:continue
  ts,z=bun[-1]
  if t-ts>1000:continue
  fair_up=num(z.get('predictUpMid'));sec=num(z.get('secondsLeft'))
  if fair_up is None or sec is None:continue
  fair= fair_up if dom=='UP' else 1.0-fair_up
  qty=abs(up-dn)
  avg_cost=(cu/up) if dom=='UP' and up>0 else (cd/dn) if dom=='DOWN' and dn>0 else None
  if avg_cost is None:continue
  fair_edge_per_share=fair-avg_cost
  alpha_value=qty*fair_edge_per_share
  rv=[x for x in (rv_bps(bun,'spotPrice'),rv_bps(bun,'futuresPrice')) if x is not None]
  rv_mean=None if not rv else float(sum(rv)/len(rv))
  # External-research anchored risk-pressure proxy. Scale-free for classification; gamma intentionally omitted.
  risk_pressure=None if rv_mean is None else qty*(rv_mean**2)*max(sec,0.0)
  risk_sqrt=None if rv_mean is None else qty*rv_mean*math.sqrt(max(sec,0.0))
  upside=num(pre.get('upside'));floor=num(pre.get('floor'));pair_edge=num(pre.get('edge'))
  alpha_to_risk=None if risk_sqrt in (None,0) else alpha_value/risk_sqrt
  upside_to_risk=None if risk_sqrt in (None,0) or upside is None else upside/risk_sqrt
  # Equal-weight economic state using signs only: high risk pushes toward completion; positive alpha/upside push toward hold.
  # No outcome-fitted coefficient or threshold.
  risk_log=None if risk_pressure is None else math.log1p(max(0.0,risk_pressure))
  alpha_signed=alpha_value
  delta=pnl(V[mid],wins[mid])-pnl(H[mid],wins[mid])
  rows.append({'marketId':mid,'cohort':CO[mid],'t':t,'dominantSide':dom,'label':'BENEFICIAL' if delta>1e-9 else 'HARMFUL' if delta<-1e-9 else 'NEUTRAL','deltaPnl':delta,
    'features':{'qty':qty,'avgDominantCost':avg_cost,'fairDominantProb':fair,'fairEdgePerShare':fair_edge_per_share,'directionalAlphaValue':alpha_value,'rv10MeanBps':rv_mean,'secondsLeft':sec,'riskPressure':risk_pressure,'riskSqrt':risk_sqrt,'alphaToRisk':alpha_to_risk,'upside':upside,'floor':floor,'pairEdge':pair_edge,'upsideToRisk':upside_to_risk,'riskLog':risk_log}})

features=sorted({k for r in rows for k in r['features']})
summary={}
for tag in ['A','B','C','ALL']:
 rr=rows if tag=='ALL' else [r for r in rows if r['cohort']==tag];nz=[r for r in rr if r['label']!='NEUTRAL'];y=[1 if r['label']=='BENEFICIAL' else 0 for r in nz]
 summary[tag]={'n':len(rr),'beneficial':sum(r['label']=='BENEFICIAL' for r in rr),'harmful':sum(r['label']=='HARMFUL' for r in rr),'neutral':sum(r['label']=='NEUTRAL' for r in rr),'features':{}}
 for f in features:
  summary[tag]['features'][f]={'beneficialMedian':med([r['features'].get(f) for r in rr if r['label']=='BENEFICIAL']),'harmfulMedian':med([r['features'].get(f) for r in rr if r['label']=='HARMFUL']),'auc':auc(y,[r['features'].get(f) for r in nz])}
rep={'version':'R4_HOLD_VS_COMPLETE_VALUE_V1','researchOnly':True,'strictPast':True,'winnerOnlyOfflineLabel':True,'formula':{'directionalAlphaValue':'absNet * (Predict fair probability of dominant side - dominant-side average acquisition cost)','riskPressure':'absNet * rv10_bps^2 * secondsLeft','riskSqrt':'absNet * rv10_bps * sqrt(secondsLeft)','decisionMeaning':'completion pressure should rise with inventory-volatility-horizon risk and fall with positive directional alpha / preserved upside'},'rows':rows,'summary':summary}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print('rows',len(rows))
for f in ['fairEdgePerShare','directionalAlphaValue','riskPressure','riskSqrt','alphaToRisk','upsideToRisk','rv10MeanBps','upside','floor']:
 print('\n'+f)
 for tag in ['A','B','C','ALL']:print(tag,summary[tag]['features'].get(f))
print('artifact',OUT)
