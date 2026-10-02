from __future__ import annotations
import argparse,bisect,json,math,sqlite3
from collections import defaultdict,deque,Counter
from pathlib import Path
EPS=1e-9;SIDES=('UP','DOWN')
def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def getv(d,*ks):
 for k in ks:
  if k in d and d[k] is not None:return d[k]
 return None
def finite(v):
 try:return math.isfinite(float(v))
 except:return False
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target',default='data/target_wallet_official_v1.db');ap.add_argument('--public',default='data/public_source_snapshot_archive_v2.db');ap.add_argument('--markets',nargs='+',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tc=ro(a.target);pc=ro(a.public)
 feats=[('predict_up_mid',('predictUpMid','predict_up_mid')),('predict_down_mid',('predictDownMid','predict_down_mid')),('spot_minus_strike_bps',('spotMinusStrikeBps','spot_minus_strike_bps')),('chainlink_minus_strike_bps',('chainlinkMinusStrikeBps','chainlink_minus_strike_bps')),('spot_queue_imbalance',('spotQueueImbalance','spot_queue_imbalance')),('spot_taker_imbalance_1s',('spotTakerImbalance1s','spot_taker_imbalance_1s')),('spot_return_1s_bps',('spotReturn1sBps','spot_return_1s_bps')),('futures_queue_imbalance',('futuresQueueImbalance','futures_queue_imbalance')),('futures_taker_imbalance_1s',('futuresTakerImbalance1s','futures_taker_imbalance_1s')),('futures_return_1s_bps',('futuresReturn1sBps','futures_return_1s_bps')),('perp_spot_basis_bps',('perpSpotBasisBps','perp_spot_basis_bps')),('seconds_left',('secondsLeft','seconds_left'))]
 rows=[];ms=[]
 try:
  for mid in a.markets:
   snaps=[];st=[]
   for r in pc.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
    try:j=json.loads(str(r['snapshot_json']))
    except:continue
    if not isinstance(j,dict):continue
    t=int(r['sampled_at_ms']);snaps.append((t,j));st.append(t)
   ev=list(tc.execute("select id,event_ms,role,side,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' and role in ('MAKER','TAKER') and side in ('UP','DOWN') order by event_ms,id",(mid,)))
   by=defaultdict(list)
   for e in ev:by[int(e['event_ms'])].append(e)
   q={s:deque() for s in SIDES};outstanding={s:0.0 for s in SIDES};roles=Counter();ages=[];nonmiss=Counter();strictbad=0
   for t,legs in sorted(by.items()):
    pre=dict(outstanding);preq=pre['UP']+pre['DOWN'];rem={s:0.0 for s in SIDES};maker=taker=0.0
    for e in legs:
     s=str(e['side']);qty=float(e['shares']);rem[s]+=qty
     if str(e['role'])=='MAKER':maker+=qty
     elif str(e['role'])=='TAKER':taker+=qty
    repair={s:0.0 for s in SIDES}
    for pay in SIDES:
     need=rem[pay];dq=q[opp(pay)]
     while need>EPS and dq:
      lot=dq[0];take=min(need,lot['remaining']);lot['remaining']-=take;outstanding[opp(pay)]-=take;repair[pay]+=take;need-=take
      if lot['remaining']<=EPS:dq.popleft()
     rem[pay]=need
    pair=min(rem['UP'],rem['DOWN']);rem['UP']-=pair;rem['DOWN']-=pair;birth={s:0.0 for s in SIDES}
    for s in SIDES:
     if rem[s]>EPS:q[s].append({'remaining':rem[s]});outstanding[s]+=rem[s];birth[s]=rem[s]
    rq=sum(repair.values());bq=sum(birth.values());role='COMPOSITE_CROSSING' if rq>EPS and bq>EPS else 'REPAIR_ONLY' if rq>EPS else 'CLEAN_AGGREGATE_EXPAND' if bq>EPS else 'PAIR_ONLY_OR_NET_NEUTRAL';roles[role]+=1
    i=bisect.bisect_left(st,t)-1;snap=snaps[i] if i>=0 else None;age=t-snap[0] if snap else None
    if snap and not snap[0]<t:strictbad+=1
    if age is not None:ages.append(age)
    rec={'market_id':mid,'event_ms':t,'signal_ms':snap[0] if snap else None,'signal_age_ms':age,'strict_past':bool(snap and snap[0]<t),'pre_outstanding_qty':preq,'pre_outstanding_side':'UP' if pre['UP']>EPS else 'DOWN' if pre['DOWN']>EPS else None,'repair_qty':rq,'birth_qty':bq,'economic_role':role,'maker_qty':maker,'taker_qty':taker}
    if snap:
     for nm,ks in feats:
      v=getv(snap[1],*ks);rec[nm]=v
      if finite(v):nonmiss[nm]+=1
    rows.append(rec)
   n=len(by);a2=sum(1 for r in rows if r['market_id']==mid and r['signal_age_ms'] is not None and 0<=r['signal_age_ms']<=2000);a5=sum(1 for r in rows if r['market_id']==mid and r['signal_age_ms'] is not None and 0<=r['signal_age_ms']<=5000)
   ms.append({'market_id':mid,'fill_clocks':n,'snapshots':len(snaps),'joined_any':len(ages),'joined_2s':a2,'joined_5s':a5,'strict_past_violations':strictbad,'roles':dict(roles),'feature_nonmissing_over_joined':{k:(nonmiss[k]/len(ages) if ages else None) for k,_ in feats}})
  n=len(rows);joined=[r for r in rows if r['signal_ms'] is not None];j2=[r for r in joined if 0<=r['signal_age_ms']<=2000];j5=[r for r in joined if 0<=r['signal_age_ms']<=5000]
  out={'version':'TARGET_DIRECTION_CONFIDENCE_PUBLIC_JOIN_SMOKE_V1','researchOnly':True,'source':'public_source_snapshot_archive_v2.db + target_wallet_official_v1.db','markets':a.markets,'summary':{'fill_clocks':n,'joined_any':len(joined),'joined_any_rate':len(joined)/n if n else None,'joined_2s':len(j2),'joined_2s_rate':len(j2)/n if n else None,'joined_5s':len(j5),'joined_5s_rate':len(j5)/n if n else None,'strict_past_violations':sum(not r['strict_past'] for r in joined),'role_counts':dict(Counter(r['economic_role'] for r in rows)),'route_counts':dict(Counter('MIXED' if r['maker_qty']>EPS and r['taker_qty']>EPS else 'MAKER_ONLY' if r['maker_qty']>EPS else 'TAKER_ONLY' for r in rows)),'pre_outstanding_nonzero_rate':sum(r['pre_outstanding_qty']>EPS for r in rows)/n if n else None,'feature_nonmissing_rates_2s':{nm:(sum(finite(r.get(nm)) for r in j2)/len(j2) if j2 else None) for nm,_ in feats},'markets':ms},'guards':['Latest public snapshot is strictly earlier than confirmed fill clock.','Economic roles are aggregate confirmed-fill accounting, not private intent.','No winner/settlement/final inventory/future Target action used.','WAIT is not inferred.']}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out['summary'],ensure_ascii=False,indent=2))
 finally:tc.close();pc.close()
if __name__=='__main__':main()
