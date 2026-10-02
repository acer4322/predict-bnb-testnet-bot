from __future__ import annotations
import json,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_queue_option_counterfactual_v1 import run_recovery

MIDS=[1739033,1738905,1738894,1738866,1738856,1738846,1738836,1738826,1738785,1738776,1738773,1738764]
OUT=ROOT/'data/research/r4_v0/hourly/r4_creation_time_provenance_contract_v1.json'
CK=ROOT/'data/research/r4_v0/hourly/r4_creation_time_provenance_contract_v1_checkpoint.json'
TZ=ZoneInfo('Asia/Taipei')

def compact(mid,r):
 p=r.get('creationTimeProvenance') or {}; f=p.get('candidateIdentityFrontier'); fills=p.get('makerFillTimeline') or []; orders=p.get('makerOrders') or []
 return {'marketId':mid,'candidateAtMs':r.get('candidateAtMs'),'candidateSide':r.get('candidateRecoverySide'),
   'candidateResponsibilityId':p.get('candidateResponsibilityId'),'candidateClientIntentId':p.get('candidateClientIntentId'),
   'frontier':f,'makerOrderCount':len(orders),'makerFillEventCount':len(fills),
   'makerOrders':[{'orderNum':o.get('orderNum'),'responsibilityId':o.get('responsibilityId'),'clientIntentId':o.get('clientIntentId'),'submittedAtMs':o.get('submittedAtMs'),'side':o.get('side'),'qty':o.get('qty')} for o in orders],
   'makerFillTimeline':[{'eventMs':e.get('eventMs'),'orderNum':e.get('orderNum'),'responsibilityId':e.get('responsibilityId'),'clientIntentId':e.get('clientIntentId'),'shares':e.get('shares'),'cumExecQtyAfter':e.get('cumExecQtyAfter')} for e in fills]}

def valid_row(x):
 candidate=x['candidateAtMs'] is not None
 idok=(not candidate) or bool(x.get('candidateResponsibilityId') and x.get('candidateClientIntentId'))
 fillid=all(bool(e.get('responsibilityId') and e.get('clientIntentId')) for e in x['makerFillTimeline'])
 mono=True
 by={}
 for e in x['makerFillTimeline']:
  cid=e.get('clientIntentId'); c=float(e.get('cumExecQtyAfter') or 0.0)
  if cid in by and c+1e-9<by[cid]: mono=False
  by[cid]=max(by.get(cid,0.0),c)
 fm=True
 if candidate:
  f=x.get('frontier')
  if not f: fm=False
  else:
   a,b,c=float(f['fillShares1s']),float(f['fillShares3s']),float(f['fillShares5s']); rem=float(f['remainingQtyAtCheckpoint'])
   fm=(-1e-9<=a<=b+1e-9<=c+1e-9 and c<=rem+1e-9)
 return candidate,idok,fillid,mono,fm

def main():
 ck={'rows':[]}
 if CK.exists():
  try: ck=json.loads(CK.read_text(encoding='utf-8'))
  except: pass
 done={int(x['marketId']) for x in ck.get('rows',[])}
 for mid in MIDS:
  if mid in done: continue
  r=run_recovery(mid,queue_reinsert=False)
  ck.setdefault('rows',[]).append(compact(mid,r)); CK.write_text(json.dumps(ck,indent=2),encoding='utf-8')
  print(json.dumps({'progress':len(ck['rows']),'marketId':mid,'candidate':r.get('candidateAtMs') is not None},ensure_ascii=False),flush=True)
 rows=sorted(ck['rows'],key=lambda x:MIDS.index(int(x['marketId'])))
 checks=[valid_row(x) for x in rows]
 cand=[x for x,c in zip(rows,checks) if c[0]]
 repeat=[]
 for x in cand[:3]:
  r2=compact(int(x['marketId']),run_recovery(int(x['marketId']),queue_reinsert=False))
  fields=['candidateAtMs','candidateResponsibilityId','candidateClientIntentId','frontier','makerFillTimeline']
  exact=all(x.get(k)==r2.get(k) for k in fields)
  repeat.append({'marketId':x['marketId'],'exact':exact})
 n=len(rows); nc=len(cand)
 identity_complete=sum(c[1] for c in checks if c[0]); fill_complete=sum(c[2] for c in checks); mono=sum(c[3] for c in checks); frontier_ok=sum(c[4] for c in checks if c[0])
 pos5=sum(1 for x in cand if x.get('frontier') and float(x['frontier'].get('fillShares5s') or 0)>1e-9)
 invariants=(nc>0 and identity_complete==nc and fill_complete==n and mono==n and frontier_ok==nc and repeat and all(z['exact'] for z in repeat))
 if nc<5 or pos5==0:
  decision='TESTED_INCONCLUSIVE' if invariants else ('TESTED_REJECTED' if nc>=5 else 'TESTED_INCONCLUSIVE')
 elif invariants: decision='TESTED_KEEP_SIGNAL'
 else: decision='TESTED_REJECTED'
 rep={'testId':'R4_CREATION_TIME_PROVENANCE_CONTRACT_V1_20260827_2035','createdAt':datetime.now(TZ).isoformat(),'status':decision,
  'semanticAxis':'EXECUTION_DATA_PROVENANCE / CREATION_TIME_IMMUTABLE_INTENT_AND_FILL_LINEAGE','actionAuthority':False,
  'cohort':{'marketsRequested':len(MIDS),'marketsCompleted':n,'marketIds':MIDS,'newIndependentChronology':True,'special20260816Sealed':True,'echtgeldTraining':False,'dreamFill':False},
  'primaryResult':{'candidateCheckpointCount':nc,'candidateStableIdentityComplete':identity_complete,'candidateStableIdentityCompletenessRate':identity_complete/nc if nc else None,
    'marketsAllFillEventsIdentityComplete':fill_complete,'fillEventIdentityMarketRate':fill_complete/n if n else None,'marketsAppendOnlyMonotonic':mono,'appendOnlyMonotonicMarketRate':mono/n if n else None,
    'candidateFrontierMonotonic':frontier_ok,'candidateFrontierMonotonicRate':frontier_ok/nc if nc else None,'positiveFillShares5sCount':pos5,'fixedRepeat':repeat,'fixedRepeatExactRate':sum(z['exact'] for z in repeat)/len(repeat) if repeat else None},
  'decision':decision,'rows':rows,
  'interpretation':'Creation-time provenance contract only. Continuous remaining-option belief remains closed and no execution/action authority is granted.'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':decision,'primaryResult':rep['primaryResult']},ensure_ascii=False))
if __name__=='__main__': main()
