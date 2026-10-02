from __future__ import annotations
import argparse,csv,json,sqlite3
from pathlib import Path

def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def dump(c,table,cols,mids,path):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 n=0
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
  for mid in mids:
   q=f"select {','.join(cols)} from {table} where market_id=? order by " + ('placement_first_ms,parent_id' if 'parent_lifecycles' in table else 'cancel_source_ms,candidate_id')
   for r in c.execute(q,(mid,)):
    w.writerow({k:r[k] for k in cols});n+=1
 return n

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cohort',required=True);ap.add_argument('--maker',default='data/wallet_maker_book_inference.db');ap.add_argument('--outdir',required=True);a=ap.parse_args();mids=[int(x) for x in json.loads(Path(a.cohort).read_text(encoding='utf-8'))['marketIds']];c=ro(a.maker);o=Path(a.outdir)
 try:
  pc=['parent_id','market_id','order_hash','target_side','native_book_side','target_price','native_price','first_target_ms','last_target_ms','target_fill_count','target_filled_shares','expected_parent_shares','allocated_fill_shares','fill_allocation_coverage','placement_allocated_shares','placement_coverage','placement_first_ms','placement_last_ms','resting_ms','post_action','post_action_delay_ms','post_action_native_price','multi_fill_parent','observed_filled_near_18','placement_supports_18','confidence','inferred_at_ms']
  cc=['candidate_id','market_id','target_side','native_book_side','target_price','native_price','placement_source_ms','cancel_source_ms','allocated_quantity','resting_ms','post_action','post_action_native_price','likely_reason','pressure_side','seconds_left','signal_age_ms','confidence','confidence_label','inferred_at_ms']
  np=dump(c,'maker_book_inference_v21_parent_lifecycles',pc,mids,o/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv');nc=dump(c,'maker_book_inference_v21_cancel_candidates',cc,mids,o/'27_TARGET_BTC_MAKER_CANCEL_DIAGNOSTICS_RETROSPECTIVE_V1.csv')
  q={'version':'TARGET_DIRECTION_CONFIDENCE_EXECUTION_DIAGNOSTIC_EXPORT_V1','markets':len(mids),'parentRows':np,'cancelRows':nc,'semantics':['RETROSPECTIVE_EXECUTION_DIAGNOSTIC only.','Do not use v21 inferred lifecycle/cancel identity as runtime-safe causal confidence source.','Use to test whether apparent directional persistence/escalation is explained by reprice/cancel/carrier mechanics.']};(o/'28_EXECUTION_DIAGNOSTIC_EXPORT_AUDIT_V1.json').write_text(json.dumps(q,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(q,ensure_ascii=False,indent=2))
 finally:c.close()
if __name__=='__main__':main()
