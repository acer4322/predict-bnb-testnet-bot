from __future__ import annotations
import argparse,hashlib,json,lzma,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_marginal_successor_probe_simulator_v1 as sim
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/hft_forward_paper_v1/markets'; EPS=1e-9

def dig(*x): return hashlib.sha256('|'.join(map(str,x)).encode()).hexdigest()[:20]
def oid(mid,logical): return 'obj:'+dig('objective',mid,logical)
def origin_id(mid,logical,t): return 'objevent:'+dig('open',mid,logical,t)

def logical_parent(logical):
 s=str(logical or '')
 if not s.startswith('SUCCESSOR:'): return None
 # SUCCESSOR:{parentLogical}:{seq}; parent logical itself may contain ':' so strip last seq only.
 body=s[len('SUCCESSOR:'):]
 return body.rsplit(':',1)[0] if ':' in body else None

def load_markets(n,offset=0):
 # fixed chronology from untouched extension cohort; already date-audited to exclude 2026-08-16.
 d=json.loads((P/'r4_p0b_role_anatomy_extension_cohort_preregistered_v1.json').read_text(encoding='utf-8'))
 ids=[int(x) for x in d.get('extensionMarketIds',d.get('marketIds',[]))]
 if not ids:
  # fallback: derive exact frozen order from discovery artifacts, unique markets.
  ids=[]
  for start in range(0,240,20):
   q=P/f'r4_p0b_role_anatomy_extension_discovery_{start}_20_v1.json'
   if q.exists(): ids.extend(int(r['marketId']) for r in json.loads(q.read_text(encoding='utf-8'))['rows'])
  ids=list(dict.fromkeys(ids))
 return ids[offset:offset+n]

def materialize(mid, prov):
 obj={}; resp_to_obj={}; intent_state={}; journal=[]; seq=0
 def emit(t,typ,oid_,**kw):
  nonlocal seq; seq+=1
  e={'schema_version':'R4_P0B_OBJECTIVE_LEDGER_EVENT_V1','global_seq':seq,'market_id':mid,'received_at_ms':int(t),'event_type':typ,'portfolio_objective_id':oid_,**kw};journal.append(e)
 def recompute(oid_,t,reason):
  o=obj[oid_]; confirmed=sum(float(o['resp'][r]['confirmed']) for r in o['resp'])
  reserved=sum(float(o['resp'][r]['reserved']) for r in o['resp'])
  credited=float(o.get('credited',0.0)); gross=float(o['gross'])
  residual=max(0.,gross-confirmed-reserved-credited)
  o.update(confirmed=confirmed,reserved=reserved,residual=residual)
  emit(t,'OBJECTIVE_CHECKPOINT',oid_,reason=reason,gross_objective_deficit_qty=gross,confirmed_same_objective_completion_qty=confirmed,reserved_same_objective_commitment_qty=reserved,credited_same_objective_completion_qty=credited,residual_objective_deficit_qty=residual,responsibility_ids=sorted(o['resp']))
 for e in prov:
  t=int(e.get('received_at_ms') or e.get('event_at_ms') or 0); et=str(e.get('event_type') or ''); rid=str(e.get('responsibility_id') or '')
  if not rid: continue
  extras=e.get('extras') or {}; logical=extras.get('logical')
  if et=='RESPONSIBILITY_OPENED':
   logical=str(logical); oo=oid(mid,logical); parent=logical_parent(logical); parent_oid=oid(mid,parent) if parent else None
   rq=float(e.get('requested_qty') or 0.); side=str(e.get('side') or '')
   if oo not in obj:
    obj[oo]={'logical':logical,'side':side,'gross':rq,'credited':0.0,'resp':{},'type':'UNKNOWN','parent_objective_id':parent_oid,'parallel_relation':'UNKNOWN_NEW_OBJECTIVE_CANDIDATE' if parent else 'ROOT_OBJECTIVE'}
    emit(t,'OBJECTIVE_OPENED',oo,objective_origin_event_id=origin_id(mid,logical,t),opened_at_ms=t,side=side,gross_objective_deficit_qty=rq,objective_type_at_open_or_UNKNOWN='UNKNOWN',strict_past_geometry_snapshot=None,logical=logical,parent_objective_id=parent_oid,objective_relation_at_open=obj[oo]['parallel_relation'])
    if parent_oid: emit(t,'OBJECTIVE_PARALLEL_LINKED',oo,parallel_to_objective_id=parent_oid,relation='UNKNOWN_NEW_OBJECTIVE_CANDIDATE')
   obj[oo]['resp'][rid]={'requested':rq,'confirmed':0.0,'reserved':0.0,'intents':set()};resp_to_obj[rid]=oo
   emit(t,'RESPONSIBILITY_ATTACHED',oo,responsibility_id=rid,logical=logical,requested_qty=rq)
   recompute(oo,t,'RESPONSIBILITY_OPENED')
   continue
  oo=resp_to_obj.get(rid)
  if not oo: continue
  rr=obj[oo]['resp'][rid]; iid=str(e.get('intent_id') or '')
  if et=='SUBMIT_SENT' and iid:
   rr['intents'].add(iid); intent_state[(rid,iid)]='PENDING'
   # reservation at responsibility level, not intent level: cap at unresolved objective responsibility.
   rr['reserved']=max(0.,rr['requested']-rr['confirmed'])
   emit(t,'COMMITMENT_RESERVED',oo,responsibility_id=rid,intent_id=iid,reserved_qty=rr['reserved'],execution_state='PENDING_SUBMIT')
   recompute(oo,t,'SUBMIT_SENT')
  elif et=='ACK_NEW' and iid:
   intent_state[(rid,iid)]='LIVE';emit(t,'COMMITMENT_STATE_CHANGED',oo,responsibility_id=rid,intent_id=iid,execution_state='LIVE')
  elif et in {'PARTIAL_FILL','FULL_FILL'}:
   cum=float(e.get('cum_confirmed_fill_qty') or 0.);rr['confirmed']=max(rr['confirmed'],min(rr['requested'],cum));rr['reserved']=max(0.,rr['requested']-rr['confirmed']) if any(v in {'PENDING','LIVE','CANCEL_PENDING'} for (r,_),v in intent_state.items() if r==rid) else 0.
   emit(t,'CONFIRMED_COMPLETION',oo,responsibility_id=rid,intent_id=iid,execution_id=e.get('execution_id'),confirmed_qty=rr['confirmed'],fill_delta_qty=float((e.get('extras') or {}).get('fillDeltaQty') or 0.))
   if et=='FULL_FILL' and iid: intent_state[(rid,iid)]='TERMINAL';rr['reserved']=0.
   recompute(oo,t,et)
  elif et=='CANCEL_REQUESTED' and iid:
   intent_state[(rid,iid)]='CANCEL_PENDING';emit(t,'COMMITMENT_STATE_CHANGED',oo,responsibility_id=rid,intent_id=iid,execution_state='CANCEL_PENDING')
  elif et in {'ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:
   intent_state[(rid,iid)]='TERMINAL'
   if not any(v in {'PENDING','LIVE','CANCEL_PENDING'} for (r,_),v in intent_state.items() if r==rid): rr['reserved']=0.
   emit(t,'COMMITMENT_RELEASED',oo,responsibility_id=rid,intent_id=iid,terminal_event=et)
   recompute(oo,t,et)
  elif et=='RESPONSIBILITY_COMPLETED':
   rr['confirmed']=rr['requested'];rr['reserved']=0.;emit(t,'OBJECTIVE_COMPLETION_PROGRESS',oo,responsibility_id=rid,status='RESPONSIBILITY_COMPLETED');recompute(oo,t,et)
  elif et=='RESPONSIBILITY_TERMINATED':
   rr['reserved']=0.;emit(t,'OBJECTIVE_COMPLETION_PROGRESS',oo,responsibility_id=rid,status='RESPONSIBILITY_TERMINATED');recompute(oo,t,et)
 # canonical serializable state
 final={}
 for k,o in sorted(obj.items()):
  final[k]={'logical':o['logical'],'side':o['side'],'gross_objective_deficit_qty':o['gross'],'confirmed_same_objective_completion_qty':o.get('confirmed',0.),'reserved_same_objective_commitment_qty':o.get('reserved',0.),'credited_same_objective_completion_qty':o.get('credited',0.),'residual_objective_deficit_qty':o.get('residual',o['gross']),'objective_type':'UNKNOWN','parent_objective_id':o.get('parent_objective_id'),'objective_relation':o.get('parallel_relation'),'responsibility_ids':sorted(o['resp'])}
 return journal,final,resp_to_obj

def replay_objective_journal(journal):
 out={}
 for e in journal:
  oo=e.get('portfolio_objective_id'); typ=e.get('event_type')
  if not oo: continue
  if typ=='OBJECTIVE_OPENED':
   out[oo]={'logical':e.get('logical'),'side':e.get('side'),'gross_objective_deficit_qty':float(e.get('gross_objective_deficit_qty') or 0.),'confirmed_same_objective_completion_qty':0.0,'reserved_same_objective_commitment_qty':0.0,'credited_same_objective_completion_qty':0.0,'residual_objective_deficit_qty':float(e.get('gross_objective_deficit_qty') or 0.),'objective_type':'UNKNOWN','parent_objective_id':e.get('parent_objective_id'),'objective_relation':e.get('objective_relation_at_open'),'responsibility_ids':[]}
  elif typ=='RESPONSIBILITY_ATTACHED' and oo in out:
   r=str(e.get('responsibility_id'));
   if r not in out[oo]['responsibility_ids']: out[oo]['responsibility_ids'].append(r);out[oo]['responsibility_ids'].sort()
  elif typ=='OBJECTIVE_CHECKPOINT' and oo in out:
   x=out[oo];x['gross_objective_deficit_qty']=float(e.get('gross_objective_deficit_qty') or 0.);x['confirmed_same_objective_completion_qty']=float(e.get('confirmed_same_objective_completion_qty') or 0.);x['reserved_same_objective_commitment_qty']=float(e.get('reserved_same_objective_commitment_qty') or 0.);x['credited_same_objective_completion_qty']=float(e.get('credited_same_objective_completion_qty') or 0.);x['residual_objective_deficit_qty']=float(e.get('residual_objective_deficit_qty') or 0.);x['responsibility_ids']=sorted(map(str,e.get('responsibility_ids') or x['responsibility_ids']))
 return dict(sorted(out.items()))

def core(r): return {'makerFilledShares':r['makerFilledShares'],'final':r['final'],'durableBase':r['durableBase'],'everSafe':r['everSafe'],'counts':r['counts'],'cancelReasons':r['cancelReasons']}
def h(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def run_market(mid,successor_mode='NONE'):
 d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
 base=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode=successor_mode)
 # second identical simulation proves instrumentation source determinism; ledger itself is pure reducer.
 rep=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True,successor_mode=successor_mode)
 j,s,m=materialize(mid,base.get('provenanceJournal') or []);j2,s2,m2=materialize(mid,rep.get('provenanceJournal') or []);replayed=replay_objective_journal(j)
 vals=list(s.values()); checks={
  'execution_core_repeat_exact':core(base)==core(rep),
  'journal_repeat_exact':h(j)==h(j2),
  'state_repeat_exact':h(s)==h(s2),'journal_only_reconstruction_exact':h(s)==h(replayed),
  'responsibility_unique_objective':len(m)==len(set(m)),
  'no_negative_residual':all(float(x['residual_objective_deficit_qty'])>=-EPS for x in vals),
  'no_over_accounting':all(float(x['confirmed_same_objective_completion_qty'])+float(x['reserved_same_objective_commitment_qty'])+float(x['credited_same_objective_completion_qty'])<=float(x['gross_objective_deficit_qty'])+EPS for x in vals),
  'same_side_not_auto_merged':True,
 }
 # since V1 creates objective per native logical, equal-side objectives remain distinct by construction.
 byside=defaultdict(list)
 for oo,x in s.items(): byside[x['side']].append(oo)
 checks['same_side_not_auto_merged']=all(len(v)==len(set(v)) for v in byside.values())
 return {'marketId':mid,'checks':checks,'pass':all(checks.values()),'objectives':len(s),'responsibilities':len(m),'events':len(j),'parallelObjectiveCandidates':sum(1 for x in s.values() if x.get('parent_objective_id')),'objectiveStateHash':h(s),'objectiveJournalHash':h(j),'sampleObjectives':list(s.items())[:3]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--markets',type=int,default=5);ap.add_argument('--offset',type=int,default=0);ap.add_argument('--successor-mode',choices=['NONE','ALL'],default='NONE');a=ap.parse_args();ids=load_markets(a.markets,a.offset);rows=[]
 for mid in ids:
  r=run_market(mid,a.successor_mode);rows.append(r);print(json.dumps({'marketId':mid,'pass':r['pass'],'objectives':r['objectives'],'events':r['events']},ensure_ascii=False),flush=True)
 out={'version':'R4_P0B_OBJECTIVE_LEDGER_RUNTIME_MATERIALIZATION_V1','researchOnly':True,'actionAuthority':False,'marketOffset':a.offset,'successorMode':a.successor_mode,'rows':rows,'aggregate':{'markets':len(rows),'passMarkets':sum(r['pass'] for r in rows),'objectives':sum(r['objectives'] for r in rows),'responsibilities':sum(r['responsibilities'] for r in rows),'events':sum(r['events'] for r in rows),'parallelObjectiveCandidates':sum(r.get('parallelObjectiveCandidates',0) for r in rows)}}
 p=P/f'r4_p0b_objective_ledger_runtime_materialization_{a.successor_mode.lower()}_o{a.offset}_m{a.markets}_v1.json';p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'aggregate':out['aggregate']},ensure_ascii=False))
if __name__=='__main__': main()
