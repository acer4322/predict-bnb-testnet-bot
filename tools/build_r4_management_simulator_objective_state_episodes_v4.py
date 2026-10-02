from __future__ import annotations
import argparse,json,lzma,sys,time,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as sim
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import hftbacktest_execution_tape_feed_v1 as tape
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
P=ROOT/'data/research/r4_v0/p0_provenance_v1';SRC=ROOT/'data/hft_forward_paper_v1/markets';ENTRY=1092;MAX_REST=5000;EPS=1e-9

def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def rel(side,weak):return 'WEAK' if side==weak else 'DOMINANT'
def objctx(mid,rid,t,side,oj,resp_to_obj):
 pref=[e for e in oj if int(e.get('received_at_ms') or 0)<=t];st=led.replay_objective_journal(pref);active={k:v for k,v in st.items() if float(v.get('residual_objective_deficit_qty') or 0)>EPS or float(v.get('reserved_same_objective_commitment_qty') or 0)>EPS};same=[v for v in active.values() if str(v.get('side'))==side];opp=[v for v in active.values() if str(v.get('side'))!=side];oo=resp_to_obj.get(rid);cur=st.get(oo) or {};gross=float(cur.get('gross_objective_deficit_qty') or 0);res=float(cur.get('residual_objective_deficit_qty') or 0);opens=[e for e in pref if e.get('event_type')=='OBJECTIVE_OPENED'];pars=[e for e in pref if e.get('event_type')=='OBJECTIVE_PARALLEL_LINKED']
 return {'activeObjectiveCount':len(active),'sameSideActiveObjectives':len(same),'oppositeSideActiveObjectives':len(opp),'sameSideResidualQty':sum(float(v.get('residual_objective_deficit_qty') or 0) for v in same),'oppositeSideResidualQty':sum(float(v.get('residual_objective_deficit_qty') or 0) for v in opp),'sameSideReservedQty':sum(float(v.get('reserved_same_objective_commitment_qty') or 0) for v in same),'oppositeSideReservedQty':sum(float(v.get('reserved_same_objective_commitment_qty') or 0) for v in opp),'sameSideConfirmedQty':sum(float(v.get('confirmed_same_objective_completion_qty') or 0) for v in same),'oppositeSideConfirmedQty':sum(float(v.get('confirmed_same_objective_completion_qty') or 0) for v in opp),'currentObjectiveResidualQty':res,'currentObjectiveReservedQty':float(cur.get('reserved_same_objective_commitment_qty') or 0),'currentObjectiveConfirmedQty':float(cur.get('confirmed_same_objective_completion_qty') or 0),'currentObjectiveResidualRatio':res/gross if gross>EPS else 0.0,'currentObjectiveResponsibilityCount':len(cur.get('responsibility_ids') or []),'recentObjectiveOpens5s':sum(int(e.get('received_at_ms') or 0)>=t-5000 for e in opens),'recentObjectiveOpens15s':sum(int(e.get('received_at_ms') or 0)>=t-15000 for e in opens),'recentParallelObjectiveOpens15s':sum(int(e.get('received_at_ms') or 0)>=t-15000 for e in pars),'currentObjectiveKnown':int(bool(oo and oo in st))}
def extract(mid):
 d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));events,times,meta=tape.build_archive_events(mid,trade_offset='mid');orders,takers,dec=v2.prep(d,meta,ENTRY+MAX_REST);b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False,collect_provenance=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b)==core(r);prov=r.get('provenanceJournal') or [];oj,ostate,resp_to_obj=led.materialize(mid,prov);first={}
 for x in r.get('managementLifecycleRows') or []:
  rid=str(x['checkpointResponsibilityId'])
  if rid not in first or int(x['t'])<int(first[rid]['t']):first[rid]=x
 rows=[]
 for rid,x in first.items():
  t0=int(x['t']);cut=t0+30000;weak=str(x.get('weakSide') or x.get('checkpointSide') or '');pe=[]
  for e in prov:
   rt=int(e.get('received_at_ms') or 0)
   if not (t0<rt<=cut):continue
   et=str(e.get('event_type') or '')
   if et not in {'RESPONSIBILITY_OPENED','CARRIER_INTENT_CREATED','ACK_NEW','PARTIAL_FILL','FULL_FILL','CANCEL_REQUESTED','ACK_CANCELED','RESPONSIBILITY_COMPLETED'}:continue
   side=str(e.get('side') or '');pe.append({'dtMs':rt-t0,'eventType':et,'responsibilityId':e.get('responsibility_id'),'intentId':e.get('intent_id'),'side':side,'sideRelation':rel(side,weak) if side in {'UP','DOWN'} else None,'qty':float((e.get('extras') or {}).get('fillDeltaQty') or e.get('requested_qty') or 0),'price':float(e.get('price') or 0),'leavesQty':float(e.get('leaves_qty') or 0)})
  for z in takers:
   tt=int(z['t'])
   if t0<tt<=cut:
    side=str(z['side']);pe.append({'dtMs':tt-t0,'eventType':'TAKER_EXECUTION','responsibilityId':None,'intentId':None,'side':side,'sideRelation':rel(side,weak),'qty':float(z['q']),'price':float(z['px']),'leavesQty':0.0})
  pe.sort(key=lambda z:(int(z['dtMs']),str(z['eventType']),str(z.get('responsibilityId'))));base={'marketId':mid,'responsibilityId':rid,'t':t0,'secondsLeft':float(x.get('seconds_left') or 0),'floor':float(x.get('floor') or 0),'absNet':float(x.get('absNet') or x.get('abs_gap') or 0),'coverage':float(x.get('coverage') or 0),'absnetRatio':float(x.get('absnet_ratio') or 0),'floorPerGross':float(x.get('floor_per_gross') or 0),'weakSide':weak,'dominantSide':str(x.get('dominantSide') or ''),'checkpointSide':str(x.get('checkpointSide') or ''),'requestedPx':float(x.get('requested_px') or 0),'unresolvedQty':float(x.get('checkpointUnresolvedQty') or 0),'progressRatio':float(x.get('checkpointProgressRatio') or 0),'ownerCount':int(x.get('checkpointOwnerCount') or 0),'weakResponsibilityCount':float(x.get('weakResponsibilityCount') or 0),'dominantResponsibilityCount':float(x.get('dominantResponsibilityCount') or 0),'events15s':float(x.get('events_15s') or 0),'transitions15s':float(x.get('transitions_15s') or 0),'oldestOwnerAge':float(x.get('checkpointOldestOwnerAgeS') or 0),'pendingCancelCount':len(x.get('pendingCancelResponsibilityIds') or []),'portfolioEvents30s':pe};base.update(objctx(mid,rid,t0,str(x.get('checkpointSide') or weak),oj,resp_to_obj));rows.append(base)
 return ce,rows,int((r.get('provenanceSummary') or {}).get('duplicateExecutionApplicationCount') or 0),int((r.get('counts') or {}).get('pendingSubmitReservationBlocks') or 0)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=999);ap.add_argument('--out',required=True);a=ap.parse_args();ids=[int(x) for x in json.loads((ROOT/a.ids_json).read_text(encoding='utf-8'))][a.start:a.start+a.count];out=ROOT/a.out;out.parent.mkdir(parents=True,exist_ok=True);rep={'version':'R4_MANAGEMENT_SIMULATOR_OBJECTIVE_STATE_EPISODES_V4','markets':[],'rows':[],'duplicateExecutionCredit':0,'pendingSubmitReservationBlocks':0}
 for mid in ids:
  vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.2)
  if cpu>=80 or vm.percent>=86:time.sleep(1)
  ce,rows,dup,blocks=extract(mid);rep['markets'].append({'marketId':mid,'executionCoreExact':ce,'roots':len(rows)});rep['rows'].extend(rows);rep['duplicateExecutionCredit']+=dup;rep['pendingSubmitReservationBlocks']+=blocks;out.write_text(json.dumps(rep),encoding='utf-8');print(json.dumps({'marketId':mid,'exact':ce,'roots':len(rows),'objectiveKnown':sum(r['currentObjectiveKnown'] for r in rows)}),flush=True)
 print(json.dumps({'status':'COMPLETE','markets':len(rep['markets']),'roots':len(rep['rows']),'exact':sum(x['executionCoreExact'] for x in rep['markets']),'duplicateExecutionCredit':rep['duplicateExecutionCredit'],'pendingBlocks':rep['pendingSubmitReservationBlocks']}))
if __name__=='__main__':main()
