from __future__ import annotations
import argparse,bisect,json,math,sqlite3,statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=ROOT/'data'/'research'/'target_maker_inventory_recovery_context_v1_report.json'
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
TIME=[('T300_240',240,300.001),('T240_180',180,240),('T180_120',120,180),('T120_60',60,120),('T60_30',30,60),('T30_15',15,30),('T15_0',0,15)]

def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=10); c.row_factory=sqlite3.Row; c.execute('PRAGMA query_only=ON'); return c

def pct(xs,p):
 if not xs:return None
 ys=sorted(xs); pos=(len(ys)-1)*p; lo=int(pos); hi=min(lo+1,len(ys)-1); w=pos-lo; return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(xs),'mean':statistics.mean(xs) if xs else None,'median':statistics.median(xs) if xs else None,'p25':pct(xs,.25),'p75':pct(xs,.75)}

def tb(s):
 for n,lo,hi in TIME:
  if lo<=s<hi:return n
 return 'OUTSIDE'

def build_idx(rows):
 by=defaultdict(lambda:{'UP':[],'DOWN':[]})
 for r in rows: by[int(r['market_id'])][str(r['side'])].append((int(r['event_ms']),float(r['shares'])))
 out={}
 for m,ss in by.items():
  out[m]={}
  for side,rr in ss.items():
   rr.sort(); ts=[]; sums=[]; total=0
   for t,sh in rr: total+=sh; ts.append(t); sums.append(total)
   out[m][side]=(ts,sums)
 return out

def cum(idx,m,side,t):
 pair=idx.get(m,{}).get(side)
 if not pair:return 0.0
 ts,ss=pair; i=bisect.bisect_right(ts,t)-1; return ss[i] if i>=0 else 0.0

def inv(idx,m,t):
 u=cum(idx,m,'UP',t); d=cum(idx,m,'DOWN',t); net=u-d; gross=u+d
 return {'up':u,'down':d,'net':net,'abs':abs(net),'gross':gross,'ratio':abs(net)/gross if gross>1e-9 else 0.0,'dom':'UP' if net>1e-9 else 'DOWN' if net<-1e-9 else 'FLAT'}

def summarize(rr):
 if not rr:return {'n':0}
 elig=[r for r in rr if r['next5'] is not None]; cont=[r for r in elig if r['next5']]
 return {'n':len(rr),'markets':len({r['market'] for r in rr}),'continuationRate':len(cont)/len(elig) if elig else None,
         'pauseRate':1-len(cont)/len(elig) if elig else None,'priorDeltaAbs5s':stats([r['dAbs5'] for r in rr]),'priorDeltaAbs10s':stats([r['dAbs10'] for r in rr]),
         'postAbs':stats([r['postAbs'] for r in rr]),'postRatio':stats([r['postRatio'] for r in rr])}

def cls(v,eps=1.0):
 return 'WORSENING' if v>eps else 'RECOVERING' if v<-eps else 'STABLE'

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--book-db',type=Path,default=BOOK_DB); ap.add_argument('--target-db',type=Path,default=TARGET_DB); ap.add_argument('--report',type=Path,default=REPORT); a=ap.parse_args()
 b=ro(a.book_db); t=ro(a.target_db)
 try:
  latest=int(b.execute('SELECT COALESCE(MAX(source_timestamp_ms),0) FROM maker_book_inference_updates').fetchone()[0])
  markets={int(r['market_id']):int(r['window_end_ms']) for r in b.execute('SELECT market_id,window_end_ms FROM maker_book_inference_markets WHERE window_end_ms IS NOT NULL AND window_end_ms<=?',(latest-15000,))}
  marks=','.join('?' for _ in markets)
  ev=list(t.execute(f"SELECT market_id,event_ms,side,shares FROM wallet_shadow_target_events WHERE lower(wallet)=lower(?) AND asset='BTC' AND role='MAKER' AND quote_type='BID' AND market_id IN ({marks}) ORDER BY market_id,event_ms",[WALLET,*markets.keys()]))
  idx=build_idx(ev)
  ps=[dict(r) for r in b.execute(f"SELECT parent_id,market_id,target_side,native_price,last_target_ms,placement_first_ms FROM maker_book_inference_v21_parent_lifecycles WHERE market_id IN ({marks}) AND placement_first_ms IS NOT NULL AND placement_supports_18=1 AND placement_coverage>=.85 AND fill_allocation_coverage>=.70 AND confidence>=.75 ORDER BY market_id,first_target_ms",list(markets.keys()))]
  bm=defaultdict(list)
  for p in ps: bm[int(p['market_id'])].append(p)
  rows=[]
  for m,pp in bm.items():
   end=markets[m]
   for p in pp:
    ft=int(p['last_target_ms']); sec=(end-ft)/1000
    if not 0<=sec<=300.5: continue
    post=inv(idx,m,ft); side=str(p['target_side'])
    if post['dom']=='FLAT':continue
    rel='DOMINANT' if side==post['dom'] else 'MINORITY'
    pre5=inv(idx,m,ft-5000); pre10=inv(idx,m,ft-10000)
    d5=post['abs']-pre5['abs']; d10=post['abs']-pre10['abs']
    eligible=sec>=5
    nxt=any(str(q['target_side'])==side and q['parent_id']!=p['parent_id'] and ft<int(q['placement_first_ms'])<=ft+5000 for q in pp) if eligible else None
    rows.append({'market':m,'side':side,'rel':rel,'secondsLeft':sec,'time':tb(sec),'postAbs':post['abs'],'postRatio':post['ratio'],'dAbs5':d5,'dAbs10':d10,'trend5':cls(d5),'trend10':cls(d10),'next5':nxt})
  dom=[r for r in rows if r['rel']=='DOMINANT']; mino=[r for r in rows if r['rel']=='MINORITY']
  bytrend={}
  for h in ('trend5','trend10'):
   bytrend[h]={}
   for c in ('RECOVERING','STABLE','WORSENING'):
    bytrend[h][c]={'DOMINANT':summarize([r for r in dom if r[h]==c]),'MINORITY':summarize([r for r in mino if r[h]==c])}
  controlled=[]
  for tn,_,_ in TIME:
   for c in ('RECOVERING','STABLE','WORSENING'):
    d=[r for r in dom if r['time']==tn and r['trend10']==c and r['next5'] is not None]
    mi=[r for r in mino if r['time']==tn and r['trend10']==c and r['next5'] is not None]
    if len(d)>=30 and len(mi)>=30:
     dr=sum(r['next5'] for r in d)/len(d); mr=sum(r['next5'] for r in mi)/len(mi)
     controlled.append({'timeBucket':tn,'prior10sTrend':c,'dominantN':len(d),'minorityN':len(mi),'dominantContinuation':dr,'minorityContinuation':mr,'dominantMinusMinority':dr-mr})
  rep={'reportVersion':'TARGET_MAKER_INVENTORY_RECOVERY_CONTEXT_V1','researchOnly':True,'layer':'LAYER2_INVENTORY_ONLY_NO_DIRECTION','method':{'trend':'strict-past Maker-only abs-net change from t-5s/t-10s to current anchored fill; +/-1 share deadband','continuation':'same-side anchored parent placement within future 5s, post-hoc outcome only','directionUsed':False,'warning':'anchored placement ownership remains inferred/probabilistic; future continuation is outcome label, never strategy input'},'coverage':{'finalizedMarkets':len(markets),'anchoredNonFlatParents':len(rows),'latestBookSourceMs':latest},'overall':{'DOMINANT':summarize(dom),'MINORITY':summarize(mino)},'byPriorTrend':bytrend,'timeTrendControlledCells':controlled}
  a.report.parent.mkdir(parents=True,exist_ok=True); a.report.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
 finally:b.close();t.close()
 return 0
if __name__=='__main__': raise SystemExit(main())
