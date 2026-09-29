from __future__ import annotations
import argparse,json
from pathlib import Path

GROUPS=[
 ('MARKET_STATE_DIRECTION',1),('OWN_EXPOSURE_FEEDBACK',2),('PHASE_DIRECTION_BIAS',4),
 ('GROSS_PHASE_SCALING',8),('DEFICIT_TICKET_FEEDBACK',16),('INVENTORY_QUOTE_FEEDBACK',32)]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('job_ids',nargs='+');ap.add_argument('--output',required=True);a=ap.parse_args()
 root=Path('data/research/lan_worker_returns');rows={};sources=[]
 for jid in a.job_ids:
  p=root/jid/'result.json';s=json.loads(p.read_text(encoding='utf-8'));sources.append(dict(job_id=jid,status=s.get('status'),market_id=s.get('market_id')))
  for r in s.get('rows',[]):
   if r.get('status')=='PASS':rows[int(r['mask'])]=r
 need={0,1,2,4,8,16,32,31,47,55,59,61,62,63}
 missing=sorted(need-set(rows))
 base=rows.get(0);full=rows.get(63)
 singles=[];los=[]
 if base:
  for n,b in GROUPS:
   r=rows.get(b)
   if r: singles.append(dict(name=n,mask=b,score=r['core_similarity'],score_lift_vs_null=r['core_similarity']-base['core_similarity'],
       path_mse=r['path_mse'],path_mse_improvement_vs_null=base['path_mse']-r['path_mse'],events=r['our_profile']['event_batches'],filled_orders=r['economic_filled_orders']))
 if full:
  for n,b in GROUPS:
   m=63-b;r=rows.get(m)
   if r: los.append(dict(name=n,without_mask=m,full_score=full['core_similarity'],without_score=r['core_similarity'],
       score_contribution_in_full_context=full['core_similarity']-r['core_similarity'],full_path_mse=full['path_mse'],without_path_mse=r['path_mse'],
       path_mse_contribution_in_full_context=r['path_mse']-full['path_mse'],full_events=full['our_profile']['event_batches'],without_events=r['our_profile']['event_batches'],
       event_change_when_removed=r['our_profile']['event_batches']-full['our_profile']['event_batches'],full_filled_orders=full['economic_filled_orders'],without_filled_orders=r['economic_filled_orders']))
 out=dict(version='TARGET_CORE_CYCLE_OPEN_FUNDING_V2_SCREEN',purpose='MECHANISM_DISCOVERY_NOT_PARAMETER_TUNING',sources=sources,
  complete=not missing,missing_masks=missing,null={k:base.get(k) for k in ('core_similarity','path_mse','our_profile','economic_filled_orders','final_cost')} if base else None,
  full={k:full.get(k) for k in ('core_similarity','path_mse','our_profile','economic_filled_orders','final_cost')} if full else None,
  single_mechanism_effects=sorted(singles,key=lambda x:x['score_lift_vs_null'],reverse=True),
  leave_one_out_effects=sorted(los,key=lambda x:x['score_contribution_in_full_context'],reverse=True),
  all_masks=[{k:r.get(k) for k in ('mask','enabled','core_similarity','path_mse','economic_filled_orders','receipt_conditioned_continuations','final_cost','terminal_worst')} for _,r in sorted(rows.items())],
  interpretation_boundary=['Consumed discovery market only; not promotion.','No coefficient/threshold/sizing tuning in this screen.','Target data is scoring-only and never enters runtime policy frames.','capital_cap is null; no live funding authority is implied.','Mechanisms require zero-retune cross-market replication before being called a core regularity.'])
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({'ok':not missing,'missing':missing,'best_single':out['single_mechanism_effects'][0] if singles else None,'top_leave_one_out':out['leave_one_out_effects'][0] if los else None},ensure_ascii=False))
 if missing:raise SystemExit(2)
if __name__=='__main__':main()
