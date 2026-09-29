from __future__ import annotations
import json,lzma,sys,math
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
from tools import test_r4_p0b_objective_ledger_runtime_materialization_v1 as led
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'; EPS=1e-9

def ctx_before(j,open_idx,parent_oid,side,t):
 pref=j[:open_idx]; st=led.replay_objective_journal(pref)
 active={k:v for k,v in st.items() if float(v.get('residual_objective_deficit_qty') or 0)>EPS or float(v.get('reserved_same_objective_commitment_qty') or 0)>EPS}
 ss=[(k,v) for k,v in active.items() if v.get('side')==side]; os=[(k,v) for k,v in active.items() if v.get('side')!=side]
 opens=[e for e in pref if e.get('event_type')=='OBJECTIVE_OPENED']
 pars=[e for e in pref if e.get('event_type')=='OBJECTIVE_PARALLEL_LINKED']
 po=st.get(parent_oid) or {}; parent_open=next((e for e in reversed(opens) if e.get('portfolio_objective_id')==parent_oid),None)
 ages=[]
 for k,v in ss:
  oe=next((e for e in reversed(opens) if e.get('portfolio_objective_id')==k),None)
  if oe: ages.append(max(0.,(t-int(oe.get('opened_at_ms') or oe.get('received_at_ms') or t))/1000.))
 gross=float(po.get('gross_objective_deficit_qty') or 0.);res=float(po.get('residual_objective_deficit_qty') or 0.)
 return {
  'same_side_active_objectives':len(ss),'opposite_side_active_objectives':len(os),
  'same_side_total_residual':sum(float(v.get('residual_objective_deficit_qty') or 0.) for _,v in ss),'opposite_side_total_residual':sum(float(v.get('residual_objective_deficit_qty') or 0.) for _,v in os),
  'recent_objective_opens_5s':sum(int(e.get('received_at_ms') or 0)>=t-5000 for e in opens),'recent_objective_opens_15s':sum(int(e.get('received_at_ms') or 0)>=t-15000 for e in opens),
  'recent_parallel_opens_15s':sum(int(e.get('received_at_ms') or 0)>=t-15000 for e in pars),
  'parent_objective_age_s':None if not parent_open else max(0.,(t-int(parent_open.get('opened_at_ms') or parent_open.get('received_at_ms') or t))/1000.),
  'parent_residual':res,'parent_reserved':float(po.get('reserved_same_objective_commitment_qty') or 0.),'parent_confirmed':float(po.get('confirmed_same_objective_completion_qty') or 0.),
  'parent_residual_ratio':res/gross if gross>EPS else 0.,'same_side_oldest_objective_age_s':max(ages) if ages else 0.
 }

def main():
 rows=json.loads((P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json').read_text(encoding='utf-8'))['rows']
 rows=[r for r in rows if r.get('knownRole') in {'PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}]
 out=[]
 for r in rows:
  mid=int(r['marketId']); key=str(r['candidateKey']); d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
  z=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode='ONE',successor_target=key)
  j,s,m=led.materialize(mid,z.get('provenanceJournal') or [])
  parent=str(r.get('parentLogical')); poid=led.oid(mid,parent); t=int(r['candidateT']); side=str(r['candidateSide'])
  idx=None; oo=None
  for i,e in enumerate(j):
   if e.get('event_type')=='OBJECTIVE_OPENED' and e.get('parent_objective_id')==poid and abs(int(e.get('received_at_ms') or 0)-t)<=1:
    idx=i;oo=e.get('portfolio_objective_id');break
  if idx is None:
   out.append({'marketId':mid,'candidateKey':key,'error':'NO_PARALLEL_OBJECTIVE_OPEN'});continue
  c=ctx_before(j,idx,poid,side,t); c.update({'marketId':mid,'candidateKey':key,'groundTruth':'SAME_OBJECTIVE' if r['knownRole']=='PREPOSITION_REPAIR_SUBSTITUTE' else 'DIFFERENT_OBJECTIVE'})
  out.append(c); print(json.dumps({'marketId':mid,'gt':c['groundTruth'],'ctx':{k:c[k] for k in ['same_side_active_objectives','same_side_total_residual','recent_objective_opens_15s','recent_parallel_opens_15s','parent_objective_age_s','parent_residual_ratio']}},ensure_ascii=False),flush=True)
 valid=[x for x in out if 'error' not in x]; same=[x for x in valid if x['groundTruth']=='SAME_OBJECTIVE']; diff=[x for x in valid if x['groundTruth']=='DIFFERENT_OBJECTIVE']
 feats=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','recent_parallel_opens_15s','parent_objective_age_s','parent_residual','parent_reserved','parent_confirmed','parent_residual_ratio','same_side_oldest_objective_age_s']
 anatomy={}
 for f in feats:
  sv=[float(x[f]) for x in same if x.get(f) is not None]; dv=[float(x[f]) for x in diff if x.get(f) is not None]
  p=sum(a>b for a in sv for b in dv)+.5*sum(a==b for a in sv for b in dv); den=len(sv)*len(dv)
  anatomy[f]={'sameMedian':sorted(sv)[len(sv)//2] if sv else None,'differentMedian':sorted(dv)[len(dv)//2] if dv else None,'pSameGreater':p/den if den else None}
 rep={'version':'R4_P0B_OBJECTIVE_CONTEXT_MEMORY_V1','status':'PASS' if len(valid)==18 else 'FAIL','counts':{'valid':len(valid),'same':len(same),'different':len(diff)},'anatomy':anatomy,'rows':out,'interpretation':'Generic objective-history context is strict-past and reconstructible. This is anatomy only; no grouping authority or threshold is promoted.'}
 q=P/'r4_p0b_objective_context_memory_v1.json';q.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'counts':rep['counts'],'anatomy':anatomy},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
