from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--maker',default='data/wallet_maker_book_inference.db');ap.add_argument('--markets',nargs='+',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();c=ro(a.maker)
 try:
  tabs={r[0] for r in c.execute("select name from sqlite_master where type='table'")}; rows=[]
  for mid in a.markets:
   rec={'market_id':mid}
   queries={
    'market_meta':("maker_book_inference_v21_market_meta","select count(*) from maker_book_inference_v21_market_meta where market_id=?"),
    'parent_lifecycles':("maker_book_inference_v21_parent_lifecycles","select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=?"),
    'cancel_candidates':("maker_book_inference_v21_cancel_candidates","select count(*) from maker_book_inference_v21_cancel_candidates where market_id=?"),
    'inferred_orders':("inferred_orders","select count(*) from inferred_orders where market_id=?")}
   for k,(t,q) in queries.items():rec[k]=int(c.execute(q,(mid,)).fetchone()[0]) if t in tabs else None
   if 'maker_book_inference_v21_parent_lifecycles' in tabs:
    x=c.execute("select count(*),sum(case when placement_first_ms is not null then 1 else 0 end),sum(case when last_target_ms is not null then 1 else 0 end),sum(case when placement_coverage>=0.85 then 1 else 0 end),sum(case when fill_allocation_coverage>=0.70 then 1 else 0 end) from maker_book_inference_v21_parent_lifecycles where market_id=?",(mid,)).fetchone();rec['parent_quality']={'total':x[0],'placement_timestamp':x[1] or 0,'last_target_timestamp':x[2] or 0,'placement_cov_ge_085':x[3] or 0,'fill_alloc_cov_ge_070':x[4] or 0}
   if 'maker_book_inference_v21_cancel_candidates' in tabs:
    x=c.execute("select count(*),sum(case when placement_source_ms is not null then 1 else 0 end),sum(case when cancel_source_ms is not null then 1 else 0 end),sum(case when post_action is not null then 1 else 0 end) from maker_book_inference_v21_cancel_candidates where market_id=?",(mid,)).fetchone();rec['cancel_quality']={'total':x[0],'placement_source_ms':x[1] or 0,'cancel_source_ms':x[2] or 0,'post_action':x[3] or 0}
   rows.append(rec)
  out={'version':'TARGET_DIRECTION_CONFIDENCE_EXECUTION_STATE_SMOKE_V1','researchOnly':True,'markets':rows,'summary':{'markets':len(rows),'with_market_meta':sum((r.get('market_meta') or 0)>0 for r in rows),'with_parent_lifecycles':sum((r.get('parent_lifecycles') or 0)>0 for r in rows),'with_cancel_candidates':sum((r.get('cancel_candidates') or 0)>0 for r in rows),'with_inferred_orders':sum((r.get('inferred_orders') or 0)>0 for r in rows)},'guard':'Execution-state inference is probabilistic/public reconstruction; use as confounder/diagnostic, not private Target intent ground truth.'}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
 finally:c.close()
if __name__=='__main__':main()
