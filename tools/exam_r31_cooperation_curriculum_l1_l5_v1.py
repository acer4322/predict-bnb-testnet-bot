from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
D=ROOT/'data/research/r3_v0'; OUT=D/'r31_cooperation_curriculum_l1_l5_v1.json'
def ev(seq,cid,typ,state,at,role='MAKER',side='UP',dq=0,fillq=0,req=10):
 return {'seq':seq,'source_id':'R3S_R31_TEST','client_order_id':cid,'event_type':typ,'occurred_at_ms':at,'created_at_ms':1000,'source_market_id':1,'role':role,'side':side,'state':state,'delta_shares':dq,'filled_share_qty':fillq,'requested_shares':req}
def snap(b,at,up=0,down=0,pc=None): return b.snapshot(at_ms=at,actual_inventory={'UP':up,'DOWN':down},pending_cancels=set(),orphan_count=0,engine_state={},portfolio_context=pc or {})
def main():
 tests=[]
 # L1: two live Maker children remain separately visible.
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);b.register_intent(client_order_id='u1',market_id=1,role='MAKER',side='UP',requested_shares=10,created_at_ms=1000,requested_price=.4);b.register_intent(client_order_id='d1',market_id=1,role='MAKER',side='DOWN',requested_shares=10,created_at_ms=1000,requested_price=.4);s=snap(b,7000)
 ok=s['formationExecutionContext']['liveBySide']['UP']['maker']['count']==1 and s['formationExecutionContext']['liveBySide']['DOWN']['maker']['count']==1
 tests.append({'level':'L1_MULTI_CHILD_VISIBILITY','pass':ok,'context':s['formationExecutionContext']})
 # L2: partial fill changes confirmed inventory context while unresolved remains visible.
 b.observe_event(ev(1,'u1','FILL_DELTA','PARTIAL_FILL',8000,side='UP',dq=4,fillq=4));s=snap(b,8000,up=4,down=0,pc={'combined_abs_net':4,'combined_paired_coverage':0})
 upm=s['formationExecutionContext']['liveBySide']['UP']['maker'];ok=abs(upm['confirmedFilledQty']-4)<1e-9 and abs(upm['unresolvedQty']-6)<1e-9 and s['formationExecutionContext']['actualAbsNet']==4
 tests.append({'level':'L2_PARTIAL_FILL_FORMATION','pass':ok,'context':s['formationExecutionContext']})
 # L3: unknown write is visible and never treated terminal.
 b.register_intent(client_order_id='u2',market_id=1,role='MAKER',side='UP',requested_shares=10,created_at_ms=9000,requested_price=.4);b.observe_event(ev(2,'u2','ORDER_STATE','UNKNOWN_SUBMISSION',9500,side='UP'));s=snap(b,9500,up=4,down=0)
 ok=s['formationExecutionContext']['liveBySide']['UP']['maker']['unknownCount']>=1 and s['terminalCertainty'] is False
 tests.append({'level':'L3_UNKNOWN_QUARANTINE','pass':ok,'ownership':s['ownershipState'],'situation':s['situationCode']})
 # L4: terminal partial remainder creates a residual obligation without releasing other child ownership.
 b.observe_event(ev(3,'u1','ORDER_CANCELED','CANCELED',12000,side='UP',fillq=4,req=10));s=snap(b,12000,up=4,down=0)
 ok=s['remainingObligation']['UP']>=5.99 and s['formationExecutionContext']['liveBySide']['UP']['maker']['count']>=1
 tests.append({'level':'L4_TERMINAL_REMAINDER','pass':ok,'remaining':s['remainingObligation'],'ownership':s['ownershipState']})
 # L5: Taker pending/partial is visible separately from Maker formation.
 b.register_intent(client_order_id='t1',market_id=1,role='TAKER',side='DOWN',requested_shares=20,created_at_ms=13000,requested_price=.55,reason='ADD_EFFECT');b.observe_event(ev(4,'t1','FILL_DELTA','PARTIAL_FILL',14000,role='TAKER',side='DOWN',dq=5,fillq=5,req=20));s=snap(b,14000,up=4,down=5)
 td=s['formationExecutionContext']['liveBySide']['DOWN']['taker'];ok=abs(td['confirmedFilledQty']-5)<1e-9 and abs(td['unresolvedQty']-15)<1e-9 and s['formationExecutionContext']['weakSide']=='UP'
 tests.append({'level':'L5_TAKER_UNRESOLVED_RECOVERY_CONTEXT','pass':ok,'context':s['formationExecutionContext']})
 rep={'version':'R31_COOPERATION_CURRICULUM_L1_L5_V1','researchOnly':True,'tests':tests,'passed':sum(x['pass'] for x in tests),'total':len(tests),'allPass':all(x['pass'] for x in tests)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
