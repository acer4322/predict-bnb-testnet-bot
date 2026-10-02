from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path: sys.path.insert(0,str(ROOT/'src'))
from predict_bot import unified_controller_paper_v2 as base
from predict_bot.r3s_active_stack_v110 import R3SActiveStackV110
MID=1698252
OUT=ROOT/'data/research/r3_v0/r3s_v110_echtgeld_1698252_counterfactual_replay_v1.json'

def ro(p):
 c=sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro",uri=True);c.row_factory=sqlite3.Row;return c

def main():
 ec=ro(ROOT/'data/echtgeld_engine_v1.db')
 dc=ro(ROOT/'data/strategy_r3s_r31_echtgeld_v1.db')
 fills=[dict(r) for r in ec.execute("select seq,occurred_at_ms,event_type,client_order_id,role,side,delta_shares,delta_usdt,fill_price,state from engine_cap100_events where source_market_id=? and event_type='FILL_DELTA' order by occurred_at_ms,seq",(MID,))]
 orders=[dict(r) for r in ec.execute("select client_order_id,role,side,requested_price,requested_shares,created_at_ms,state,error_kind from engine_cap100_orders where source_market_id=? and role='TAKER' order by created_at_ms",(MID,))]
 decisions=[dict(r) for r in dc.execute("select decision_ms,seconds_left,desired_portfolio_action,execution_choice,primary_reason,payload_json from our_decisions where market_id=? order by decision_ms",(MID,))]
 ec.close();dc.close()
 sec_by_t={int(r['decision_ms']):r.get('seconds_left') for r in decisions}
 def nearest_sec(t):
  if not decisions:return None
  r=min(decisions,key=lambda z:abs(int(z['decision_ms'])-int(t)))
  return r.get('seconds_left') if abs(int(r['decision_ms'])-int(t))<=1500 else None
 inv=base.Inventory(); stack=R3SActiveStackV110(ROOT/'data/research/r3_v0'); ep=None; meta={}; audit=[]; early_done=False
 timeline=[]
 for o in orders: timeline.append((int(o['created_at_ms']),'INTENT',o))
 for f in fills: timeline.append((int(f['occurred_at_ms']),'FILL',f))
 for d in decisions: timeline.append((int(d['decision_ms']),'CHECK',d))
 timeline.sort(key=lambda x:(x[0],{'FILL':0,'INTENT':1,'CHECK':2}[x[1]]))
 for t,kind,row in timeline:
  if kind=='INTENT':
   side=str(row['side']); port=inv.features(t); effect=stack.structural_effect(side,float(port.get('combined_net') or 0.0)); sec=nearest_sec(t); snap={'secondsLeft':sec}
   ev=None; allowed=True;reason=None;post=None
   if effect=='ADD_EFFECT':
    allowed,reason,post=stack.readd_gate(inv,ep,t,snap); ev=stack.pre_add_evidence(inv,t,snap)
   meta[str(row['client_order_id'])]={'effect':effect,'pre':dict(port),'evidence':ev,'snapshot':snap,'firstFill':False,'allowed':allowed,'gateReason':reason}
   audit.append({'atMs':t,'type':'TAKER_INTENT','clientOrderId':row['client_order_id'],'side':side,'requestedShares':row['requested_shares'],'effect':effect,'fullStackWouldAllow':allowed,'gateReason':reason,'stableEvidence':ev,'postAddGate':post})
  elif kind=='FILL':
   role=str(row['role']);side=str(row['side']);cid=str(row['client_order_id']);sh=float(row['delta_shares'] or 0);px=float(row['fill_price'] or 0);pre=inv.features(t)
   effect=None
   if role=='TAKER': effect=(meta.get(cid) or {}).get('effect') or stack.structural_effect(side,float(pre.get('combined_net') or 0.0))
   stack.observe_fill(ep,event_ms=t,role=role,structural_effect=effect)
   inv.apply({'event_ms':t,'role':role,'side':side,'price':px,'shares':sh});post=inv.features(t)
   if role=='TAKER' and effect=='ADD_EFFECT' and cid in meta and not meta[cid]['firstFill']:
    ep=stack.start_add_episode(fill_ms=t,pre_port=meta[cid]['pre'],post_port=post,evidence=meta[cid]['evidence'],snapshot=meta[cid]['snapshot'],intent_id=cid);meta[cid]['firstFill']=True;early_done=False
    audit.append({'atMs':t,'type':'POST_ADD_START','clientOrderId':cid,'episode':ep})
  else:
   if ep is not None and not early_done and t-int(ep['atMs'])>=15000:
    snap={'secondsLeft':row.get('seconds_left')};ok,detail=stack.triple_confirm(inv,ep,t,snap);early_done=True
    port=inv.features(t);net=float(port.get('combined_net') or 0.0);side='DOWN' if net>0 else 'UP' if net<0 else None
    audit.append({'atMs':t,'type':'EARLY_RECOVERY_15S','tripleConfirm':ok,'detail':detail,'currentAbsNet':port.get('combined_abs_net'),'containmentSide':side,'containmentQty75pct':abs(net)*0.75 if side else 0})
 would_veto=[x for x in audit if x['type']=='TAKER_INTENT' and x.get('effect')=='ADD_EFFECT' and not x.get('fullStackWouldAllow')]
 triples=[x for x in audit if x['type']=='EARLY_RECOVERY_15S' and x.get('tripleConfirm')]
 rep={'version':'R3S_V110_ECHTGELD_1698252_COUNTERFACTUAL_REPLAY_V1','marketId':MID,'strictPast':True,'note':'Counterfactual uses actual venue-confirmed event chronology only until each checkpoint; path is not re-simulated after a veto/containment.','summary':{'takerIntents':len(orders),'fills':len(fills),'wouldVetoReAdds':len(would_veto),'tripleConfirmContainments':len(triples),'firstWouldVeto':would_veto[0] if would_veto else None,'firstTripleConfirm':triples[0] if triples else None},'audit':audit}
 OUT.write_text(json.dumps(rep,indent=2,default=str),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'summary':rep['summary']},default=str))
if __name__=='__main__':main()
