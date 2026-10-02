from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
B=ROOT/'data/research/r4_v0/p0_provenance_v1'
PUB=ROOT/'data/public_source_snapshot_archive_v2.db'; MKR=ROOT/'data/wallet_maker_book_inference.db'; OFF=ROOT/'data/target_wallet_official_v1.db'
OUT=B/'TARGET_BTC_REPAIR_TAKER_DUAL_ROUTE_ACTIVE_READINESS_V32O_RESULT.json'

def f(v):
 try:
  x=float(v); return x if math.isfinite(x) else np.nan
 except Exception:return np.nan

def med(xs):
 xs=[float(x) for x in xs if x is not None and math.isfinite(float(x))]; return float(np.median(xs)) if xs else None

def main():
 cp=sqlite3.connect(f'file:{PUB.resolve().as_posix()}?mode=ro',uri=True); cp.row_factory=sqlite3.Row
 cm=sqlite3.connect(f'file:{MKR.resolve().as_posix()}?mode=ro',uri=True); cm.row_factory=sqlite3.Row
 co=sqlite3.connect(f'file:{OFF.resolve().as_posix()}?mode=ro',uri=True); co.row_factory=sqlite3.Row
 pm={int(x[0]) for x in cp.execute('select distinct market_id from public_source_snapshots_v2 where market_id>1399926')}
 vm={int(x[0]) for x in cm.execute('select distinct market_id from maker_book_inference_v21_market_meta where market_id>1399926')}
 om={int(x[0]) for x in co.execute("select distinct market_id from target_markets where asset='BTC' and market_id>1399926")}
 common=sorted(pm&vm&om); markets=common[180:240]
 rows=[]
 for mid in markets:
  snaps=[]
  for rr in cp.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
   t=int(rr['sampled_at_ms']); b=t//1000*1000; j=json.loads(rr['snapshot_json'])
   if snaps and snaps[-1][0]==b: snaps[-1]=(b,t,j)
   else: snaps.append((b,t,j))
  ev=[dict(r) for r in co.execute("select event_ms,role,side,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' order by event_ms,id",(mid,))]
  tak=defaultdict(list)
  for e in ev:
   if e['role']=='TAKER': tak[int(e['event_ms'])//1000*1000].append(e['side'])
  canc=[dict(r) for r in cm.execute('select target_side,placement_source_ms,cancel_source_ms,post_action from maker_book_inference_v21_cancel_candidates where market_id=? order by cancel_source_ms',(mid,))]
  i=0; up=dn=cost=0.0
  for b,t,j in snaps:
   sl=f(j.get('secondsLeft'))
   if not math.isfinite(sl) or sl>=60: continue
   while i<len(ev) and int(ev[i]['event_ms'])<b:
    e=ev[i]; q=float(e['shares']); p=float(e['price']); cost+=p*q
    if e['side']=='UP': up+=q
    elif e['side']=='DOWN': dn+=q
    i+=1
   if abs(up-dn)<=1e-9 or min(up,dn)-cost>=-1e-9: continue
   weak='UP' if up<dn else 'DOWN'; wm=f(j.get('predictUpMid' if weak=='UP' else 'predictDownMid'))
   wc=[x for x in canc if x['target_side']==weak]
   c3=[x for x in wc if t-3000<=int(x['cancel_source_ms'])<t]
   c10=[x for x in wc if t-10000<=int(x['cancel_source_ms'])<t]
   rep10=sum(str(x.get('post_action') or '').startswith('REPRICE') for x in c10)
   sb=f(j.get('spotMinusStrikeBps')); pressure=sb*(1 if weak=='UP' else -1) if math.isfinite(sb) else np.nan
   label=1 if weak in tak.get(b+1000,[]) else 0
   rows.append({'marketId':mid,'bucketMs':b,'secondsLeft':sl,'weakSide':weak,'weakMid':wm,'spotMinusStrikeBps':sb,'repairSideStrikePressureBps':pressure,'churn3':len(c3),'churn10':len(c10),'reprice10':rep10,'route':'CHURN3' if c3 else ('LAGGED_CHURN10_ONLY' if c10 else 'NO_CHURN10'),'label':label})
 cp.close(); cm.close(); co.close()
 pos=[r for r in rows if r['label']]; by={}
 for route in ['CHURN3','LAGGED_CHURN10_ONLY','NO_CHURN10']:
  z=[r for r in rows if r['route']==route]; zp=[r for r in z if r['label']]
  by[route]={'states':len(z),'positives':len(zp),'positiveRate':len(zp)/len(z) if z else None,'positiveCoverage':len(zp)/len(pos) if pos else None,'positiveWeakMidMedian':med([r['weakMid'] for r in zp]),'positivePressureMedian':med([r['repairSideStrikePressureBps'] for r in zp])}
 nc=[r for r in rows if r['route']=='NO_CHURN10' and math.isfinite(r['weakMid'])]
 y=[r['label'] for r in nc]; score=[1-r['weakMid'] for r in nc]
 auc=float(roc_auc_score(y,score)) if len(set(y))>1 else None
 pmid=med([r['weakMid'] for r in nc if r['label']]); nmid=med([r['weakMid'] for r in nc if not r['label']])
 npos=sum(y); keep=bool(npos>=8 and auc is not None and auc>=.70 and pmid is not None and nmid is not None and pmid<nmid)
 out={'version':'TARGET_BTC_REPAIR_TAKER_DUAL_ROUTE_ACTIVE_READINESS_V32O_RESULT','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'preregistered':'TARGET_BTC_REPAIR_TAKER_DUAL_ROUTE_ACTIVE_READINESS_V32O_PREREGISTERED.json','cohort':{'commonLaterMarkets':len(common),'marketIds':markets,'marketCount':len(markets),'lateEligibleStates':len(rows),'lateRepairTakerPositives':len(pos)},'routeCoverage':by,'cheapTerminalInsuranceTest':{'noChurn10States':len(nc),'positives':npos,'cheapnessAuc':auc,'positiveWeakMidMedian':pmid,'negativeWeakMidMedian':nmid,'keep':keep},'decision':'KEEP_CHEAP_TERMINAL_INSURANCE_SHADOW' if keep else 'REJECT_CHEAP_TERMINAL_INSURANCE_SHADOW','positiveExamplesNoChurn10':[r for r in nc if r['label']][:30],'boundary':['BTC architecture only; no threshold transfer to ETH','late<60s only','v21 cancel identity retrospective teacher-only','no winner/PnL','no threshold fitting','no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'cohort':out['cohort'],'routeCoverage':by,'cheapTerminalInsuranceTest':out['cheapTerminalInsuranceTest'],'decision':out['decision']},ensure_ascii=False))
if __name__=='__main__': main()
