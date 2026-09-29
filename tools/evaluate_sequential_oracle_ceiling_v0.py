from __future__ import annotations
import argparse, json, sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_pair_completion_counterfactual_v3_sequence as cf
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TEACHER=D/'sequential_arbitration_option_teacher_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
OUT=D/'sequential_oracle_ceiling_v0.jsonl'
REPORT=D/'sequential_oracle_ceiling_v0_report.json'

def load_teacher():
 rows=[json.loads(x) for x in TEACHER.read_text(encoding='utf-8').splitlines() if x.strip()]
 by={}
 for r in sorted(rows,key=lambda z:(int(z['marketId']),int(z['sequenceStep']))): by.setdefault(int(r['marketId']),[]).append(r)
 out={}
 for mid,rs in by.items():
  terminal=next((r for r in rs if r['teacherAction']!='WAIT_FOR_CLARITY'),rs[-1])
  out[mid]={'action':terminal['teacherAction'],'delayMs':int(terminal['delayMs']),'candidateAtMs':int(terminal['candidateAtMs']),'path':[r['teacherAction'] for r in rs]}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--scope',choices=['forward23','validation26','all149'],default='forward23');ap.add_argument('--limit',type=int,default=0);ap.add_argument('--reset',action='store_true');a=ap.parse_args()
 mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
 if a.scope=='forward23': mids=mids[126:]
 elif a.scope=='validation26': mids=mids[100:126]
 teacher=load_teacher();
 if a.reset and OUT.exists(): OUT.unlink()
 done={}
 if OUT.exists():
  for x in OUT.read_text(encoding='utf-8').splitlines():
   if x.strip(): q=json.loads(x); done[(str(q.get('scope')),int(q['marketId']))]=q
 todo=[m for m in mids if (a.scope,m) not in done]
 if a.limit>0: todo=todo[:a.limit]
 with OUT.open('a',encoding='utf-8') as fh:
  for i,mid in enumerate(todo,1):
   spec=teacher[mid]; b=cf.run_recovery(mid,False,candidate_delay_ms=0)
   if spec['action']=='REPLACE_ROUTE': o=cf.run_recovery(mid,True,candidate_delay_ms=spec['delayMs'])
   else: o=b
   def delta(k):
    x=b.get(k);y=o.get(k);return (float(y)-float(x)) if x is not None and y is not None else None
   row={'version':'SEQUENTIAL_ORACLE_CEILING_V0','scope':a.scope,'marketId':mid,'teacherAction':spec['action'],'teacherDelayMs':spec['delayMs'],'teacherPath':spec['path'],
        'baselinePnl':b.get('realizedPnl'),'oraclePnl':o.get('realizedPnl'),'deltaPnl':delta('realizedPnl'),
        'baselineTargetErrorArea':b.get('targetErrorAreaShareSeconds'),'oracleTargetErrorArea':o.get('targetErrorAreaShareSeconds'),'deltaTargetErrorArea':delta('targetErrorAreaShareSeconds'),
        'baselineExposureArea':b.get('combinedExposureAreaShareSeconds'),'oracleExposureArea':o.get('combinedExposureAreaShareSeconds'),'deltaExposureArea':delta('combinedExposureAreaShareSeconds'),
        'baselineFinalAbsTrackingError':b.get('finalAbsTrackingError'),'oracleFinalAbsTrackingError':o.get('finalAbsTrackingError'),'deltaFinalAbsTrackingError':delta('finalAbsTrackingError'),
        'baselineFinalAbsNet':b.get('combinedFinalAbsNet'),'oracleFinalAbsNet':o.get('combinedFinalAbsNet'),'deltaFinalAbsNet':delta('combinedFinalAbsNet'),
        'oracleIntervention':o.get('intervention') if spec['action']=='REPLACE_ROUTE' else None,
        'diagnosticOnly':'Teacher uses counterfactual future Pareto information; this can never be graduation/promotion evidence.'}
   fh.write(json.dumps(row,ensure_ascii=False,allow_nan=True)+'\n');fh.flush();print(json.dumps({'progress':i,'marketId':mid,'action':spec['action'],'delayMs':spec['delayMs'],'deltaPnl':row['deltaPnl'],'deltaTrackArea':row['deltaTargetErrorArea'],'deltaAbsNet':row['deltaFinalAbsNet']},ensure_ascii=False),flush=True)
 rows=[json.loads(x) for x in OUT.read_text(encoding='utf-8').splitlines() if x.strip() and json.loads(x).get('scope')==a.scope]
 pnl=[r for r in rows if r.get('baselinePnl') is not None and r.get('oraclePnl') is not None]
 rep={'version':'SEQUENTIAL_ORACLE_CEILING_V0_REPORT','scope':a.scope,'researchOnly':True,'graduationEligible':False,'markets':len(rows),'actionCounts':dict(Counter(r['teacherAction'] for r in rows)),
      'pnlMarkets':len(pnl),'baselineTotalPnl':sum(float(r['baselinePnl']) for r in pnl),'oracleTotalPnl':sum(float(r['oraclePnl']) for r in pnl),'sumDeltaPnl':sum(float(r['deltaPnl']) for r in pnl),
      'baselineWins':sum(float(r['baselinePnl'])>0 for r in pnl),'oracleWins':sum(float(r['oraclePnl'])>0 for r in pnl),
      'baselineWinRate':sum(float(r['baselinePnl'])>0 for r in pnl)/len(pnl) if pnl else None,'oracleWinRate':sum(float(r['oraclePnl'])>0 for r in pnl)/len(pnl) if pnl else None,
      'sumDeltaTargetErrorArea':sum(float(r['deltaTargetErrorArea']) for r in rows if r.get('deltaTargetErrorArea') is not None),'sumDeltaExposureArea':sum(float(r['deltaExposureArea']) for r in rows if r.get('deltaExposureArea') is not None),'sumDeltaFinalAbsNet':sum(float(r['deltaFinalAbsNet']) for r in rows if r.get('deltaFinalAbsNet') is not None),
      'guard':'Offline oracle diagnostic only. It proves or falsifies action-space value; it is not a deployable model.'}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(REPORT),'summary':rep},ensure_ascii=False,allow_nan=True))
if __name__=='__main__':main()
