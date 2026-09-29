from __future__ import annotations
import bisect, glob, json, math, sqlite3, statistics
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
DB=ROOT/'data/public_source_snapshot_archive_v2.db'
WINDB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_external_value_state_v1.json'

COHORTS={
 'A':{
  'h2':'r4-cont-h2s-chal24-*',
  'v17':'r4-cont-latecomp-v17-a-*',
  'trace':'r4-cont-latetrace-v18-a-*'},
 'B':{
  'h2':'r4-cont-h2s-chalb-*',
  'v17':'r4-cont-latecomp-v17-b-*',
  'trace':'r4-cont-latetrace-v18-b-*'},
 'C':{
  'h2':'r4-profit-v2c-pair-*',
  'v17':'r4-profit-c-late-v17-*',
  'trace':'r4-latetrace-v18-c-*'},
}

def load_rows(pattern:str)->list[dict[str,Any]]:
 out=[]
 for p in glob.glob(str(RET/pattern)):
  rp=Path(p)/'result.json'
  if not rp.exists(): continue
  try:d=json.loads(rp.read_text(encoding='utf-8'))
  except Exception: continue
  out.extend(d.get('rows') or [])
 return out

def by_cfg(rows,cfg):
 return {int(r['marketId']):r for r in rows if r.get('config')==cfg}

def pnl(r,w):
 f=r['final']; return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])

def sgn(side): return 1.0 if side=='UP' else -1.0

def num(x):
 try:
  v=float(x)
  return v if math.isfinite(v) else None
 except Exception:return None

def aligned(x,side):
 v=num(x); return None if v is None else sgn(side)*v

def micro_bps(micro,px,side):
 a=num(micro);b=num(px)
 if a is None or b is None or b==0:return None
 return sgn(side)*(a-b)/b*1e4

def strict_past_bundle(db,mid,t,lookback_ms=10000):
 rr=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? and sampled_at_ms>=? and sampled_at_ms<? order by sampled_at_ms',(int(mid),int(t-lookback_ms),int(t))).fetchall()
 out=[]
 for ts,raw in rr:
  try:out.append((int(ts),json.loads(raw)))
  except Exception:pass
 return out

def rv_bps(bundle,key):
 vals=[]
 for _,z in bundle:
  v=num(z.get(key))
  if v is not None and v>0: vals.append(v)
 if len(vals)<3:return None
 rets=[math.log(vals[i]/vals[i-1])*1e4 for i in range(1,len(vals)) if vals[i-1]>0 and vals[i]>0]
 return statistics.pstdev(rets) if len(rets)>=2 else None

def auc_abs(y,x):
 pairs=[(int(a),float(b)) for a,b in zip(y,x) if b is not None and math.isfinite(float(b))]
 if len(pairs)<4 or len({a for a,_ in pairs})<2:return None
 yy=[a for a,_ in pairs];xx=[b for _,b in pairs];a=roc_auc_score(yy,xx)
 return {'n':len(pairs),'auc':float(a),'aucAbs':float(max(a,1-a)),'benefitWhenHigher':bool(a>=.5)}

def med(vals):
 xs=[float(x) for x in vals if x is not None and math.isfinite(float(x))]
 return None if not xs else float(statistics.median(xs))

# winners only used for offline label of V17 terminal delta, never feature construction.
all_h2={}; all_v17={}; all_trace={}; cohort_of={}
for tag,spec in COHORTS.items():
 h=by_cfg(load_rows(spec['h2']),'CONT_STATE_H2_SUSPEND')
 v=by_cfg(load_rows(spec['v17']),'CONT_STATE_H2_SUSPEND_LATE_COMPLETE_TOUCH1')
 tr=by_cfg(load_rows(spec['trace']),'CONT_STATE_H2_SUSPEND_LATE_COMPLETE_TOUCH1')
 for m,r in h.items():all_h2[m]=r;cohort_of[m]=tag
 all_v17.update(v); all_trace.update(tr)
ids=sorted(set(all_h2)&set(all_v17)&set(all_trace))
q=','.join('?'*len(ids))
wins={}
with sqlite3.connect(WINDB) as db:
 for m,w in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids):
  if w in ('UP','DOWN'):wins[int(m)]=str(w)

rows=[]
with sqlite3.connect(DB) as db:
 for mid in ids:
  tr=all_trace[mid].get('lateActionTrace') or []
  if not tr or mid not in wins: continue
  e=tr[0]; t=int(e['t']); pre=e.get('pre') or {}
  up=num(pre.get('up')) or 0.; down=num(pre.get('down')) or 0.
  dom='UP' if up>down else 'DOWN' if down>up else None
  if dom is None: continue
  bun=strict_past_bundle(db,mid,t,10000)
  if not bun: continue
  ts,z=bun[-1]; age=t-ts
  if age>1000: continue
  hp=pnl(all_h2[mid],wins[mid]); vp=pnl(all_v17[mid],wins[mid]); delta=vp-hp
  # public-source features are all strict-past; values aligned to the current dominant inventory side.
  f={
   'directionAligned':aligned(z.get('directionScore'),dom),
   'predictAligned':sgn(dom)*((num(z.get('predictUpMid')) or .5)-.5)*2.0,
   'spotStrikeAlignedBps':aligned(z.get('spotMinusStrikeBps'),dom),
   'chainlinkStrikeAlignedBps':aligned(z.get('chainlinkMinusStrikeBps'),dom),
   'spotMicroAlignedBps':micro_bps(z.get('spotMicroprice'),z.get('spotPrice'),dom),
   'futuresMicroAlignedBps':micro_bps(z.get('futuresMicroprice'),z.get('futuresPrice'),dom),
   'spotQIAligned':aligned(z.get('spotQueueImbalance'),dom),
   'futuresQIAligned':aligned(z.get('futuresQueueImbalance'),dom),
   'spotTaker250Aligned':aligned(z.get('spotTakerImbalance250ms'),dom),
   'futuresTaker250Aligned':aligned(z.get('futuresTakerImbalance250ms'),dom),
   'spotTaker1sAligned':aligned(z.get('spotTakerImbalance1s'),dom),
   'futuresTaker1sAligned':aligned(z.get('futuresTakerImbalance1s'),dom),
   'spotRet1sAligned':aligned(z.get('spotReturn1sBps'),dom),
   'futuresRet1sAligned':aligned(z.get('futuresReturn1sBps'),dom),
   'spotRet5sAligned':aligned(z.get('spotReturn5sBps'),dom),
   'futuresRet5sAligned':aligned(z.get('futuresReturn5sBps'),dom),
   'spotRv10sBps':rv_bps(bun,'spotPrice'),
   'futuresRv10sBps':rv_bps(bun,'futuresPrice'),
   'basisAlignedBps':aligned(z.get('perpSpotBasisBps'),dom),
   'floor':num(pre.get('floor')),
   'upside':num(pre.get('upside')),
   'pairEdge':num(pre.get('edge')),
   'absNet':num(pre.get('absNet')),
   'coverage':num(pre.get('coverage')),
   'secondsPastNeed':num(e.get('secondsPastNeed')),
   'quoteOffsetTicks':num(e.get('quoteOffsetTicks')),
  }
  # A small literature-motivated descriptive state; no thresholds/action authority.
  aligned_terms=[f[k] for k in ['directionAligned','predictAligned','spotQIAligned','futuresQIAligned','spotTaker1sAligned','futuresTaker1sAligned'] if f[k] is not None]
  f['alphaSupportMean']=None if not aligned_terms else float(sum(aligned_terms)/len(aligned_terms))
  rv=[x for x in [f['spotRv10sBps'],f['futuresRv10sBps']] if x is not None]
  f['rv10sMeanBps']=None if not rv else float(sum(rv)/len(rv))
  rows.append({'marketId':mid,'cohort':cohort_of[mid],'t':t,'snapshotAgeMs':age,'dominantSide':dom,'h2Pnl':hp,'completionPnl':vp,'deltaPnl':delta,'label':'BENEFICIAL' if delta>1e-9 else 'HARMFUL' if delta<-1e-9 else 'NEUTRAL','features':f})

features=sorted({k for r in rows for k in r['features']})
summary={}
for tag in ['A','B','C','ALL']:
 rr=rows if tag=='ALL' else [r for r in rows if r['cohort']==tag]
 nz=[r for r in rr if r['label']!='NEUTRAL']; y=[1 if r['label']=='BENEFICIAL' else 0 for r in nz]
 summary[tag]={'n':len(rr),'beneficial':sum(r['label']=='BENEFICIAL' for r in rr),'harmful':sum(r['label']=='HARMFUL' for r in rr),'neutral':sum(r['label']=='NEUTRAL' for r in rr),'features':{}}
 for f in features:
  xb=[r['features'].get(f) for r in rr if r['label']=='BENEFICIAL']; xh=[r['features'].get(f) for r in rr if r['label']=='HARMFUL']
  summary[tag]['features'][f]={'beneficialMedian':med(xb),'harmfulMedian':med(xh),'auc':auc_abs(y,[r['features'].get(f) for r in nz])}

rep={'version':'R4_EXTERNAL_VALUE_STATE_V1','researchOnly':True,'strictPast':True,'externalAnchors':['microprice / signed-flow fair value','inventory-relative alpha','volatility-aware quoting','queue/latency-aware execution state'],'featureAuthority':'information-only; no trading action authority','rows':rows,'summary':summary}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print('rows',len(rows),'cohorts',{k:(v['n'],v['beneficial'],v['harmful'],v['neutral']) for k,v in summary.items()})
for f in ['alphaSupportMean','directionAligned','predictAligned','spotQIAligned','futuresQIAligned','spotTaker1sAligned','futuresTaker1sAligned','spotRet5sAligned','futuresRet5sAligned','rv10sMeanBps','pairEdge','floor','upside','absNet']:
 print('\n',f)
 for tag in ['A','B','C','ALL']:
  print(tag,summary[tag]['features'].get(f))
print('artifact',OUT)
