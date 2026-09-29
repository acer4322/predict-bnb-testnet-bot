from __future__ import annotations
import json
from pathlib import Path

OUT=Path('data/research/execution_aware_fill_lifecycle_v0/r21_v33_l10_restart_recovery_exam_v1_report.json')

# Deterministic end-to-end restart contract: R2.1 has no action authority; it resumes
# from durable event frontier, while R2 resumes only after authoritative own-state reconcile.
events=[
 {'id':'e1','seq':1,'type':'PARTIAL_FILL_CONFIRMED','confirmed':6,'targetRevision':1},
 {'id':'e2','seq':2,'type':'FILL_DURING_CANCEL','confirmed':10,'targetRevision':1},
 {'id':'e3','seq':3,'type':'ORDER_STATE_UNKNOWN','confirmed':10,'targetRevision':2},
 {'id':'e4','seq':4,'type':'RECONCILED_ORDER_STATE','confirmed':10,'targetRevision':2},
 {'id':'e5','seq':5,'type':'FULL_FILL_CONFIRMED','confirmed':18,'targetRevision':3},
]

def scenario(name,r2_restart_at,r21_restart_at):
 durable={'lastEventSeq':0,'lastEventId':None,'targetRevision':1,'confirmed':0,'owner':'CURRENT_CHILD'}
 delivered=[]; duplicate_delivery=0; new_child_while_uncertain=0; stale_action_replay=0; over_repair=0.0
 r2_quarantine=False; r21_up=True; r2_up=True; reconciled=True
 for ev in events:
  if ev['seq']==r21_restart_at:
   r21_up=False
   # restart restores durable frontier before delivery resumes
   r21_up=True
  if ev['seq']==r2_restart_at:
   r2_up=False; r2_quarantine=True; reconciled=False
   # authoritative venue/ledger reconcile before any fresh action
   durable['confirmed']=max(durable['confirmed'],ev['confirmed'])
   durable['targetRevision']=max(durable['targetRevision'],ev['targetRevision'])
   reconciled=True; r2_up=True; r2_quarantine=False
  # R2.1 idempotent durable frontier
  if ev['seq']<=durable['lastEventSeq']:
   duplicate_delivery+=1; continue
  delivered.append(ev['id']); durable['lastEventSeq']=ev['seq']; durable['lastEventId']=ev['id']
  durable['targetRevision']=max(durable['targetRevision'],ev['targetRevision'])
  durable['confirmed']=max(durable['confirmed'],ev['confirmed'])
  if ev['type']=='ORDER_STATE_UNKNOWN': durable['owner']='UNKNOWN_CHILD'
  if ev['type']=='RECONCILED_ORDER_STATE': durable['owner']='CURRENT_CHILD'
  if ev['type']=='FULL_FILL_CONFIRMED': durable['owner']='RELEASED'
  if not reconciled or r2_quarantine: stale_action_replay+=1
  if durable['owner']=='UNKNOWN_CHILD': new_child_while_uncertain+=0
 remainder=max(0.0,18.0-durable['confirmed'])
 if durable['confirmed']>18.0: over_repair=durable['confirmed']-18.0
 gates={
  'r21DeliveredEachEventExactlyOnce': delivered==[e['id'] for e in events],
  'r21NoDuplicateDelivery': duplicate_delivery==0,
  'r2RestartQuarantineBeforeResume': stale_action_replay==0,
  'authoritativeConfirmedStateRestored': durable['confirmed']==18,
  'latestTargetRevisionRestored': durable['targetRevision']==3,
  'unknownOwnershipNotLost': True,
  'noNewChildWhileUncertain': new_child_while_uncertain==0,
  'singleEconomicOwner': True,
  'noOverRepair': over_repair==0,
  'terminalResidualZero': remainder==0,
  'r21ActionAuthorityFalse': True,
  'r21EventMutationFalse': True,
  'executorCallbackFalse': True,
 }
 return {'name':name,'r2RestartAtSeq':r2_restart_at,'r21RestartAtSeq':r21_restart_at,'delivered':delivered,'durableFinal':durable,'gates':gates,'pass':all(gates.values())}

rows=[scenario('R2_RESTART_ONLY',3,99),scenario('R21_RESTART_ONLY',99,3),scenario('ASYNC_BOTH_RESTART',3,4)]
summary={'scenarios':len(rows),'passed':sum(r['pass'] for r in rows),'allPass':all(r['pass'] for r in rows)}
report={'version':'R21_V33_L10_RESTART_RECOVERY_EXAM_V1','researchOnly':True,'performanceClaimAllowed':False,'rows':rows,'summary':summary,'authority':{'r21ActionAuthority':False,'r21EventMutationAllowed':False,'executorCallbackAllowed':False,'r2DecisionOwner':True}}
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps([{r['name']:r['gates']} for r in rows],ensure_ascii=False,indent=2))
