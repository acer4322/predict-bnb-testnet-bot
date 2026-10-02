from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
OUT=ROOT/'data/research/r3_v0/r31_recovery_signal_l11_l15_v1.json'
def child(i,role,side,req,fill,state,created,terminal=False):
 return {'intentId':i,'role':role,'side':side,'requestedShares':req,'confirmedFilledShares':fill,'state':state,'createdAtMs':created,'terminal':terminal}
def snap(children,inv,now,inc=None):
 b=R31EchtgeldStateBridgeV1('R3S_R31_TEST');b.children={x['intentId']:dict(x) for x in children};
 # base bridge incidents are an inherited information channel
 b.incidents=list(inc or [])
 return b.snapshot(at_ms=now,actual_inventory=inv,pending_cancels=[],orphan_count=0,engine_state={},portfolio_context={})
def classify(s):
 f=s['formationExecutionContext']; live=f['liveBySide']; unresolved=sum(live[x][r]['unresolvedQty'] for x in ('UP','DOWN') for r in ('maker','taker')); unknown=sum(live[x][r]['unknownCount'] for x in ('UP','DOWN') for r in ('maker','taker')); partial=sum(live[x][r]['partialCount'] for x in ('UP','DOWN') for r in ('maker','taker')); maxage=max(live[x][r]['maxAgeMs'] for x in ('UP','DOWN') for r in ('maker','taker'))
 # deterministic cooperation language, not action authority
 if unknown: state='EXECUTION_UNCERTAIN'
 elif f['recent15sFillDeltaCount']>0 and f['recent15sStallCount']==0: state='RECOVERY_PROGRESS'
 elif unresolved>0 and maxage>=12000 and f['recent15sFillDeltaCount']==0: state='RECOVERY_STALLED'
 elif unresolved>0 or partial>0: state='RECOVERY_PENDING'
 else: state='RECOVERY_CLEAR'
 return {'state':state,'unresolvedQty':unresolved,'maxAgeMs':maxage,'pairedCoverage':f['actualPairedCoverage'],'absNet':f['actualAbsNet']}
def main():
 now=100000;cases=[]
 def add(name,children,inv,inc,expect):
  s=snap(children,inv,now,inc);o=classify(s);cases.append({'name':name,'expected':expect,'observed':o,'pass':o['state']==expect})
 add('L11_recent_maker_fill_recovery',[child('m','MAKER','DOWN',10,6,'LIVE',94000)],{'UP':10,'DOWN':6},[{'eventId':'e1','atMs':99000,'incidentType':'FILL_CONFIRMED'}],'RECOVERY_PROGRESS')
 add('L12_old_unfilled_child_stall',[child('m','MAKER','DOWN',10,0,'LIVE',85000)],{'UP':10,'DOWN':0},[],'RECOVERY_STALLED')
 add('L13_partial_young_pending',[child('m','MAKER','DOWN',10,4,'LIVE',95000)],{'UP':10,'DOWN':4},[],'RECOVERY_PENDING')
 add('L14_unknown_overrides_progress',[child('m','MAKER','DOWN',10,4,'UNKNOWN_SUBMISSION',85000)],{'UP':10,'DOWN':4},[{'eventId':'e1','atMs':99000,'incidentType':'FILL_CONFIRMED'}],'EXECUTION_UNCERTAIN')
 add('L15_terminal_clear',[child('m','MAKER','DOWN',10,10,'FILLED',90000,True)],{'UP':10,'DOWN':10},[],'RECOVERY_CLEAR')
 rep={'version':'R31_RECOVERY_SIGNAL_L11_L15_V1','cases':cases,'passed':sum(x['pass'] for x in cases),'total':len(cases),'allPass':all(x['pass'] for x in cases)};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__':main()
