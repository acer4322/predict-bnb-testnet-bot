from __future__ import annotations
import argparse,json,lzma,sys,time,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
from tools import build_r4_management_simulator_objective_state_episodes_v5 as v5
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';EPS=1e-9

def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def relation(side,weak):return 'WEAK' if side==weak else 'DOMINANT'
def next_transition(t,rid,weak,oid,oj,prov):
 candidates=[]
 # Objective-ledger transitions.
 for e in oj:
  tt=int(e.get('received_at_ms') or 0)
  if tt<=t or tt>t+30000:continue
  et=str(e.get('event_type') or '')
  eo=e.get('portfolio_objective_id')
  if eo==oid and et=='OBJECTIVE_COMPLETION_PROGRESS' and e.get('status')=='RESPONSIBILITY_COMPLETED': candidates.append((tt,'CURRENT_OBJECTIVE_COMPLETES'))
  elif eo==oid and et=='OBJECTIVE_CHECKPOINT' and str(e.get('reason')) in {'PARTIAL_FILL','FULL_FILL'}: candidates.append((tt,'PROGRESS_CURRENT_OBJECTIVE'))
  elif et=='OBJECTIVE_OPENED':
   sd=str(e.get('side') or '')
   if sd in {'UP','DOWN'}: candidates.append((tt,'OPEN_PARALLEL_WEAK' if relation(sd,weak)=='WEAK' else 'OPEN_PARALLEL_DOMINANT'))
 # Handoff-like: current responsibility leaves before a new root appears.
 depart=None
 for e in prov:
  tt=int(e.get('received_at_ms') or 0)
  if tt<=t or tt>t+30000:continue
  if str(e.get('responsibility_id') or '')==rid and e.get('event_type') in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
   depart=tt;break
 if depart is not None:
  for e in prov:
   tt=int(e.get('received_at_ms') or 0)
   if depart<tt<=min(t+30000,depart+5000) and e.get('event_type')=='RESPONSIBILITY_OPENED' and str(e.get('responsibility_id') or '')!=rid:
    candidates.append((tt,'HANDOFF_LIKE_DEPARTURE'));break
 if not candidates:return 'PERSIST_NO_PROGRESS',30.0
 tt,typ=min(candidates,key=lambda z:(z[0],z[1]));return typ,max(0.,(tt-t)/1000.)
def extract(mid):
 d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False,collect_provenance=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b)==core(r);prov=r.get('provenanceJournal') or [];oj,ostate,resp_to_obj=led.materialize(mid,prov);rows=[]
 for x in r.get('managementLifecycleRows') or []:
  rid=str(x.get('checkpointResponsibilityId') or '');t=int(x.get('t') or 0);weak=str(x.get('weakSide') or x.get('checkpointSide') or '');side=str(x.get('checkpointSide') or weak);oid=resp_to_obj.get(rid);ctx=v5.objctx(mid,rid,t,side,oj,resp_to_obj);typ,dt=next_transition(t,rid,weak,oid,oj,prov)
  z={'marketId':mid,'responsibilityId':rid,'t':t,'secondsLeft':float(x.get('seconds_left') or 0),'floor':float(x.get('floor') or 0),'absNet':float(x.get('absNet') or x.get('abs_gap') or 0),'coverage':float(x.get('coverage') or 0),'absnetRatio':float(x.get('absnet_ratio') or 0),'floorPerGross':float(x.get('floor_per_gross') or 0),'weakSide':weak,'checkpointSide':side,'unresolvedQty':float(x.get('checkpointUnresolvedQty') or 0),'progressRatio':float(x.get('checkpointProgressRatio') or 0),'ownerCount':int(x.get('checkpointOwnerCount') or 0),'weakResponsibilityCount':float(x.get('weakResponsibilityCount') or 0),'dominantResponsibilityCount':float(x.get('dominantResponsibilityCount') or 0),'events15s':float(x.get('events_15s') or 0),'transitions15s':float(x.get('transitions_15s') or 0),'oldestOwnerAge':float(x.get('checkpointOldestOwnerAgeS') or 0),'pendingCancelCount':len(x.get('pendingCancelResponsibilityIds') or []),'nextObjectiveTransition':typ,'nextObjectiveTransitionDtS':dt};z.update(ctx);rows.append(z)
 return ce,rows,int((r.get('provenanceSummary') or {}).get('duplicateExecutionApplicationCount') or 0),int((r.get('counts') or {}).get('pendingSubmitReservationBlocks') or 0)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=999);ap.add_argument('--out',required=True);a=ap.parse_args();ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text(encoding='utf-8'))][a.start:a.start+a.count];out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True);rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_SEMIMARKOV_ROWS_V6','markets':[],'rows':[],'duplicateExecutionCredit':0,'pendingSubmitReservationBlocks':0}
 for mid in ids:
  vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.2)
  if cpu>=80 or vm.percent>=86:time.sleep(1)
  ce,rows,dup,blocks=extract(mid);rep['markets'].append({'marketId':mid,'executionCoreExact':ce,'rows':len(rows)});rep['rows'].extend(rows);rep['duplicateExecutionCredit']+=dup;rep['pendingSubmitReservationBlocks']+=blocks;out.write_text(json.dumps(rep),encoding='utf-8');print(json.dumps({'marketId':mid,'exact':ce,'rows':len(rows)}),flush=True)
 print(json.dumps({'status':'COMPLETE','markets':len(rep['markets']),'rows':len(rep['rows']),'exact':sum(x['executionCoreExact'] for x in rep['markets']),'duplicateExecutionCredit':rep['duplicateExecutionCredit'],'pendingBlocks':rep['pendingSubmitReservationBlocks']}))
if __name__=='__main__':main()
