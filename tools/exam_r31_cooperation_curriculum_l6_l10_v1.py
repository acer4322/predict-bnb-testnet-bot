from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
D=ROOT/'data/research/r3_v0';OUT=D/'r31_cooperation_curriculum_l6_l10_v1.json'
def E(seq,cid,typ,state,at,role='MAKER',side='UP',dq=0,fillq=0,req=10):return {'seq':seq,'source_id':'R3S_R31_TEST','client_order_id':cid,'event_type':typ,'occurred_at_ms':at,'created_at_ms':1000,'source_market_id':1,'role':role,'side':side,'state':state,'delta_shares':dq,'filled_share_qty':fillq,'requested_shares':req}
def S(b,at,up=0,down=0,pending=()):return b.snapshot(at_ms=at,actual_inventory={'UP':up,'DOWN':down},pending_cancels=set(pending),orphan_count=0,engine_state={},portfolio_context={})
def main():
 t=[]
 # L6 no-fill stall is visible as recent execution incident and unresolved maker.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);b.register_intent(client_order_id='m1',market_id=1,role='MAKER',side='UP',requested_shares=10,created_at_ms=1000,requested_price=.4);s=S(b,17000);f=s['formationExecutionContext'];t.append({'level':'L6_NO_FILL_STALL','pass':f['recent15sStallCount']>=1 and f['liveMakerUnresolvedQty']['UP']>=9.99})
 # L7 cancel-pending child that fills during cancel remains visible via incident and unresolved remainder.
 b.observe_event(E(1,'m1','ORDER_STATE','CANCEL_PENDING',18000,side='UP'));b.observe_event(E(2,'m1','FILL_DELTA','PARTIAL_FILL',18500,side='UP',dq=3,fillq=3));s=S(b,18500,up=3,pending=['m1']);f=s['formationExecutionContext'];inc=[x['incidentType'] for x in s['incidents']];t.append({'level':'L7_FILL_DURING_CANCEL','pass':'FILL_DURING_CANCEL' in inc and f['liveMakerUnresolvedQty']['UP']>=6.99})
 # L8 maker and taker can both remain unresolved without one hiding the other.
 b.register_intent(client_order_id='t1',market_id=1,role='TAKER',side='DOWN',requested_shares=20,created_at_ms=19000,requested_price=.55);b.observe_event(E(3,'t1','FILL_DELTA','PARTIAL_FILL',19500,role='TAKER',side='DOWN',dq=4,fillq=4,req=20));s=S(b,19500,up=3,down=4,pending=['m1']);f=s['formationExecutionContext'];t.append({'level':'L8_SIMULTANEOUS_UNRESOLVED','pass':f['liveMakerUnresolvedQty']['UP']>=6.99 and f['liveTakerUnresolvedQty']['DOWN']>=15.99})
 # L9 unknown child + existing partial child preserves UNKNOWN ownership quarantine.
 b.register_intent(client_order_id='m2',market_id=1,role='MAKER',side='DOWN',requested_shares=10,created_at_ms=20000,requested_price=.4);b.observe_event(E(4,'m2','ORDER_STATE','UNKNOWN_SUBMISSION',20500,side='DOWN'));s=S(b,20500,up=3,down=4,pending=['m1']);f=s['formationExecutionContext'];t.append({'level':'L9_MULTI_CHILD_UNKNOWN_QUARANTINE','pass':s['ownershipState']=='UNKNOWN_CHILD' and f['liveBySide']['DOWN']['maker']['unknownCount']>=1})
 # L10 terminal remainder is reduced by later same-side Maker fill from another child.
 b.observe_event(E(5,'m1','ORDER_CANCELED','CANCELED',22000,side='UP',fillq=3,req=10));before=S(b,22000,up=3,down=4)['remainingObligation']['UP'];b.register_intent(client_order_id='m3',market_id=1,role='MAKER',side='UP',requested_shares=10,created_at_ms=22500,requested_price=.4);b.observe_event(E(6,'m3','FILL_DELTA','PARTIAL_FILL',23000,side='UP',dq=5,fillq=5,req=10));after=S(b,23000,up=8,down=4)['remainingObligation']['UP'];t.append({'level':'L10_OBLIGATION_RECOVERY_PROGRESS','pass':before>=6.99 and after<=before-4.99,'before':before,'after':after})
 rep={'version':'R31_COOPERATION_CURRICULUM_L6_L10_V1','researchOnly':True,'tests':t,'passed':sum(x['pass'] for x in t),'total':len(t),'allPass':all(x['pass'] for x in t)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
