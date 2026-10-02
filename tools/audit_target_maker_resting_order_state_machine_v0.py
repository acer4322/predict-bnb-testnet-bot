from __future__ import annotations

import bisect, json, math, sqlite3, statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
PUB_DB=ROOT/'data'/'strategy_target_compare_v1.db'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'/'target_maker_resting_order_state_machine_v0_report.json'
ROWS_OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'/'target_maker_resting_order_state_machine_v0_rows.csv'
GRID=.01
BASE='UNIFIED_CONTROLLER_PAPER_V1%'
ACTIONS=['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD','OPP_FIRST','END_NO_NEW']

def ro(p):
 c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=15); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def num(v):
 try:
  x=float(v); return x if math.isfinite(x) else None
 except: return None

def pv(s,a,b):
 x=num(s.get(a)); return x if x is not None else num(s.get(b))

def side_bid(s,side): return pv(s,'predictUpBid','predict_up_bid') if side=='UP' else pv(s,'predictDownBid','predict_down_bid')
def sec_left(s): return pv(s,'secondsLeft','seconds_left')
def q(xs,p):
 if not xs:return None
 y=sorted(xs); z=(len(y)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo; return y[lo]*(1-w)+y[hi]*w
def stats(xs):
 y=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(y),'mean':statistics.mean(y) if y else None,'median':statistics.median(y) if y else None,'p25':q(y,.25),'p75':q(y,.75),'p90':q(y,.9)}

def load_public(c):
 out=defaultdict(list)
 for r in c.execute('select market_id,coalesce(source_snapshot_ms,decision_ms) ms,public_state_json from our_decisions where strategy_version like ? and public_state_json is not null order by market_id,ms',(BASE,)):
  try:s=json.loads(r['public_state_json'])
  except:continue
  if isinstance(s,dict): out[int(r['market_id'])].append((int(r['ms']),s))
 return out

def load_parents(c,markets):
 out=defaultdict(list); ids=sorted(markets)
 for st in range(0,len(ids),300):
  b=ids[st:st+300]; qs=','.join('?'*len(b))
  sql=f'''select parent_id,market_id,target_side,target_price,placement_first_ms,placement_last_ms,last_target_ms,resting_ms,post_action
  from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null and last_target_ms is not null order by market_id,placement_first_ms,parent_id'''
  for r in c.execute(sql,b): out[int(r['market_id'])].append(dict(r))
 return out

def load_fills(c,markets):
 out=defaultdict(list); ids=sorted(markets)
 for st in range(0,len(ids),300):
  b=ids[st:st+300]; qs=','.join('?'*len(b))
  sql=f'''select market_id,event_ms,side,shares,price from wallet_shadow_target_events where market_id in ({qs}) and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') order by market_id,event_ms,id'''
  for r in c.execute(sql,b): out[int(r['market_id'])].append(dict(r))
 return out

def inv_before(fs,cp):
 up=down=0.;
 for f in fs:
  if int(f['event_ms'])>=cp: break
  if f['side']=='UP': up+=float(f['shares'])
  else: down+=float(f['shares'])
 gross=up+down; net=up-down; dom='UP' if net>1e-9 else 'DOWN' if net<-1e-9 else None
 return up,down,gross,net,dom,(2*min(up,down)/gross if gross>1e-9 else 1.)

def age_bin(ms):
 if ms<500:return 'A_LT0_5S'
 if ms<1500:return 'A_0_5_1_5S'
 if ms<3000:return 'A_1_5_3S'
 if ms<5000:return 'A_3_5S'
 return 'A_5S_PLUS'
def off_bin(x):
 if x is None:return 'Q_UNKNOWN'
 if x<=-1:return 'Q_AHEAD_INSIDE'
 if x<1:return 'Q_AT_BID'
 if x<2:return 'Q_1T_BEHIND'
 if x<4:return 'Q_2_3T_BEHIND'
 return 'Q_4T_PLUS_BEHIND'
def time_bin(s):
 if s is None:return 'T_UNKNOWN'
 if s>240:return 'T300_240'
 if s>180:return 'T240_180'
 if s>120:return 'T180_120'
 if s>60:return 'T120_60'
 if s>30:return 'T60_30'
 if s>15:return 'T30_15'
 return 'T15_0'
def absnet_bin(x):
 x=abs(x)
 if x<18:return 'N0_17'
 if x<54:return 'N18_53'
 if x<90:return 'N54_89'
 return 'N90_PLUS'
def action_summary(rows):
 n=len(rows); cnt=Counter(r['action'] for r in rows)
 return {'n':n,'markets':len({r['marketId'] for r in rows}),'rates':{a:(cnt[a]/n if n else None) for a in ACTIONS},
         'sameSideAnyRate':((cnt['SAME_PRICE_ADD']+cnt['SAME_NEAR_1_3T_ADD']+cnt['SAME_FAR_4T_PLUS_ADD'])/n if n else None),
         'keepRate':cnt['KEEP_PROXY']/n if n else None,'oppFirstRate':cnt['OPP_FIRST']/n if n else None,
         'multiActiveRate':sum(r['activeSameCount']>=2 for r in rows)/n if n else None,
         'currentAgeMs':stats([r['currentAgeMs'] for r in rows]),'quoteOffsetTicks':stats([r['quoteOffsetTicks'] for r in rows if r['quoteOffsetTicks'] is not None]),
         'absNet':stats([r['absNet'] for r in rows]),'pairedCoverage':stats([r['pairedCoverage'] for r in rows])}
def group(rows,key,min_n=30):
 d=defaultdict(list)
 for r in rows:d[str(r[key])].append(r)
 return {k:action_summary(v) for k,v in sorted(d.items()) if len(v)>=min_n}
def cross(rows,k1,k2,min_n=50):
 d=defaultdict(list)
 for r in rows:d[(str(r[k1]),str(r[k2]))].append(r)
 return {f'{a}|{b}':action_summary(v) for (a,b),v in sorted(d.items()) if len(v)>=min_n}
def rate_range(block,metric):
 vals=[]
 for k,v in block.items():
  x=v.get(metric)
  if x is not None: vals.append((k,float(x),int(v['n'])))
 if not vals:return None
 lo=min(vals,key=lambda z:z[1]); hi=max(vals,key=lambda z:z[1])
 return {'metric':metric,'min':{'bin':lo[0],'rate':lo[1],'n':lo[2]},'max':{'bin':hi[0],'rate':hi[1],'n':hi[2]},'range':hi[1]-lo[1]}

def main():
 pubc=ro(PUB_DB); bookc=ro(BOOK_DB); tc=ro(TARGET_DB)
 try:
  public=load_public(pubc); parents=load_parents(bookc,set(public)); fills=load_fills(tc,set(public)); rows=[]
  for mid,pubs in public.items():
   ps=parents.get(mid,[]); fs=fills.get(mid,[])
   if not ps: continue
   for cp,s in pubs:
    for side in ('UP','DOWN'):
     active=[p for p in ps if p['target_side']==side and int(p['placement_first_ms'])<=cp<int(p['last_target_ms'])]
     if not active: continue
     active.sort(key=lambda p:(int(p['placement_first_ms']),str(p['parent_id'])))
     cur=active[-1]; cur_px=float(cur['target_price']); age=cp-int(cur['placement_first_ms'])
     future=[p for p in ps if cp<int(p['placement_first_ms'])<=cp+1000]
     future.sort(key=lambda p:(int(p['placement_first_ms']),str(p['parent_id'])))
     nxt=future[0] if future else None
     if nxt is not None:
      if nxt['target_side']!=side: action='OPP_FIRST'; delta=None
      else:
       delta=abs(float(nxt['target_price'])-cur_px)/GRID
       if delta<.5: action='SAME_PRICE_ADD'
       elif delta<=3.5: action='SAME_NEAR_1_3T_ADD'
       else: action='SAME_FAR_4T_PLUS_ADD'
     else:
      delta=None; action='KEEP_PROXY' if int(cur['last_target_ms'])>cp+1000 else 'END_NO_NEW'
     up,down,gross,net,dom,pc=inv_before(fs,cp)
     role='FLAT' if dom is None else ('DOMINANT' if side==dom else 'MINORITY')
     bid=side_bid(s,side); off=((bid-cur_px)/GRID if bid is not None else None)
     bias=str(s.get('directionBias') or 'NEUTRAL'); align='NEUTRAL' if bias not in ('UP','DOWN') else ('TAILWIND' if bias==side else 'HEADWIND')
     vol=str(s.get('volatilityAlert') or 'UNKNOWN')
     sec=sec_left(s)
     rows.append({'marketId':mid,'checkpointMs':cp,'side':side,'action':action,'activeSameCount':len(active),'currentAgeMs':age,'ageBin':age_bin(age),'quoteOffsetTicks':off,'quoteOffsetBin':off_bin(off),'nextSamePriceDeltaTicks':delta,
                  'secondsLeft':sec,'timeBin':time_bin(sec),'upShares':up,'downShares':down,'absNet':abs(net),'absNetBin':absnet_bin(net),'pairedCoverage':pc,'inventoryRole':role,
                  'directionBias':bias,'directionAlignment':align,'volatilityAlert':vol,'directionScore':num(s.get('directionScore')),'spotReturn1sBps':num(s.get('spotReturn1sBps')),'futuresReturn1sBps':num(s.get('futuresReturn1sBps'))})
  blocks={
   'byAge':group(rows,'ageBin'),'byQuoteOffset':group(rows,'quoteOffsetBin'),'byInventoryRole':group(rows,'inventoryRole'),'byAbsNet':group(rows,'absNetBin'),'byTime':group(rows,'timeBin'),'byDirectionAlignment':group(rows,'directionAlignment'),'byVolatility':group(rows,'volatilityAlert'),
   'ageXQuoteOffset':cross(rows,'ageBin','quoteOffsetBin'),'inventoryRoleXQuoteOffset':cross(rows,'inventoryRole','quoteOffsetBin'),'timeXQuoteOffset':cross(rows,'timeBin','quoteOffsetBin'),'volatilityXQuoteOffset':cross(rows,'volatilityAlert','quoteOffsetBin'),'inventoryRoleXDirection':cross(rows,'inventoryRole','directionAlignment')}
  separation={name:{m:rate_range(bl,m) for m in ('sameSideAnyRate','keepRate','oppFirstRate','multiActiveRate')} for name,bl in blocks.items()}
  rep={'reportVersion':'TARGET_MAKER_RESTING_ORDER_STATE_MACHINE_V0','researchOnly':True,'liveTradingChanges':False,
       'question':'When a high-confidence Target same-side parent is already active, does the next 1s behavior look like keep, same-price add/refill/stack, near/far same-side reprice-or-stack, opposite-first, or lifecycle end; and do these actions vary by state/regime?',
       'method':{'checkpoint':'public decision snapshots from strategy_target_compare_v1; Target parent active proxy iff placement_first_ms <= checkpoint < last_target_ms','parentFilter':'placement_supports_18=1, placement_coverage>=.85, fill_allocation_coverage>=.70, confidence>=.75','strictPastState':'public snapshot and official Target Maker fills with event_ms < checkpoint','actionWindow':'first new high-confidence parent placement in (checkpoint, checkpoint+1s]','KEEP_PROXY':'no new parent within 1s AND current anchored parent has a later Target fill after checkpoint+1s','warning':'active-parent overlap and same-price/near-price events are inferred lifecycle proxies, not private cancel/replace/stack ground truth; SAME_* means new anchored parent while prior same-side parent still has future fill evidence.'},
       'coverage':{'publicMarkets':len(public),'parentMarkets':len(parents),'occupiedCheckpoints':len(rows),'markets':len({r['marketId'] for r in rows})},
       'overall':action_summary(rows),'blocks':blocks,'separation':separation,
       'interpretationGuard':['Do not reject a knob because its global rate is weak; inspect conditional blocks and interactions.','Do not call SAME_NEAR a proven cancel/replace: overlapping future-fill evidence also permits stacking/multi-level quoting.','No EBM/model training or threshold optimization in this V0.']}
  OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
  try:
   import pandas as pd; pd.DataFrame(rows).to_csv(ROWS_OUT,index=False)
  except Exception: pass
  print(json.dumps({'coverage':rep['coverage'],'overall':rep['overall'],'topSeparation':rep['separation']},ensure_ascii=False,indent=2))
 finally:
  pubc.close();bookc.close();tc.close()
if __name__=='__main__': main()
