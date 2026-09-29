from __future__ import annotations
import json, math, sqlite3, statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'data/target_wallet_official_v1.db'
PUBLIC=ROOT/'data/public_source_snapshot_archive_v2.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/target_inventory_risk_fairvalue_v1.json'
MAX_SAMPLE=16000
MIN_GAP=18.0

def num(x):
 try:
  v=float(x);return v if math.isfinite(v) else None
 except Exception:return None

def rv_bps(rows,key):
 vals=[]
 for _,raw in rows:
  try:z=json.loads(raw);v=num(z.get(key))
  except Exception:continue
  if v is not None and v>0:vals.append(v)
 if len(vals)<3:return None
 rr=[math.log(vals[i]/vals[i-1])*1e4 for i in range(1,len(vals)) if vals[i-1]>0 and vals[i]>0]
 return statistics.pstdev(rr) if len(rr)>=2 else None

with sqlite3.connect(PUBLIC) as db:
 mn,mx=db.execute('select min(sampled_at_ms),max(sampled_at_ms) from public_source_snapshots_v2').fetchone()

# Reconstruct Target BID inventory chronologically. Parent average price and shares are treated as executed buy inventory.
candidates=[];state={}
with sqlite3.connect(TARGET) as db:
 cur=db.execute("select market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where quote_type='BID' and first_event_ms between ? and ? order by market_id,first_event_ms,parent_id",(mn,mx))
 last_mid=None
 for mid,role,side,t,px,qty,pid in cur:
  mid=int(mid);side=str(side);t=int(t);px=float(px);qty=float(qty)
  if mid!=last_mid:
   s={'UP':0.0,'DOWN':0.0,'CU':0.0,'CD':0.0};state[mid]=s;last_mid=mid
  s=state[mid];up=s['UP'];dn=s['DOWN'];gap=abs(up-dn)
  if gap>=MIN_GAP-1e-9 and up!=dn:
   dom='UP' if up>dn else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';purpose='WEAK_SIDE_BUILD' if side==weak else 'DOMINANT_ADD' if side==dom else 'OTHER'
   candidates.append({'marketId':mid,'t':t,'role':str(role),'actionSide':side,'purpose':purpose,'preUp':up,'preDown':dn,'preCU':s['CU'],'preCD':s['CD'],'absNet':gap,'dominantSide':dom,'weakSide':weak,'orderQty':qty,'orderPx':px})
  if side=='UP':s['UP']+=qty;s['CU']+=qty*px
  else:s['DOWN']+=qty;s['CD']+=qty*px

# Deterministic time-stratified subsample, no action/outcome selection.
if len(candidates)>MAX_SAMPLE:
 step=len(candidates)/MAX_SAMPLE
 sample=[candidates[min(len(candidates)-1,int(i*step))] for i in range(MAX_SAMPLE)]
else:sample=candidates

rows=[]
with sqlite3.connect(PUBLIC) as db:
 for i,c in enumerate(sample):
  mid=c['marketId'];t=c['t']
  snap=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? and sampled_at_ms<? order by sampled_at_ms desc limit 1',(mid,t)).fetchone()
  if not snap or t-int(snap[0])>1000:continue
  try:z=json.loads(snap[1])
  except Exception:continue
  hist=db.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? and sampled_at_ms>=? and sampled_at_ms<? order by sampled_at_ms',(mid,t-10000,t)).fetchall()
  rv=[x for x in (rv_bps(hist,'spotPrice'),rv_bps(hist,'futuresPrice')) if x is not None]
  if not rv:continue
  rv10=sum(rv)/len(rv);sec=num(z.get('secondsLeft'));fair_up=num(z.get('predictUpMid'))
  if sec is None or fair_up is None:continue
  dom=c['dominantSide'];fair=fair_up if dom=='UP' else 1-fair_up
  avg=(c['preCU']/c['preUp']) if dom=='UP' and c['preUp']>0 else (c['preCD']/c['preDown']) if dom=='DOWN' and c['preDown']>0 else None
  if avg is None:continue
  fair_edge=fair-avg;alpha_value=c['absNet']*fair_edge;risk=c['absNet']*(rv10**2)*max(sec,0.0);risk_sqrt=c['absNet']*rv10*math.sqrt(max(sec,0.0))
  dscore=num(z.get('directionScore'));align=(dscore if dom=='UP' else -dscore) if dscore is not None else None
  rows.append({**c,'snapshotAgeMs':t-int(snap[0]),'secondsLeft':sec,'rv10MeanBps':rv10,'fairDominantProb':fair,'avgDominantCost':avg,'fairEdgePerShare':fair_edge,'directionalAlphaValue':alpha_value,'riskPressure':risk,'riskSqrt':risk_sqrt,'directionAligned':align})

# Compare observable Target weak-side build vs dominant-side add, separately Maker/Taker and combined.
def med(v):
 x=[float(z) for z in v if z is not None and math.isfinite(float(z))];return None if not x else statistics.median(x)
def summarize(rr):
 out={'n':len(rr),'counts':{}}
 for p in ('WEAK_SIDE_BUILD','DOMINANT_ADD'):
  x=[r for r in rr if r['purpose']==p];out['counts'][p]=len(x);out[p]={}
  for f in ('absNet','rv10MeanBps','riskPressure','riskSqrt','fairEdgePerShare','directionalAlphaValue','directionAligned','secondsLeft'):
   out[p][f]=med([r.get(f) for r in x])
 return out
summary={'ALL':summarize(rows),'MAKER':summarize([r for r in rows if r['role']=='MAKER']),'TAKER':summarize([r for r in rows if r['role']=='TAKER'])}
# Chronological thirds for sign stability.
ts=sorted(r['t'] for r in rows)
if ts:
 c1=ts[len(ts)//3];c2=ts[(2*len(ts))//3]
 summary['EARLY']=summarize([r for r in rows if r['t']<=c1]);summary['MID']=summarize([r for r in rows if c1<r['t']<=c2]);summary['LATE']=summarize([r for r in rows if r['t']>c2])
rep={'version':'TARGET_INVENTORY_RISK_FAIRVALUE_V1','strictPast':True,'samplePolicy':{'candidateCount':len(candidates),'maxSample':MAX_SAMPLE,'method':'deterministic equal-index time/order stratification after absNet>=18 non-flat filter; no terminal outcome filtering'},'rows':rows,'summary':summary}
OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print('candidates',len(candidates),'sample',len(sample),'joined',len(rows))
for k,v in summary.items():
 print('\n',k,'n',v['n'],'counts',v['counts'])
 for p in ('WEAK_SIDE_BUILD','DOMINANT_ADD'):print(p,v[p])
print('artifact',OUT)
