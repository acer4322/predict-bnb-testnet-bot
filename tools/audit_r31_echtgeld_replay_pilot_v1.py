from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
D=ROOT/'data/research/r3_v0';OUT=D/'r31_echtgeld_replay_pilot_v1.json';DB=ROOT/'data/echtgeld_engine_v1.db'
def main():
 con=sqlite3.connect(DB);con.row_factory=sqlite3.Row
 orders=[dict(r) for r in con.execute("select * from engine_cap100_orders where source_id='R2_R21_8789' order by source_market_id,created_at_ms,client_order_id")]
 events=[dict(r) for r in con.execute("select e.* from engine_cap100_events e join engine_cap100_orders o on o.client_order_id=e.client_order_id where o.source_id='R2_R21_8789' order by e.source_market_id,e.occurred_at_ms,e.seq")];con.close()
 om={};
 for o in orders:om.setdefault(int(o['source_market_id']),[]).append(o)
 em={};
 for e in events:em.setdefault(int(e['source_market_id']),[]).append(e)
 rows=[];all_ok=True
 for mid in sorted(set(om)|set(em)):
  b=R31EchtgeldStateBridgeV1('R2_R21_8789');b.reset_market(mid);up=down=0.;registered=set();errs=[]
  os=om.get(mid,[]);es=em.get(mid,[]);oi=0
  timeline=sorted([(int(o.get('created_at_ms') or 0),'ORDER',o) for o in os]+[(int(e.get('occurred_at_ms') or 0),'EVENT',e) for e in es],key=lambda z:(z[0],0 if z[1]=='ORDER' else 1))
  for at,kind,obj in timeline:
   if kind=='ORDER':
    cid=str(obj['client_order_id']);registered.add(cid);b.register_intent(client_order_id=cid,market_id=mid,role=str(obj.get('role') or ''),side=str(obj.get('side') or ''),requested_shares=float(obj.get('requested_shares') or 0.),created_at_ms=int(obj.get('created_at_ms') or at),reason=str(obj.get('strategy') or ''),requested_price=float(obj.get('requested_price') or 0.))
   else:
    e=dict(obj);e['source_id']='R2_R21_8789';b.observe_event(e)
    if str(e.get('event_type') or '').upper()=='FILL_DELTA':
     sh=float(e.get('delta_shares') or 0.);side=str(e.get('side') or '').upper();
     if side=='UP':up+=sh
     elif side=='DOWN':down+=sh
   s=b.snapshot(at_ms=at,actual_inventory={'UP':up,'DOWN':down},pending_cancels=set(),orphan_count=0,engine_state={},portfolio_context={});f=s['formationExecutionContext']
   if abs(float(f['actualNet'])-(up-down))>1e-9:errs.append('ACTUAL_NET_MISMATCH')
   for side in ('UP','DOWN'):
    for role in ('maker','taker'):
     if float(f['liveBySide'][side][role]['unresolvedQty']) < -1e-9:errs.append('NEGATIVE_UNRESOLVED')
   if s.get('actionAuthority') or s.get('orderMutationAuthority') or s.get('executorCallbackAllowed'):errs.append('AUTHORITY_LEAK')
  final=b.snapshot(at_ms=(timeline[-1][0] if timeline else 0)+1,actual_inventory={'UP':up,'DOWN':down},pending_cancels=set(),orphan_count=0,engine_state={},portfolio_context={});ok=not errs;all_ok=all_ok and ok
  rows.append({'marketId':mid,'orders':len(os),'events':len(es),'confirmedUp':up,'confirmedDown':down,'finalOwnership':final.get('ownershipState'),'finalSituation':final.get('situationCode'),'remainingObligation':final.get('remainingObligation'),'errors':sorted(set(errs)),'pass':ok})
 rep={'version':'R31_ECHTGELD_REPLAY_PILOT_V1','researchOnly':True,'markets':len(rows),'rows':rows,'allPass':all_ok,'boundary':'Replay of actual 8781 R2_R21_8789 lifecycle history. Information-only audit; no writes to venue/controller.'};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'markets':len(rows),'allPass':all_ok,'failed':[r['marketId'] for r in rows if not r['pass']]}))
if __name__=='__main__':main()
