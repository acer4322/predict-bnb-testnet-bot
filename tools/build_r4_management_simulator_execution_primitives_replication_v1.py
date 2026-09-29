from __future__ import annotations
import json,time,psutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.build_r4_management_simulator_execution_primitives_reservation_v2 import extract
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_simulator_factorized_execution_replication_v1_preregistered.json'
OUT=ROOT/'.lan_worker_v1/results/r4-factorized-exec-rep20/rep20.json'

def resource():
 vm=psutil.virtual_memory();return psutil.cpu_percent(.5),vm.percent

def main():
 ids=[int(x) for x in json.loads(PRE.read_text(encoding='utf-8'))['replicationCohort']];OUT.parent.mkdir(parents=True,exist_ok=True)
 rep=json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_PRIMITIVES_REPLICATION_V1','markets':[],'rows':[],'resourceDeferrals':[],'duplicateExecutionCredit':0,'pendingSubmitReservationBlocks':0}
 done={int(x['marketId']) for x in rep['markets']}
 for mid in ids:
  if mid in done:continue
  cpu,ram=resource()
  if cpu>=75 or ram>=86:
   rep['resourceDeferrals'].append({'marketId':mid,'cpu':cpu,'ram':ram,'at':int(time.time())});OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'status':'RESOURCE_DEFERRED','marketId':mid,'cpu':cpu,'ram':ram,'completedMarkets':len(done)}));return
  ce,rows,dup,blocks=extract(mid);rep['markets'].append({'marketId':mid,'executionCoreExact':ce,'roots':len(rows)});rep['rows'].extend(rows);rep['duplicateExecutionCredit']+=dup;rep['pendingSubmitReservationBlocks']+=blocks;done.add(mid);OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'marketId':mid,'exact':ce,'roots':len(rows),'overfills':sum(1 for x in rows if x['overfill5s']>1e-9),'pendingBlocks':blocks}),flush=True)
 print(json.dumps({'status':'COMPLETE','markets':len(rep['markets']),'rows':len(rep['rows']),'exact':sum(bool(x['executionCoreExact']) for x in rep['markets']),'overfillRoots':sum(1 for x in rep['rows'] if x['overfill5s']>1e-9),'duplicateExecutionCredit':rep['duplicateExecutionCredit'],'pendingSubmitReservationBlocks':rep['pendingSubmitReservationBlocks']},indent=2))
if __name__=='__main__':main()
