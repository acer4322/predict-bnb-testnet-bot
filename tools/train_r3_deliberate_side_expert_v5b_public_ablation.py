from pathlib import Path
p=Path('tools/train_r3_deliberate_side_expert_v5.py')
s=p.read_text(encoding='utf-8')
s=s.replace("STATE=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','signed_inventory','signed_payoff_gap','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','maker_events_15s','taker_events_15s','up_events_15s','down_events_15s','up_shares_15s','down_shares_15s','event_index_norm']","STATE=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','maker_events_15s','taker_events_15s','event_index_norm']")
s=s.replace("'version':'R3_DELIBERATE_SIDE_HGB_V5'","'version':'R3_DELIBERATE_SIDE_HGB_V5B_PUBLIC_ABLATION'")
s=s.replace("OUT/'r3_deliberate_side_hgb_v5.joblib'","OUT/'r3_deliberate_side_hgb_v5b_public_ablation.joblib'")
s=s.replace("OUT/'r3_deliberate_side_hgb_v5_report.json'","OUT/'r3_deliberate_side_hgb_v5b_public_ablation_report.json'")
exec(compile(s,'v5b','exec'))
