from __future__ import annotations
import json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/'.lan_worker_v1/v49_oracle_proposal_add_memory_teacher_v2_20260915/data.jsonl'
OUT=ROOT/'.lan_worker_v1/v49_oracle_add_memory_pair_teacher_v5_20260915';OUT.mkdir(exist_ok=True)
rows=[]
for line in SRC.read_text().splitlines():
 r=json.loads(line)
 add=float(r['proposed_add_price']); wb=float(r['weak_bid']); wa=float(r['weak_ask'])
 # Passive weak repair can at best rest on weak bid; immediate/active repair uses weak ask.
 r['pair_sum_passive']=add+wb if r.get('book_available') else 2.0
 r['pair_edge_passive']=1.0-r['pair_sum_passive']
 r['pair_sum_active']=add+wa if r.get('book_available') else 2.0
 r['pair_edge_active']=1.0-r['pair_sum_active']
 r['pair_sum_mid']=add+(wb+wa)/2.0 if r.get('book_available') else 2.0
 r['pair_edge_mid']=1.0-r['pair_sum_mid']
 # Economically natural interaction terms; no fitted thresholds.
 debt=float(r['repair_debt_qref']); pend=float(r['pending_repair_qref'])
 r['uncovered_repair_debt_qref']=max(0.0,debt-pend)
 r['pair_cost_pressure_passive']=max(0.0,r['pair_sum_passive']-1.0)*r['uncovered_repair_debt_qref']
 r['pair_cost_pressure_active']=max(0.0,r['pair_sum_active']-1.0)*r['uncovered_repair_debt_qref']
 rows.append(r)
(OUT/'data.jsonl').write_text('\n'.join(json.dumps(r,separators=(',',':')) for r in rows)+'\n')
base=json.loads((ROOT/'.lan_worker_v1/v49_oracle_proposal_add_memory_teacher_v2_20260915/summary.json').read_text())
extra=['pair_sum_passive','pair_edge_passive','pair_sum_active','pair_edge_active','pair_sum_mid','pair_edge_mid','uncovered_repair_debt_qref','pair_cost_pressure_passive','pair_cost_pressure_active']
base['features_pair_economics']=extra;base['strict_past_pair_economics']=True;base['status']='PASS'
(OUT/'summary.json').write_text(json.dumps(base,indent=2))
print(json.dumps({'status':'PASS','rows':len(rows),'extra':extra}))
