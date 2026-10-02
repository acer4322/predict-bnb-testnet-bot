"""Strict pre-first-DEEP-NEW parity and section4 mechanism gates; no economics."""
import hashlib,json
from analyze import read,differences,audit_path
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def parity(arm,base):
 a,b=read(base/'clock_trace.json.gz'),read(arm/'clock_trace.json.gz')
 ra,rb=read(base/'result.json'),read(arm/'result.json')
 ca,cb=read(base/'execution_clock.json'),read(arm/'execution_clock.json')
 first=next((p['t'] for p in b['plans'] if any(o.get('role')=='DEEP_LAYER' and o['kind']=='NEW' for o in p['operations'])),None)
 rows=[]
 def check(k,x,y):
  okay=digest(x)==digest(y);rows.append(dict(field=k,status='PASS' if okay else 'FAIL',base_count=len(x),deep_count=len(y),base_sha256=digest(x),deep_sha256=digest(y),differences=[] if okay else differences(x,y,limit=8)))
 def before(t):return first is None or t<first
 for k in ('plans','native_actions','states','observations','direction_rows'):
  check(k,[x for x in a[k] if before(x['t'])],[x for x in b[k] if before(x['t'])])
 check('v12g_events',[x for x in ra['v12g']['events'] if before(x['t'])],[x for x in rb['v12g']['events'] if before(x['t'])])
 check('receipts',[x for x in ca['receipts'] if before(x['receive_ts']/1000000)],[x for x in cb['receipts'] if before(x['receive_ts']/1000000)])
 check('native_clock_calls',[x for x in ca['calls'] if before(x['target_ns']/1000000)],[x for x in cb['calls'] if before(x['target_ns']/1000000)])
 return dict(status='PASS' if all(r['status']=='PASS' for r in rows) else 'INVALID_PARITY',market=rb['market_id'],first_deep_new_t=first,strict_before=True,no_replacement=True,rows=rows)

def audit(arm,base):
 r=read(arm/'result.json');deep=read(arm/'deep_layer_trace.json');tr=read(arm/'clock_trace.json.gz');clock=read(arm/'execution_clock.json')
 row=audit_path(arm,None);assert row['status']=='PASS'
 # audit_path's economic branch metrics are deliberately omitted from this stage1 report.
 row={k:v for k,v in row.items() if k not in ('UP','DOWN','cost','P','L','positive_exceeds_loss')}
 owners=clock['carriers']; plans=tr['plans']; orders=deep['orders']
 keys={o['key'] for p in plans for o in p['operations'] if o['kind']=='NEW' and o.get('role')=='DEEP_LAYER'}
 allnew=[(p,o) for p in plans for o in p['operations'] if o['kind']=='NEW' and o.get('role')=='DEEP_LAYER']
 source=read(arm/'risk_floor_trace.json.gz');assert source['mode']=='ON'
 floor_checks=[p for p in source['plans'] if p['enabled']]
 risk_ok=all(min(p['reserved_after'].values())>=p['floor']-1e-8 for p in floor_checks)
 maint=[(p['t'],o['key']) for p in plans for o in p['operations'] if o['kind']=='CANCEL' and o['key'] in keys and o.get('reason')=='V8_MAINTENANCE']
 window_start=clock['calls'][0]['target_ns']//1000000
 # Source market window, not first received update, anchors STOP290.
 inputs=__import__('pathlib').Path('C:/BTC5M-worker/.lan_worker_v1/staging/btc5m_cg1at_fresh100a_20260930/base/inputs')
 if not inputs.exists():inputs=__import__('pathlib').Path(__file__).resolve().parents[3]/'data/research/btc5m_cg1at_fresh100a_20260930/base/inputs'
 public=read(inputs/f"public_{r['market_id']}.json.gz")
 window_start=public['market']['window_start_ms']
 gate=dict(freeze_new_zero=not any(o['freeze_at_placement'] for o in orders),
  STOP290_new_zero=not any(p['t']>=window_start+290000 for p,o in allnew),
  risk_floor_violations_zero=risk_ok,
  all_owner_pending_terminal=r['unresolved_owners']==0 and all(c['state']=='TERMINAL' for c in owners.values()) and all(abs(v)<=1e-8 for v in r['clock_smoke']['final_pending_cash_direct'].values()),
  V8_wrong_cancel_zero=not maint,
  fixed_price_qty=all(abs(o['price']-(o['best_bid']-2*o['tick']))<1e-8 and o['qty']==15 for o in orders),
  trace_receipt_reconciles=deep['complete'] and len(keys)==len(orders) and all(abs(sum(f['qty'] for f in o['fills'])-o['filled_qty'])<1e-8 for o in orders),
  capital_cap_null=r['capital_cap'] is None and not r['cash_budget_enabled'],
  no_target_or_winner_runtime=not r['target_runtime_access'] and not r['target_direction_input'] and not r['oracle'])
 # Strict max-one-side and TTL evidence from canonical owners/receipts, not invented terminal timestamps.
 mapping={o['key']:o for p,o in allnew}; conflicts=[];ttlbad=[]
 for p in source['plans']:
  live={s:[o for o in p['state']['owners'] if o['key'] in keys and o['side']==s] for s in ('UP','DOWN')}
  for o in p['operations']:
   if o['kind']=='NEW' and o.get('role')=='DEEP_LAYER':live[o['side']].append(o)
  if any(len(v)>1 for v in live.values()):conflicts.append(p['t'])
 for o in orders:
  for c in o['cancels']:
   if c['reason']=='DEEP_TTL' and (c['t']<o['place_t']+2000 or any(f['receive_ms']<=c['t'] for f in o['fills'])):ttlbad.append(o['order_seq'])
 gate['one_nonterminal_per_side']=not conflicts;gate['TTL_partial_fill_semantics']=not ttlbad
 par=parity(arm,base)
 row.update(parity=par,mandatory=gate,deep_stats=deep['stats'],deep_orders=len(orders),filled_orders=sum(o['filled_qty']>0 for o in orders),filled_qty=sum(o['filled_qty'] for o in orders),legacy_active_matches_opportunity=r['safety_gate']['active_matches_opportunity'],legacy_safety_gate_pass=r['safety_gate']['pass'])
 row['path_valid']=par['status']=='PASS' and all(gate.values())
 row['status']='PASS' if row['path_valid'] else 'INVALID_MECHANISM'
 return row
