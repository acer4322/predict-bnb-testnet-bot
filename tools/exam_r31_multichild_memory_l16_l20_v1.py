from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
OUT=ROOT/'data/research/r3_v0/r31_multichild_memory_l16_l20_v1.json'

def reg(b,cid,role,side,req,created,price=.4):
 b.register_intent(client_order_id=cid,market_id=1,role=role,side=side,requested_shares=req,created_at_ms=created,requested_price=price,reason='TEST')

def ev(seq,cid,role,side,state,at,delta=0.,filled=0.,etype=None):
 return {'seq':seq,'source_id':'R3S_R31_TEST','client_order_id':cid,'source_market_id':1,'role':role,'side':side,'state':state,'occurred_at_ms':at,'delta_shares':delta,'filled_share_qty':filled,'event_type':etype or ('FILL_DELTA' if delta>0 else 'ORDER_'+state)}

def snap(b,now,up=0.,dn=0.):return b.snapshot(at_ms=now,actual_inventory={'UP':up,'DOWN':dn},pending_cancels=[],orphan_count=0,engine_state={},portfolio_context={})

def main():
 tests=[]
 # L16: middle order stalled, newest filled. Must still expose middle unresolved/stall.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1)
 reg(b,'m1','MAKER','UP',10,1000);reg(b,'m2','MAKER','DOWN',10,2000);reg(b,'m3','MAKER','UP',10,3000)
 b.observe_event(ev(1,'m3','MAKER','UP','FILLED',5000,10,10,'FILL_DELTA'));b.observe_event(ev(2,'m3','MAKER','UP','FILLED',5001,0,10,'ORDER_FILLED'))
 s=snap(b,18000,10,0);f=s['formationExecutionContext'];tests.append({'level':'L16_MIDDLE_STALLED_NEWEST_OK','pass':f['liveMakerUnresolvedQty']['DOWN']>=9.99 and f['recent15sStallCount']>=1 and len([c for c in s['children'] if not c.get('terminal')])==2})
 # L17: early reject plus newest resting. Reject must remain incident, newest must not hide it.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);reg(b,'a1','MAKER','UP',10,1000);reg(b,'a2','MAKER','DOWN',10,2000);reg(b,'a3','MAKER','UP',10,3000)
 b.observe_event(ev(1,'a2','MAKER','DOWN','REJECTED',3500,0,0,'ORDER_REJECTED'));s=snap(b,7000,0,0);inc=[x['incidentType'] for x in s['incidents']];tests.append({'level':'L17_EARLY_REJECT_NOT_HIDDEN_BY_NEWEST','pass':'SUBMIT_REJECT_CONFIRMED' in inc and any(c['clientOrderId']=='a3' and not c['terminal'] for c in s['children'])})
 # L18: unknown old child + healthy newest fill. Unknown must dominate situation.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);reg(b,'u1','MAKER','DOWN',10,1000);reg(b,'u2','MAKER','UP',10,3000)
 b.observe_event(ev(1,'u1','MAKER','DOWN','UNKNOWN_SUBMISSION',4000,0,0,'ORDER_UNKNOWN'));b.observe_event(ev(2,'u2','MAKER','UP','FILLED',5000,10,10,'FILL_DELTA'));b.observe_event(ev(3,'u2','MAKER','UP','FILLED',5001,0,10,'ORDER_FILLED'));s=snap(b,7000,10,0);tests.append({'level':'L18_UNKNOWN_OLD_CHILD_DOMINATES','pass':s['situationCode']=='UNKNOWN_QUARANTINE' and s['ownershipState']=='UNKNOWN_CHILD' and f'{s["formationExecutionContext"]["liveBySide"]["DOWN"]["maker"]["unknownCount"]}'=='1'})
 # L19: partial first child, second filled, third pending. All nonterminal obligations visible.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);reg(b,'p1','MAKER','DOWN',10,1000);reg(b,'p2','MAKER','UP',10,2000);reg(b,'p3','MAKER','DOWN',10,3000)
 b.observe_event(ev(1,'p1','MAKER','DOWN','PARTIAL_FILL',4000,4,4,'FILL_DELTA'));b.observe_event(ev(2,'p2','MAKER','UP','FILLED',4500,10,10,'FILL_DELTA'));b.observe_event(ev(3,'p2','MAKER','UP','FILLED',4501,0,10,'ORDER_FILLED'));s=snap(b,10000,10,4);fd=s['formationExecutionContext'];tests.append({'level':'L19_PARTIAL_AND_PENDING_BOTH_VISIBLE','pass':fd['liveBySide']['DOWN']['maker']['partialCount']>=1 and fd['liveMakerUnresolvedQty']['DOWN']>=15.99})
 # L20: cancel-pending old child receives fill while newest child is normal. Event must remain visible and child not released until terminal.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);reg(b,'c1','MAKER','DOWN',10,1000);reg(b,'c2','MAKER','UP',10,3000)
 b.pending=set() if False else None
 b.children['c1']['cancelPending']=True;b.children['c1']['state']='CANCEL_PENDING'
 b.observe_event(ev(1,'c1','MAKER','DOWN','PARTIAL_FILL',6000,3,3,'FILL_DELTA'));s=snap(b,7000,0,3);inc=[x['incidentType'] for x in s['incidents']];tests.append({'level':'L20_FILL_DURING_CANCEL_OLD_CHILD_PERSISTS','pass':'FILL_DURING_CANCEL' in inc and any(c['clientOrderId']=='c1' and not c['terminal'] for c in s['children']) and s['formationExecutionContext']['liveMakerUnresolvedQty']['DOWN']>=6.99})
 rep={'version':'R31_MULTICHILD_MEMORY_L16_L20_V1','tests':tests,'passed':sum(x['pass'] for x in tests),'total':len(tests),'allPass':all(x['pass'] for x in tests)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
