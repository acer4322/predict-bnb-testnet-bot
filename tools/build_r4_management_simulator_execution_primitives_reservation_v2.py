from __future__ import annotations
import argparse,json,lzma,sys,time,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as sim
P=ROOT/'data/research/r4_v0/p0_provenance_v1';FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'

def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def resource():
 vm=psutil.virtual_memory();return psutil.cpu_percent(.5),vm.percent

def extract(mid):
 d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
 b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False,collect_provenance=False)
 r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True)
 ce=core(b)==core(r);journal=r.get('provenanceJournal') or [];first={}
 for x in r.get('managementLifecycleRows') or []:
  k=str(x['checkpointResponsibilityId'])
  if k not in first or int(x['t'])<int(first[k]['t']):first[k]=x
 rows=[]
 for rid,x in first.items():
  t0=int(x['t']);cut=t0+5000;fut=[e for e in journal if str(e.get('responsibility_id'))==rid and t0<int(e.get('received_at_ms') or 0)<=cut]
  fills=[e for e in fut if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}]
  qty=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0) for e in fills);ttf=min([int(e['received_at_ms'])-t0 for e in fills],default=None)
  unresolved=float(x.get('checkpointUnresolvedQty') or 0);confirmed=float(x.get('checkpointConfirmedQty') or 0);requested=float(x.get('checkpointRequestedQty') or 0)
  rows.append({'marketId':mid,'responsibilityId':rid,'t':t0,'secondsLeft':float(x.get('seconds_left') or 0),'requestedQty':requested,'unresolvedQty':unresolved,'confirmedQty':confirmed,'price':float(x.get('requested_px') or 0),'side':str(x.get('checkpointSide') or ''),'ownerCount':int(x.get('checkpointOwnerCount') or 0),'activeIntentCount':len(x.get('checkpointActiveIntentIds') or []),'weakResponsibilityCount':float(x.get('weakResponsibilityCount') or 0),'dominantResponsibilityCount':float(x.get('dominantResponsibilityCount') or 0),'events15s':float(x.get('events_15s') or 0),'transitions15s':float(x.get('transitions_15s') or 0),'oldestOwnerAge':float(x.get('checkpointOldestOwnerAgeS') or 0),'anyFill5s':int(qty>1e-9),'completed5s':int(any(e.get('event_type')=='RESPONSIBILITY_COMPLETED' for e in fut)),'cancelRequest5s':int(any(e.get('event_type')=='CANCEL_REQUESTED' for e in fut)),'fillQty5s':qty,'fillEventCount5s':len(fills),'newCarrierCount5s':sum(1 for e in fut if e.get('event_type')=='CARRIER_INTENT_CREATED'),'timeToFirstFillMs':ttf,'overfill5s':max(0.0,qty-unresolved)})
 dup=int((r.get('provenanceSummary') or {}).get('duplicateExecutionApplicationCount') or 0)
 return ce,rows,dup,int((r.get('counts') or {}).get('pendingSubmitReservationBlocks') or 0)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cohort',required=True);ap.add_argument('--out',required=True);ap.add_argument('--max-new',type=int,default=999);a=ap.parse_args();ids=[int(x) for x in json.loads(FIX.read_text(encoding='utf-8'))[a.cohort]['markets']];path=ROOT/a.out;path.parent.mkdir(parents=True,exist_ok=True)
 rep=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_PRIMITIVES_RESERVATION_V2','cohort':a.cohort,'markets':[],'rows':[],'resourceDeferrals':[],'duplicateExecutionCredit':0,'pendingSubmitReservationBlocks':0}
 done={int(x['marketId']) for x in rep['markets']};new=0
 for mid in ids:
  if mid in done:continue
  cpu,ram=resource()
  if cpu>=75 or ram>=86:
   rep['resourceDeferrals'].append({'marketId':mid,'cpu':cpu,'ram':ram,'at':int(time.time())});path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'status':'RESOURCE_DEFERRED','marketId':mid,'cpu':cpu,'ram':ram,'completedMarkets':len(done)}));return
  ce,rows,dup,blocks=extract(mid);rep['markets'].append({'marketId':mid,'executionCoreExact':ce,'roots':len(rows)});rep['rows'].extend(rows);rep['duplicateExecutionCredit']+=dup;rep['pendingSubmitReservationBlocks']+=blocks;done.add(mid);new+=1
  path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'marketId':mid,'exact':ce,'roots':len(rows),'overfills':sum(1 for x in rows if x['overfill5s']>1e-9),'pendingBlocks':blocks,'checkpointSaved':True}),flush=True)
  cpu2,ram2=resource()
  if cpu2>=75 or ram2>=86:
   rep['resourceDeferrals'].append({'marketId':'AFTER_'+str(mid),'cpu':cpu2,'ram':ram2,'at':int(time.time())});path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'status':'RESOURCE_DEFERRED_AFTER_MARKET','cpu':cpu2,'ram':ram2,'completedMarkets':len(done)}));return
  if new>=a.max_new:break
 print(json.dumps({'status':'PARTIAL_OR_COMPLETE','completedMarkets':len(done),'totalMarkets':len(ids),'rows':len(rep['rows']),'exact':sum(bool(x['executionCoreExact']) for x in rep['markets']),'overfillRoots':sum(1 for x in rep['rows'] if x['overfill5s']>1e-9),'duplicateExecutionCredit':rep['duplicateExecutionCredit'],'pendingSubmitReservationBlocks':rep['pendingSubmitReservationBlocks']},indent=2))
if __name__=='__main__':main()
