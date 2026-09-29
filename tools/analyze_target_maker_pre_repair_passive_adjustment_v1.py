from __future__ import annotations
import argparse,bisect,json,math,sqlite3,statistics
from collections import defaultdict
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=ROOT/'data'/'research'/'target_maker_pre_repair_passive_adjustment_v1_report.json'
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'
LOOKBACKS=(5000,15000,30000)

def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=10); c.row_factory=sqlite3.Row; c.execute('PRAGMA query_only=ON'); return c

def pct(xs,p):
 if not xs:return None
 ys=sorted(xs); pos=(len(ys)-1)*p; lo=int(pos); hi=min(lo+1,len(ys)-1); w=pos-lo; return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(xs),'min':min(xs) if xs else None,'max':max(xs) if xs else None,'mean':statistics.mean(xs) if xs else None,'median':statistics.median(xs) if xs else None,'p25':pct(xs,.25),'p75':pct(xs,.75)}

def build_idx(rows,role=None):
 by=defaultdict(lambda:{'UP':[],'DOWN':[]})
 for r in rows:
  if role and str(r['role'])!=role: continue
  side=str(r['side'])
  if side not in ('UP','DOWN'):continue
  by[int(r['market_id'])][side].append((int(r['event_ms']),float(r['shares'])))
 out={}
 for m,ss in by.items():
  out[m]={}
  for side,rr in ss.items():
   rr.sort(); ts=[]; sums=[]; total=0.0
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

def effect_first_repairs(events,all_idx):
 out={}
 for r in events:
  if str(r['role'])!='TAKER':continue
  m=int(r['market_id']); t=int(r['event_ms']); side=str(r['side']); sh=float(r['shares'])
  before=inv(all_idx,m,t-1); after_net=before['net']+(sh if side=='UP' else -sh)
  if abs(after_net)<before['abs']-1e-9 and m not in out:
   out[m]={'event_ms':t,'side':side,'shares':sh,'beforeAbsAll':before['abs'],'afterAbsAll':abs(after_net)}
 return out

def relation(side,state):
 return 'DOMINANT' if state['dom']==side else 'MINORITY' if state['dom']!='FLAT' else 'FLAT'

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--book-db',type=Path,default=BOOK_DB); ap.add_argument('--target-db',type=Path,default=TARGET_DB); ap.add_argument('--report',type=Path,default=REPORT); a=ap.parse_args()
 b=ro(a.book_db); t=ro(a.target_db)
 try:
  latest=int(b.execute('SELECT COALESCE(MAX(source_timestamp_ms),0) FROM maker_book_inference_updates').fetchone()[0])
  markets={int(r['market_id']):int(r['window_end_ms']) for r in b.execute('SELECT market_id,window_end_ms FROM maker_book_inference_markets WHERE window_end_ms IS NOT NULL AND window_end_ms<=?',(latest-15000,))}
  marks=','.join('?' for _ in markets)
  events=[dict(r) for r in t.execute(f"SELECT market_id,event_ms,role,side,shares,quote_type FROM wallet_shadow_target_events WHERE lower(wallet)=lower(?) AND asset='BTC' AND market_id IN ({marks}) AND side IN ('UP','DOWN') ORDER BY market_id,event_ms",[WALLET,*markets.keys()])]
  all_idx=build_idx(events); maker_idx=build_idx(events,'MAKER'); repairs=effect_first_repairs(events,all_idx)
  parents=[dict(r) for r in b.execute(f"SELECT parent_id,market_id,target_side,placement_first_ms,last_target_ms,resting_ms FROM maker_book_inference_v21_parent_lifecycles WHERE market_id IN ({marks}) AND placement_first_ms IS NOT NULL AND placement_supports_18=1 AND placement_coverage>=.85 AND fill_allocation_coverage>=.70 AND confidence>=.75 ORDER BY market_id,placement_first_ms",list(markets.keys()))]
  byp=defaultdict(list)
  for p in parents:byp[int(p['market_id'])].append(p)
  maker_events=defaultdict(list)
  for e in events:
   if e['role']=='MAKER': maker_events[int(e['market_id'])].append(e)
  rows=[]
  for m,rep in repairs.items():
   rt=int(rep['event_ms']); pre=inv(maker_idx,m,rt-1)
   if pre['dom']=='FLAT': continue
   rr={'marketId':m,'repairEventMs':rt,'repairSide':rep['side'],'makerPreUp':pre['up'],'makerPreDown':pre['down'],'makerPreAbsNet':pre['abs'],'makerPreRatio':pre['ratio'],'makerDominant':pre['dom'],'repairBuysMakerMinority':rep['side']!=pre['dom']}
   ps=byp.get(m,[])
   mes=maker_events.get(m,[])
   priorp=[p for p in ps if int(p['placement_first_ms'])<rt]
   priorm=[e for e in mes if int(e['event_ms'])<rt]
   rr['lastAnchoredPlacementAgeMs']=rt-max((int(p['placement_first_ms']) for p in priorp),default=rt)
   rr['lastMakerFillAgeMs']=rt-max((int(e['event_ms']) for e in priorm),default=rt)
   for lb in LOOKBACKS:
    k=f'W{lb//1000}s'; start=rt-lb; s0=inv(maker_idx,m,start); s1=pre
    wp=[p for p in ps if start<=int(p['placement_first_ms'])<rt]
    d=[]; mi=[]
    for p in wp:
     st=inv(maker_idx,m,int(p['placement_first_ms'])-1); rel=relation(str(p['target_side']),st)
     (d if rel=='DOMINANT' else mi if rel=='MINORITY' else []).append(p)
    rr[k]={'deltaAbsNet':s1['abs']-s0['abs'],'startAbsNet':s0['abs'],'endAbsNet':s1['abs'],'dominantParents':len(d),'minorityParents':len(mi),'minorityParentShare':len(mi)/(len(d)+len(mi)) if d or mi else None,
           'lastDominantParentAgeMs':rt-max((int(p['placement_first_ms']) for p in d),default=rt),
           'lastMinorityParentAgeMs':rt-max((int(p['placement_first_ms']) for p in mi),default=rt),
           'dominantSilent':len(d)==0,'minorityActive':len(mi)>0,'passiveCorrectionPattern':len(d)==0 and len(mi)>0}
   rows.append(rr)
  summaries={}
  for lb in LOOKBACKS:
   k=f'W{lb//1000}s'; vals=[r[k] for r in rows]
   summaries[k]={'markets':len(vals),'deltaAbsNet':stats([v['deltaAbsNet'] for v in vals]),'worseningRate':sum(v['deltaAbsNet']>1e-9 for v in vals)/len(vals) if vals else None,'recoveringRate':sum(v['deltaAbsNet']<-1e-9 for v in vals)/len(vals) if vals else None,
                 'dominantParents':stats([v['dominantParents'] for v in vals]),'minorityParents':stats([v['minorityParents'] for v in vals]),'minorityParentShare':stats([v['minorityParentShare'] for v in vals if v['minorityParentShare'] is not None]),
                 'dominantSilentRate':sum(v['dominantSilent'] for v in vals)/len(vals) if vals else None,'minorityActiveRate':sum(v['minorityActive'] for v in vals)/len(vals) if vals else None,'passiveCorrectionPatternRate':sum(v['passiveCorrectionPattern'] for v in vals)/len(vals) if vals else None,
                 'lastDominantParentAgeMs':stats([v['lastDominantParentAgeMs'] for v in vals]),'lastMinorityParentAgeMs':stats([v['lastMinorityParentAgeMs'] for v in vals])}
  rep={'reportVersion':'TARGET_MAKER_PRE_REPAIR_PASSIVE_ADJUSTMENT_V1','researchOnly':True,'postHocEndpoint':'FIRST_REPAIR_EFFECT_PER_MARKET using cumulative observed Target all-role UP-DOWN shares; reducing |net| only','guards':['REPAIR_EFFECT is inventory-effect proxy, not semantic intent ground truth','No winner or market direction used','Anchored quote ownership/resting is INFERRED/probabilistic','Repair endpoint is used only post-hoc and never as strategy input'],
       'coverage':{'finalizedMarkets':len(markets),'firstRepairMarketsAll':len(repairs),'firstRepairMarketsWithNonflatMakerInventory':len(rows),'latestBookSourceMs':latest},
       'makerStateBeforeFirstRepair':{'absNetShares':stats([r['makerPreAbsNet'] for r in rows]),'imbalanceRatio':stats([r['makerPreRatio'] for r in rows]),'repairBuysMakerMinorityRate':sum(r['repairBuysMakerMinority'] for r in rows)/len(rows) if rows else None,'lastAnchoredPlacementAgeMs':stats([r['lastAnchoredPlacementAgeMs'] for r in rows]),'lastMakerFillAgeMs':stats([r['lastMakerFillAgeMs'] for r in rows])},'lookbacks':summaries,'rows':rows}
  a.report.parent.mkdir(parents=True,exist_ok=True); a.report.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:v for k,v in rep.items() if k!='rows'},ensure_ascii=False,indent=2))
 finally:b.close();t.close()
 return 0
if __name__=='__main__':raise SystemExit(main())
