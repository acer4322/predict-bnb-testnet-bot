from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--target',default='data/target_wallet_official_v1.db');ap.add_argument('--public',default='data/public_source_snapshot_archive_v2.db');ap.add_argument('--maker',default='data/wallet_maker_book_inference.db');ap.add_argument('--scan',type=int,default=400);ap.add_argument('--take',type=int,default=120);ap.add_argument('--output',required=True);a=ap.parse_args();t=ro(a.target);p=ro(a.public);m=ro(a.maker)
 try:
  mids=[int(r[0]) for r in m.execute('select market_id from maker_book_inference_v21_market_meta order by market_id desc limit ?',(a.scan,))]
  rows=[]
  for mid in mids:
   te=int(t.execute("select count(*) from wallet_shadow_target_events where market_id=? and asset='BTC' and role in ('MAKER','TAKER') and side in ('UP','DOWN')",(mid,)).fetchone()[0])
   ps=int(p.execute('select count(*) from public_source_snapshots_v2 where market_id=?',(mid,)).fetchone()[0])
   pl=int(m.execute('select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=?',(mid,)).fetchone()[0])
   cc=int(m.execute('select count(*) from maker_book_inference_v21_cancel_candidates where market_id=?',(mid,)).fetchone()[0])
   pq=m.execute('select count(*),sum(case when placement_first_ms is not null and last_target_ms is not null then 1 else 0 end),sum(case when placement_coverage>=0.85 and fill_allocation_coverage>=0.70 then 1 else 0 end) from maker_book_inference_v21_parent_lifecycles where market_id=?',(mid,)).fetchone()
   rows.append({'market_id':mid,'target_events':te,'public_snapshots':ps,'parent_lifecycles':pl,'cancel_candidates':cc,'parent_timestamp_complete':int(pq[1] or 0),'parent_quality_ge_gate':int(pq[2] or 0),'eligible':bool(te>0 and ps>0 and pl>0 and cc>0)})
  eligible=[r for r in rows if r['eligible']][:a.take]
  out={'version':'TARGET_DIRECTION_CONFIDENCE_COMMON_COHORT_V1','researchOnly':True,'selection':'latest maker-inference market ids, then require same-market official BTC Maker/Taker fills + public_source_snapshots_v2 + v21 parent lifecycles + v21 cancel candidates; no outcome/winner/PnL filter','scan':a.scan,'requested':a.take,'eligibleFound':len(eligible),'marketIds':[r['market_id'] for r in eligible],'eligibleRows':eligible,'scanSummary':{'scanned':len(rows),'withTarget':sum(r['target_events']>0 for r in rows),'withPublic':sum(r['public_snapshots']>0 for r in rows),'withParents':sum(r['parent_lifecycles']>0 for r in rows),'withCancels':sum(r['cancel_candidates']>0 for r in rows),'allFour':sum(r['eligible'] for r in rows)}}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'eligibleFound':out['eligibleFound'],'scanSummary':out['scanSummary'],'first':out['marketIds'][:5],'last':out['marketIds'][-5:]},ensure_ascii=False,indent=2))
 finally:t.close();p.close();m.close()
if __name__=='__main__':main()
