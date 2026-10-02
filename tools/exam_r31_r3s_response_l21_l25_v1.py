from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
OUT=ROOT/'data/research/r3_v0/r31_r3s_response_l21_l25_v1.json'

def mk(intent,role,side,req,fill,state,created,terminal=False,reason=''):
 return {'clientOrderId':intent,'marketId':1,'role':role,'side':side,'requestedShares':req,'confirmedFilledShares':fill,'state':state,'createdAtMs':created,'lastEventMs':created,'terminal':terminal,'cancelPending':state in {'CANCEL_PENDING','CANCEL_UNKNOWN'},'reason':reason,'requestedPrice':0.4}

def classify_r3s(s):
 f=s['formationExecutionContext'];live=f['liveBySide']; weak=f['weakSide']; unresolved=sum(live[x][r]['unresolvedQty'] for x in ('UP','DOWN') for r in ('maker','taker')); unknown=sum(live[x][r]['unknownCount'] for x in ('UP','DOWN') for r in ('maker','taker')); maxage=max(live[x][r]['maxAgeMs'] for x in ('UP','DOWN') for r in ('maker','taker')); stalls=f['recent15sStallCount']; fills=f['recent15sFillDeltaCount'];
 # research-only expected R3-S response policy for cooperation testing
 if unknown:
  return {'primary':'WAIT_EXECUTION_CERTAINTY','secondary':['BLOCK_NEW_ADD','NO_CONTAINMENT_ON_UNKNOWN'],'reason':'UNKNOWN_CHILD_PRESENT'}
 if s.get('situationCode')=='CANCEL_PENDING':
  return {'primary':'WAIT_CANCEL_TERMINAL_ACK','secondary':['BLOCK_NEW_ADD','BLOCK_CONTAINMENT'],'reason':'CANCEL_OWNERSHIP_NOT_RELEASED'}
 if unresolved<=1e-9:
  return {'primary':'NORMAL_R3S','secondary':[],'reason':'NO_UNRESOLVED_CHILD'}
 if fills>0 and stalls==0:
  return {'primary':'KEEP_AND_OBSERVE_RECOVERY','secondary':['ALLOW_MAKER_RECOVERY','HOLD_READD_UNTIL_RECHECK'],'reason':'RECENT_CONFIRMED_PROGRESS'}
 if maxage>=15000 and stalls>0:
  return {'primary':'REASSESS_OLDEST_BLOCKER','secondary':['PAUSE_READD','RAISE_REPAIR_PRIORITY'],'reason':f'STALLED_MULTICHILD_WEAK_{weak}'}
 return {'primary':'WAIT_PENDING_CHILDREN','secondary':['NO_DUPLICATE_ADD'],'reason':'UNRESOLVED_BUT_NOT_STALLED'}

def snap(children,inv,now,incidents):
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.reset_market(1);b.children={x['clientOrderId']:dict(x) for x in children};b.incidents.extend(incidents)
 return b.snapshot(at_ms=now,actual_inventory=inv,pending_cancels=[x['clientOrderId'] for x in children if x.get('cancelPending')],orphan_count=0,engine_state={},portfolio_context={})

def main():
 now=100000;cases=[]
 def add(name,children,inv,inc,expect):
  s=snap(children,inv,now,inc);r=classify_r3s(s);f=s['formationExecutionContext'];ok=r['primary']==expect;cases.append({'name':name,'r31':{'ownershipState':s['ownershipState'],'situationCode':s['situationCode'],'weakSide':f['weakSide'],'liveBySide':f['liveBySide'],'recent15sFillDeltaCount':f['recent15sFillDeltaCount'],'recent15sStallCount':f['recent15sStallCount']},'r3sResponse':r,'expectedPrimary':expect,'pass':ok})
 add('L21_middle_child_stalled_latest_healthy',[mk('m1','MAKER','DOWN',10,0,'RESTING',80000),mk('m2','MAKER','DOWN',10,10,'FILLED',90000,True),mk('m3','MAKER','UP',10,10,'FILLED',97000,True)],{'UP':10,'DOWN':10},[{'eventId':'s1','atMs':95000,'incidentType':'LIVE_NO_FILL_STALL'}],'REASSESS_OLDEST_BLOCKER')
 add('L22_old_partial_latest_fill_progress',[mk('m1','MAKER','DOWN',10,4,'PARTIAL_FILL',88000),mk('m2','MAKER','UP',10,10,'FILLED',97000,True)],{'UP':10,'DOWN':4},[{'eventId':'f1','atMs':99000,'incidentType':'FILL_CONFIRMED'}],'KEEP_AND_OBSERVE_RECOVERY')
 add('L23_unknown_middle_latest_normal',[mk('m1','MAKER','DOWN',10,0,'UNKNOWN_SUBMISSION',85000),mk('m2','MAKER','UP',10,10,'FILLED',98000,True)],{'UP':10,'DOWN':0},[],'WAIT_EXECUTION_CERTAINTY')
 add('L24_multiple_pending_not_stalled',[mk('m1','MAKER','DOWN',10,0,'RESTING',94000),mk('m2','MAKER','DOWN',10,0,'RESTING',96000),mk('m3','MAKER','UP',10,10,'FILLED',98000,True)],{'UP':10,'DOWN':0},[],'WAIT_PENDING_CHILDREN')
 add('L25_cancel_pending_old_plus_new_fill',[mk('m1','MAKER','DOWN',10,0,'CANCEL_PENDING',82000),mk('m2','MAKER','UP',10,10,'FILLED',99000,True)],{'UP':10,'DOWN':0},[{'eventId':'f2','atMs':99500,'incidentType':'FILL_CONFIRMED'}],'WAIT_CANCEL_TERMINAL_ACK')
 rep={'version':'R31_R3S_RESPONSE_L21_L25_V1','researchOnly':True,'actionAuthority':False,'cases':cases,'passed':sum(x['pass'] for x in cases),'total':len(cases),'allPass':all(x['pass'] for x in cases)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
