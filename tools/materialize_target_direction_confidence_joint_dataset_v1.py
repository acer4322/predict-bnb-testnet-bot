from __future__ import annotations
import argparse,bisect,csv,json,math,sqlite3
from collections import defaultdict,deque,Counter
from pathlib import Path
EPS=1e-9; SIDES=('UP','DOWN')
PUB_FEATURES=[
 ('seconds_left',('secondsLeft','seconds_left')),
 ('predict_up_bid',('predictUpBid','predict_up_bid')),('predict_up_ask',('predictUpAsk','predict_up_ask')),('predict_up_mid',('predictUpMid','predict_up_mid')),
 ('predict_down_bid',('predictDownBid','predict_down_bid')),('predict_down_ask',('predictDownAsk','predict_down_ask')),('predict_down_mid',('predictDownMid','predict_down_mid')),
 ('predict_up_spread',('predictUpSpread','predict_up_spread')),('predict_down_spread',('predictDownSpread','predict_down_spread')),
 ('spot_queue_imbalance',('spotQueueImbalance','spot_queue_imbalance')),('spot_taker_imbalance_250ms',('spotTakerImbalance250ms','spot_taker_imbalance_250ms')),('spot_taker_imbalance_1s',('spotTakerImbalance1s','spot_taker_imbalance_1s')),
 ('spot_return_250ms_bps',('spotReturn250msBps','spot_return_250ms_bps')),('spot_return_1s_bps',('spotReturn1sBps','spot_return_1s_bps')),('spot_return_3s_bps',('spotReturn3sBps','spot_return_3s_bps')),('spot_return_5s_bps',('spotReturn5sBps','spot_return_5s_bps')),
 ('futures_queue_imbalance',('futuresQueueImbalance','futures_queue_imbalance')),('futures_taker_imbalance_250ms',('futuresTakerImbalance250ms','futures_taker_imbalance_250ms')),('futures_taker_imbalance_1s',('futuresTakerImbalance1s','futures_taker_imbalance_1s')),
 ('futures_return_250ms_bps',('futuresReturn250msBps','futures_return_250ms_bps')),('futures_return_1s_bps',('futuresReturn1sBps','futures_return_1s_bps')),('futures_return_3s_bps',('futuresReturn3sBps','futures_return_3s_bps')),('futures_return_5s_bps',('futuresReturn5sBps','futures_return_5s_bps')),
 ('perp_spot_basis_bps',('perpSpotBasisBps','perp_spot_basis_bps')),('spot_minus_strike_bps',('spotMinusStrikeBps','spot_minus_strike_bps')),('chainlink_minus_strike_bps',('chainlinkMinusStrikeBps','chainlink_minus_strike_bps')),('spot_minus_chainlink_bps',('spotMinusChainlinkBps','spot_minus_chainlink_bps'))]
def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def getv(d,ks):
 for k in ks:
  if k in d and d[k] is not None:return d[k]
 return None
def finite(v):
 try:return math.isfinite(float(v))
 except:return False
def write_csv(path,rows):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
 if not rows: path.write_text('',encoding='utf-8'); return
 keys=[];seen=set()
 for r in rows:
  for k in r:
   if k not in seen:seen.add(k);keys.append(k)
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(rows)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cohort',required=True);ap.add_argument('--target',default='data/target_wallet_official_v1.db');ap.add_argument('--public',default='data/public_source_snapshot_archive_v2.db');ap.add_argument('--maker',default='data/wallet_maker_book_inference.db');ap.add_argument('--outdir',required=True);a=ap.parse_args()
 mids=[int(x) for x in json.loads(Path(a.cohort).read_text(encoding='utf-8'))['marketIds']]; outdir=Path(a.outdir);outdir.mkdir(parents=True,exist_ok=True)
 tc=ro(a.target);pc=ro(a.public);mc=ro(a.maker); action_all=[]; checkpoint_all=[]; market_summary=[]; excluded=[]
 try:
  for mid in mids:
   snaps=[]; st=[]
   for r in pc.execute('select id,sampled_at_ms,timestamp_ns,seconds_left,snapshot_json,archived_at_ms from public_source_snapshots_v2 where market_id=? order by sampled_at_ms,id',(mid,)):
    try:j=json.loads(str(r['snapshot_json']))
    except:j={}
    t=int(r['sampled_at_ms']);snaps.append({'id':int(r['id']),'t':t,'json':j if isinstance(j,dict) else {},'seconds_left_col':r['seconds_left']});st.append(t)
   ev=list(tc.execute("select id,leg_id,event_ms,observed_at_ms,role,side,quote_type,order_hash,transaction_hash,price,shares from wallet_shadow_target_events where market_id=? and asset='BTC' and role in ('MAKER','TAKER') and side in ('UP','DOWN') order by event_ms,id",(mid,)))
   by=defaultdict(list)
   for e in ev:by[int(e['event_ms'])].append(e)
   q={s:deque() for s in SIDES};outstanding={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES};cost=0.0
   last_clean_side=None;last_clean_ms=None;clean_run=0;recent_clean=deque(maxlen=5);last_role=None;last_action_ms=None
   m_actions=[]
   for t,legs in sorted(by.items()):
    pre_hold=dict(hold);pre_cost=cost;pre_out=dict(outstanding);pre_out_qty=pre_out['UP']+pre_out['DOWN'];pre_pu=pre_hold['UP']-pre_cost;pre_pd=pre_hold['DOWN']-pre_cost
    hist_side=last_clean_side;hist_ms=last_clean_ms;hist_run=clean_run;hist_recent=list(recent_clean);hist_last_role=last_role;hist_last_action=last_action_ms
    agg={s:{'q':0.0,'maker':0.0,'taker':0.0,'notional':0.0,'price_num':0.0} for s in SIDES}
    for e in legs:
     s=str(e['side']);qty=float(e['shares']);p=float(e['price']);rr=str(e['role']);agg[s]['q']+=qty;agg[s]['notional']+=qty*p;agg[s]['price_num']+=qty*p
     if rr=='MAKER':agg[s]['maker']+=qty
     else:agg[s]['taker']+=qty
    rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES}
    for pay in SIDES:
     need=rem[pay];dq=q[opp(pay)]
     while need>EPS and dq:
      lot=dq[0];take=min(need,lot['remaining']);lot['remaining']-=take;outstanding[opp(pay)]-=take;repair[pay]+=take;need-=take
      if lot['remaining']<=EPS:dq.popleft()
     rem[pay]=need
    direct_pair=min(rem['UP'],rem['DOWN']);rem['UP']-=direct_pair;rem['DOWN']-=direct_pair
    birth={s:0.0 for s in SIDES}
    for s in SIDES:
     if rem[s]>EPS:q[s].append({'remaining':rem[s]});outstanding[s]+=rem[s];birth[s]=rem[s]
    rq=sum(repair.values());bq=sum(birth.values());iq=agg['UP']['q']+agg['DOWN']['q']
    if rq>EPS and bq>EPS:role='COMPOSITE_CROSSING'
    elif rq>EPS:role='REPAIR_ONLY'
    elif bq>EPS:role='CLEAN_AGGREGATE_EXPAND'
    elif iq>EPS:role='PAIR_ONLY_OR_NET_NEUTRAL'
    else:role='EMPTY'
    birth_side='UP' if birth['UP']>EPS and birth['DOWN']<=EPS else ('DOWN' if birth['DOWN']>EPS and birth['UP']<=EPS else None)
    repair_side='UP' if repair['UP']>EPS and repair['DOWN']<=EPS else ('DOWN' if repair['DOWN']>EPS and repair['UP']<=EPS else None)
    idx=bisect.bisect_left(st,t)-1;snap=snaps[idx] if idx>=0 else None;age=t-snap['t'] if snap else None;strict=bool(snap and snap['t']<t);primary=bool(strict and age is not None and 0<=age<=2000)
    rec={'market_id':mid,'event_ms':t,'signal_ms':snap['t'] if snap else None,'signal_age_ms':age,'strict_past':int(strict),'primary_strict_2s':int(primary),'official_leg_count':len(legs),
      'pre_up_shares':pre_hold['UP'],'pre_down_shares':pre_hold['DOWN'],'pre_net_shares':pre_hold['UP']-pre_hold['DOWN'],'pre_gross_shares':pre_hold['UP']+pre_hold['DOWN'],'pre_cost':pre_cost,'pre_p_up':pre_pu,'pre_p_down':pre_pd,'pre_floor':min(pre_pu,pre_pd),'pre_best':max(pre_pu,pre_pd),
      'pre_outstanding_up':pre_out['UP'],'pre_outstanding_down':pre_out['DOWN'],'pre_outstanding_qty':pre_out_qty,'pre_outstanding_side':'UP' if pre_out['UP']>EPS and pre_out['DOWN']<=EPS else ('DOWN' if pre_out['DOWN']>EPS and pre_out['UP']<=EPS else ('BOTH' if pre_out['UP']>EPS and pre_out['DOWN']>EPS else None)),
      'prior_clean_expand_side':hist_side,'prior_clean_expand_age_ms':(t-hist_ms if hist_ms is not None else None),'prior_clean_expand_run_length':hist_run,'prior_clean_last5_up_fraction':(sum(1 for s in hist_recent if s=='UP')/len(hist_recent) if hist_recent else None),'prior_clean_count_last5':len(hist_recent),'last_economic_role':hist_last_role,'last_action_age_ms':(t-hist_last_action if hist_last_action is not None else None),
      'input_up_qty':agg['UP']['q'],'input_down_qty':agg['DOWN']['q'],'maker_up_qty':agg['UP']['maker'],'maker_down_qty':agg['DOWN']['maker'],'taker_up_qty':agg['UP']['taker'],'taker_down_qty':agg['DOWN']['taker'],'direct_pair_qty':direct_pair,
      'repair_up_qty':repair['UP'],'repair_down_qty':repair['DOWN'],'repair_qty':rq,'repair_acquisition_side':repair_side,'birth_up_qty':birth['UP'],'birth_down_qty':birth['DOWN'],'birth_qty':bq,'birth_side':birth_side,'economic_role':role,
      'route_mix':'MIXED' if (agg['UP']['maker']+agg['DOWN']['maker']>EPS and agg['UP']['taker']+agg['DOWN']['taker']>EPS) else ('MAKER_ONLY' if agg['UP']['maker']+agg['DOWN']['maker']>EPS else 'TAKER_ONLY')}
    if snap:
     for nm,ks in PUB_FEATURES:rec[nm]=getv(snap['json'],ks) if getv(snap['json'],ks) is not None else (snap['seconds_left_col'] if nm=='seconds_left' else None)
    for s in SIDES:
     hold[s]+=agg[s]['q'];cost+=agg[s]['notional']
    post_pu=hold['UP']-cost;post_pd=hold['DOWN']-cost;rec.update({'post_up_shares':hold['UP'],'post_down_shares':hold['DOWN'],'post_net_shares':hold['UP']-hold['DOWN'],'post_gross_shares':hold['UP']+hold['DOWN'],'post_cost':cost,'post_p_up':post_pu,'post_p_down':post_pd,'post_floor':min(post_pu,post_pd),'post_best':max(post_pu,post_pd),'delta_p_up':post_pu-pre_pu,'delta_p_down':post_pd-pre_pd,'delta_floor':min(post_pu,post_pd)-min(pre_pu,pre_pd),'delta_best':max(post_pu,post_pd)-max(pre_pu,pre_pd),'post_outstanding_up':outstanding['UP'],'post_outstanding_down':outstanding['DOWN'],'post_outstanding_qty':outstanding['UP']+outstanding['DOWN']})
    if role=='CLEAN_AGGREGATE_EXPAND' and birth_side:
     if last_clean_side==birth_side:clean_run+=1
     else:clean_run=1
     last_clean_side=birth_side;last_clean_ms=t;recent_clean.append(birth_side)
    rec['post_last_clean_expand_side']=last_clean_side;rec['post_last_clean_expand_run_length']=clean_run;rec['post_clean_last5_up_fraction']=(sum(1 for s in recent_clean if s=='UP')/len(recent_clean) if recent_clean else None);rec['post_clean_count_last5']=len(recent_clean)
    last_role=role;last_action_ms=t;action_all.append(rec);m_actions.append(rec)
    if not primary:excluded.append({'market_id':mid,'event_ms':t,'signal_ms':rec['signal_ms'],'signal_age_ms':age,'reason':'NO_STRICT_PUBLIC_SNAPSHOT' if not strict else 'PUBLIC_SNAPSHOT_OLDER_THAN_2S'})
   action_times=[r['event_ms'] for r in m_actions]
   for srow in snaps:
    t=srow['t'];pi=bisect.bisect_left(action_times,t)-1;ni=bisect.bisect_right(action_times,t);prev=m_actions[pi] if pi>=0 else None;nxt=m_actions[ni] if ni<len(m_actions) else None
    c={'market_id':mid,'checkpoint_ms':t,'source_snapshot_id':srow['id'],'seconds_left':getv(srow['json'],('secondsLeft','seconds_left')) if getv(srow['json'],('secondsLeft','seconds_left')) is not None else srow['seconds_left_col'],
      'pre_up_shares':prev['post_up_shares'] if prev else 0.0,'pre_down_shares':prev['post_down_shares'] if prev else 0.0,'pre_net_shares':prev['post_net_shares'] if prev else 0.0,'pre_gross_shares':prev['post_gross_shares'] if prev else 0.0,'pre_cost':prev['post_cost'] if prev else 0.0,'pre_p_up':prev['post_p_up'] if prev else 0.0,'pre_p_down':prev['post_p_down'] if prev else 0.0,'pre_floor':prev['post_floor'] if prev else 0.0,'pre_best':prev['post_best'] if prev else 0.0,
      'pre_outstanding_up':prev['post_outstanding_up'] if prev else 0.0,'pre_outstanding_down':prev['post_outstanding_down'] if prev else 0.0,'pre_outstanding_qty':prev['post_outstanding_qty'] if prev else 0.0,'prior_clean_expand_side':prev['post_last_clean_expand_side'] if prev else None,'prior_clean_expand_age_ms':(t-prev['event_ms'] if prev and prev['post_last_clean_expand_side'] else None),'prior_clean_expand_run_length':prev['post_last_clean_expand_run_length'] if prev else 0,'prior_clean_last5_up_fraction':prev['post_clean_last5_up_fraction'] if prev else None,'last_economic_role':prev['economic_role'] if prev else None,'last_action_age_ms':(t-prev['event_ms'] if prev else None),
      'next_action_ms':nxt['event_ms'] if nxt else None,'next_action_delay_ms':(nxt['event_ms']-t if nxt else None),'next_economic_role':nxt['economic_role'] if nxt else None,'next_birth_side':nxt['birth_side'] if nxt else None,'next_birth_qty':nxt['birth_qty'] if nxt else None,'next_repair_qty':nxt['repair_qty'] if nxt else None,'next_route_mix':nxt['route_mix'] if nxt else None}
    for h in (1000,2000,5000):c[f'confirmed_action_within_{h//1000}s']=int(nxt is not None and 0<nxt['event_ms']-t<=h);c[f'clean_expand_within_{h//1000}s']=int(nxt is not None and 0<nxt['event_ms']-t<=h and nxt['economic_role']=='CLEAN_AGGREGATE_EXPAND')
    for nm,ks in PUB_FEATURES:
     if nm=='seconds_left':continue
     c[nm]=getv(srow['json'],ks)
    checkpoint_all.append(c)
   market_summary.append({'market_id':mid,'official_legs':len(ev),'fill_clocks':len(m_actions),'primary_strict_2s':sum(r['primary_strict_2s'] for r in m_actions),'public_snapshots':len(snaps),'clean_expand':sum(r['economic_role']=='CLEAN_AGGREGATE_EXPAND' for r in m_actions),'repair_only':sum(r['economic_role']=='REPAIR_ONLY' for r in m_actions),'composite':sum(r['economic_role']=='COMPOSITE_CROSSING' for r in m_actions)})
  primary=[r for r in action_all if r['primary_strict_2s']]
  write_csv(outdir/'21_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_PRIMARY_V1.csv',primary);write_csv(outdir/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',action_all);write_csv(outdir/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',checkpoint_all);write_csv(outdir/'24_TARGET_BTC_DIRECTION_CONFIDENCE_MARKET_SUMMARY_V1.csv',market_summary)
  miss={}
  for nm,_ in PUB_FEATURES:
   vals=[r.get(nm) for r in primary];miss[nm]={'n':len(vals),'nonmissing':sum(finite(v) for v in vals),'rate':sum(finite(v) for v in vals)/len(vals) if vals else None}
  audit={'version':'TARGET_BTC_DIRECTION_CONFIDENCE_JOINT_DATASET_V1','researchOnly':True,'cohortMarkets':len(mids),'actionClocksAll':len(action_all),'primaryStrict2s':len(primary),'excludedFromPrimary':len(excluded),'excludedRows':excluded,'checkpointRows':len(checkpoint_all),'roleCountsPrimary':dict(Counter(r['economic_role'] for r in primary)),'routeCountsPrimary':dict(Counter(r['route_mix'] for r in primary)),'publicFeatureAvailabilityPrimary':miss,'mandatoryChecks':{'strictPastViolations':sum(not r['strict_past'] for r in primary),'market_id_complete':all(r['market_id'] is not None for r in primary),'event_ms_complete':all(r['event_ms'] is not None for r in primary),'signal_ms_complete':all(r['signal_ms'] is not None for r in primary),'economic_role_complete':all(bool(r['economic_role']) for r in primary),'repair_qty_complete':all(r['repair_qty'] is not None for r in primary),'birth_qty_complete':all(r['birth_qty'] is not None for r in primary),'portfolio_geometry_complete':all(all(r[k] is not None for k in ('pre_up_shares','pre_down_shares','pre_cost','pre_p_up','pre_p_down','pre_floor','pre_best')) for r in primary),'predict_mid_complete':all(finite(r.get('predict_up_mid')) and finite(r.get('predict_down_mid')) for r in primary),'seconds_left_complete':all(finite(r.get('seconds_left')) for r in primary)},'semantics':['Primary features are strict-past public snapshot and confirmed-fill-derived portfolio state.','Economic role is aggregate accounting reconstruction, not private Target intent.','Checkpoint future-action fields are labels only; never use them as features.','No-confirmed-action within horizon is not WAIT.','Winner/settlement/final inventory are not included.']}
  (outdir/'25_TARGET_BTC_DIRECTION_CONFIDENCE_DATA_AUDIT_V1.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'markets':len(mids),'allActions':len(action_all),'primary':len(primary),'excluded':len(excluded),'checkpoints':len(checkpoint_all),'roles':audit['roleCountsPrimary'],'mandatory':audit['mandatoryChecks']},ensure_ascii=False,indent=2))
 finally:tc.close();pc.close();mc.close()
if __name__=='__main__':main()
