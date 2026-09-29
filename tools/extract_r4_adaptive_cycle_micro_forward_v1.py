from pathlib import Path
import csv,json,sqlite3
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
prev=json.loads((P/'r4_target_episodic_fresh100_cohort_v2.json').read_text(encoding='utf-8'))
cutoff=max(int(x['windowEndMs']) for x in prev['markets'])
off=sqlite3.connect(f"file:{(ROOT/'data/target_wallet_official_v1.db').as_posix()}?mode=ro",uri=True);off.execute('pragma query_only=on')
life=sqlite3.connect(f"file:{(ROOT/'data/wallet_maker_book_inference.db').as_posix()}?mode=ro",uri=True);life.execute('pragma query_only=on')
rows=[]
for mid,endms in off.execute("select market_id,window_end_ms from target_markets where asset='BTC' and window_end_ms>? order by window_end_ms,market_id",(cutoff,)):
 n=life.execute("select count(*) from maker_book_inference_v21_parent_lifecycles indexed by idx_maker_book_v21_parent_market_time where market_id=? and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",(int(mid),)).fetchone()[0]
 if n>=20: rows.append({'marketId':int(mid),'windowEndMs':int(endms),'highConfidenceLifecycleRows':int(n)})
co={'version':'R4_ADAPTIVE_CYCLE_MICRO_FORWARD_V1','status':'FROZEN_MICRO_FORWARD_SCORE_ONLY','selection':{'after':cutoff,'asset':'BTC','lifecycleMin':20,'outcomeBlind':True},'markets':rows,'guards':['score only','no training or threshold changes','too small for promotion']};(P/'r4_adaptive_cycle_micro_forward_v1.json').write_text(json.dumps(co,indent=2),encoding='utf-8')
ids=[x['marketId'] for x in rows];out=P/'r4_adaptive_cycle_micro_forward_raw_v1';out.mkdir(parents=True,exist_ok=True)
def export(conn,sql,header,path):
 n=0
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(header)
  for mid in ids:
   for r in conn.execute(sql,(mid,)):w.writerow(r);n+=1
 return n
with (out/'markets.csv').open('w',newline='',encoding='utf-8') as f:
 w=csv.writer(f);w.writerow(['market_id','asset','window_end_ms']);
 for x in rows:w.writerow([x['marketId'],'BTC',x['windowEndMs']])
counts={}
counts['parents.csv']=export(off,"select market_id,parent_id,role,side,quote_type,lower(order_hash),first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders indexed by idx_target_parents_asset_market_time where asset='BTC' and market_id=? and quote_type='BID' order by first_event_ms,parent_id",['market_id','parent_id','role','side','quote_type','order_hash','first_event_ms','last_event_ms','average_price','shares','fill_legs'],out/'parents.csv')
counts['events.csv']=export(off,"select market_id,lower(order_hash),role,side,event_ms,price,shares from wallet_shadow_target_events indexed by idx_target_events_asset_market_time where asset='BTC' and market_id=? and quote_type='BID' order by event_ms,id",['market_id','order_hash','role','side','event_ms','price','shares'],out/'events.csv')
counts['lifecycles.csv']=export(life,"select market_id,lower(order_hash),target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles indexed by idx_maker_book_v21_parent_market_time where market_id=? and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by placement_first_ms",['market_id','order_hash','target_side','placement_first_ms','last_target_ms','placement_allocated_shares','expected_parent_shares','confidence','placement_coverage','fill_allocation_coverage'],out/'lifecycles.csv')
(out/'split.json').write_text(json.dumps({'adapt60':[],'guard20':[],'final20':ids},indent=2),encoding='utf-8');off.close();life.close();print(json.dumps({'markets':rows,'counts':counts,'out':str(out)},indent=2))
