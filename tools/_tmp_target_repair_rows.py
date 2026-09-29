from __future__ import annotations
import argparse,bisect,json,math,sqlite3,statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=ROOT/'data'/'research'/'target_maker_repair_pair_recovery_context_v1_report.json'
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
  s=str(r['side'])
  if s not in ('UP','DOWN'):continue
  by[int(r['market_id'])][s].append((int(r['event_ms']),float(r['shares'])))
 out={}
 for m,ss in by.items():
  out[m]={}
  for s,rr in ss.items():
   rr.sort(); ts=[]; sums=[]; z=0.0
   for tm,sh in rr:z+=sh;ts.append(tm);sums.append(z)
   out[m][s]=(ts,sums)
 return out

def cum(idx,m,s,t):
 q=idx.get(m,{}).get(s)
 if not q:return 0.0
 ts,ss=q;i=bisect.bisect_right(ts,t)-1;return ss[i] if i>=0 else 0.0

def inv(idx,m,t):
 u=cum(idx,m,'UP',t);d=cum(idx,m,'DOWN',t);g=u+d;n=u-d;a=abs(n);paired=min(u,d)
 return {'up':u,'down':d,'gross':g,'net':n,'abs':a,'paired':paired,'pairCoverage':(2*paired/g if g>1e-9 else None),'dom':('UP' if n>1e-9 else 'DOWN' if n<-1e-9 else 'FLAT'),'minority':('DOWN' if n>1e-9 else 'UP' if n<-1e-9 else 'FLAT')}

def first_repairs(events,all_idx):
 out={}
 for e in events:
  if str(e['role'])!='TAKER':continue
  m=int(e['market_id']);tm=int(e['event_ms']);s=str(e['side']);sh=float(e['shares'])
  b=inv(all_idx,m,tm-1);after=b['net']+(sh if s=='UP' else -sh)
  if abs(after)<b['abs']-1e-9 and m not in out: out[m]={'eventMs':tm,'side':s,'shares':sh}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--book-db',type=Path,default=BOOK_DB);ap.add_argument('--target-db',type=Path,default=TARGET_DB);ap.add_argument('--report',type=Path,default=REPORT);a=ap.parse_args()
 b=ro(a.book_db);t=ro(a.target_db)
 try:
  latest=int(b.execute('select coalesce(max(source_timestamp_ms),0) from maker_book_inference_updates').fetchone()[0])
  markets={int(r['market_id']):int(r['window_end_ms']) for r in b.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null and window_end_ms<=?',(latest-15000,))}
  marks=','.join('?' for _ in markets)
  events=[dict(r) for r in t.execute(f"select market_id,event_ms,role,side,shares from wallet_shadow_target_events where lower(wallet)=lower(?) and asset='BTC' and market_id in ({marks}) and side in ('UP','DOWN') order by market_id,event_ms",[WALLET,*markets.keys()])]
  all_idx=build_idx(events);maker_idx=build_idx(events,'MAKER');repairs=first_repairs(events,all_idx)
  maker_by=defaultdict(list)
  for e in events:
   if e['role']=='MAKER':maker_by[int(e['market_id'])].append(e)
  rows=[]
  for m,r in repairs.items():
   tm=int(r['eventMs']);pre=inv(maker_idx,m,tm-1)
   if pre['dom']=='FLAT':continue
   row={'marketId':m,'repairEventMs':tm,'secondsLeft':(markets[m]-tm)/1000.0,'repairSide':r['side'],'makerDominant':pre['dom'],'makerMinority':pre['minority'],'repairBuysMakerMinority':r['side']==pre['minority'],'absNet':pre['abs'],'gross':pre['gross'],'pairCoverage':pre['pairCoverage'],'unpairedResidualShares':pre['abs']}
   mes=maker_by.get(m,[])
   for lb in LOOKBACKS:
    k=f'W{lb//1000}s';st=tm-lb;before=inv(maker_idx,m,st-1);window=[e for e in mes if st<=int(e['event_ms'])<tm]
    mi=[e for e in window if str(e['side'])==pre['minority']];dom=[e for e in window if str(e['side'])==pre['dom']]
    mi_sh=sum(float(e['shares']) for e in mi);dom_sh=sum(float(e['shares']) for e in dom)
    last_mi=max((int(e['event_ms']) for e in mi),default=None);last_dom=max((int(e['event_ms']) for e in dom),default=None)
    row[k]={'deltaAbsNet':pre['abs']-before['abs'],'pairCoverageStart':before['pairCoverage'],'pairCoverageEnd':pre['pairCoverage'],'deltaPairCoverage':(pre['pairCoverage']-before['pairCoverage'] if pre['pairCoverage'] is not None and before['pairCoverage'] is not None else None),'minorityFillCount':len(mi),'dominantFillCount':len(dom),'minorityFillShares':mi_sh,'dominantFillShares':dom_sh,'minorityMinusDominantFillShares':mi_sh-dom_sh,'minorityFillActive':bool(mi),'minorityFillAgeMs':(tm-last_mi if last_mi is not None else None),'dominantFillAgeMs':(tm-last_dom if last_dom is not None else None)}
   rows.append(row)
  look={}
  for lb in LOOKBACKS:
   k=f'W{lb//1000}s';v=[x[k] for x in rows]
   look[k]={'markets':len(v),'deltaAbsNet':stats([x['deltaAbsNet'] for x in v]),'deltaPairCoverage':stats([x['deltaPairCoverage'] for x in v]),'pairCoverageStart':stats([x['pairCoverageStart'] for x in v]),'pairCoverageEnd':stats([x['pairCoverageEnd'] for x in v]),'minorityFillActiveRate':sum(x['minorityFillActive'] for x in v)/len(v) if v else None,'minorityFillCount':stats([x['minorityFillCount'] for x in v]),'dominantFillCount':stats([x['dominantFillCount'] for x in v]),'minorityMinusDominantFillShares':stats([x['minorityMinusDominantFillShares'] for x in v]),'minorityFillAgeMs':stats([x['minorityFillAgeMs'] for x in v if x['minorityFillAgeMs'] is not None])}
  # descriptive split: was Maker-only inventory recovering vs worsening in prior 15s
  splits={}
  for name,pred in [('W15_RECOVERING',lambda x:x['W15s']['deltaAbsNet']< -1),('W15_WORSENING',lambda x:x['W15s']['deltaAbsNet']>1),('W15_STABLE',lambda x:abs(x['W15s']['deltaAbsNet'])<=1)]:
   rr=[x for x in rows if pred(x)]
   splits[name]={'markets':len(rr),'pairCoverageAtRepair':stats([x['pairCoverage'] for x in rr]),'absNetAtRepair':stats([x['absNet'] for x in rr]),'minorityFillActive5sRate':sum(x['W5s']['minorityFillActive'] for x in rr)/len(rr) if rr else None,'minorityFillActive15sRate':sum(x['W15s']['minorityFillActive'] for x in rr)/len(rr) if rr else None,'W15deltaPairCoverage':stats([x['W15s']['deltaPairCoverage'] for x in rr]),'secondsLeft':stats([x['secondsLeft'] for x in rr])}
  Path('data/research/execution_aware_fill_lifecycle_v0/target_repair_context_rows_tmp.json').write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
  rep={'reportVersion':'TARGET_MAKER_REPAIR_PAIR_RECOVERY_CONTEXT_V1','researchOnly':True,'layer':'LAYER2_POSTHOC_ESCALATION_CONTEXT_NO_DIRECTION','guards':['REPAIR_EFFECT is a post-hoc inventory-effect proxy, not semantic Target intent','No winner/SIMPLE3/spot/chainlink direction used','Pair coverage uses strict-past official Target MAKER fills only','All features are strict-past at the endpoint; the first-REPAIR endpoint itself is post-hoc and never a decision input'], 'coverage':{'finalizedMarkets':len(markets),'firstRepairMarketsAll':len(repairs),'firstRepairWithNonflatMakerInventory':len(rows),'latestBookSourceMs':latest},'atFirstRepair':{'absNetShares':stats([x['absNet'] for x in rows]),'pairCoverage':stats([x['pairCoverage'] for x in rows if x['pairCoverage'] is not None]),'repairBuysMakerMinorityRate':sum(x['repairBuysMakerMinority'] for x in rows)/len(rows) if rows else None,'secondsLeft':stats([x['secondsLeft'] for x in rows])},'lookbacks':look,'recoverySplit15s':splits,'interpretationGuard':'Descriptive lifecycle evidence only. Do not turn pairCoverage or fill-age medians into thresholds without matched non-repair controls / fresh falsifiable validation.'}
  a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
 finally:b.close();t.close()
if __name__=='__main__':main()
