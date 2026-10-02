"""BTC5M Target core-cycle V9 exact fork: persistent responsibility Active execution child.

Frozen management core:
- SOURCE_EVENT progress clock
- FORWARD desired-gross progression
- own confirmed exposure feedback
- persistent residual responsibility
- TERMINAL re-arm
- JOINT Repair/Expand admissibility
- market-direction coupling OFF

Only route topology varies:
PASSIVE_ONLY: exact passive core comparator.
PASSIVE_TERMINAL_ACTIVE_RESIDUAL: when a PASSIVE carrier newly terminalizes with
unfilled quantity and same-side persistent residual still exists, at most one
ACTIVE child may service min(source unfilled, current same-side residual) at the
current ask. No new objective/risk authority is created. ACTIVE terminal does not
spawn another ACTIVE child; later continuation returns to the frozen manager.

Research only. Target actions are scoring-only. No parameter/sizing/threshold
optimization, no fixed-second Active trigger, no PnL/winner runtime use, no 8781.
"""
from __future__ import annotations
import argparse
import bisect
from copy import deepcopy
import gzip,hashlib,importlib.util,json,math,os
from pathlib import Path
import shutil,sys,time,traceback

HELPER_PATH=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/run_target_core_cycle_rearm_v3.py')
ADAPTER_STAGED=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_native_active_adapter_v1.py')
TRAIN_BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_recovery_train_20260911_v3')
TRANSFER_BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/v20_consumed_btc5_transfer5_20260912_v1')
ORIGINAL_MIDS=(2022527,2022538,2022602)
TRANSFER_MIDS=(2023438,2026085,2026817,2028352,2029246)
TEMPLATE_ROOT=Path(r'C:/BTC5M-worker/.tmp/open_funding_recovery_train_20260911_v3')
BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
OPEN_POLICY_SHA='507bd3724ec534e6c9c64a479aa3985f77304f796529bad765780e9e538ca1dd'
RECOVERY_SHA='2468fd631c277e114c3f943bfc9f4867c4f06bdab5f69a26b24d8a8685dd00f1'
ROUTES=('PASSIVE_ONLY','PASSIVE_TERMINAL_ACTIVE_RESIDUAL','PARALLEL_ACTIVE_PASSIVE_RESIDUAL')


def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(262144),b''):h.update(b)
 return h.hexdigest()

def load_module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def load_source_from(bundle,mid,window):
 p=bundle/f'input_{mid}.json.gz'
 with gzip.open(p,'rb') as f:data=f.read(8*1024*1024+1)
 if len(data)>8*1024*1024:raise RuntimeError('source too large')
 s=json.loads(data);assert int(s['market']['market_id'])==mid
 assert [s['market']['window_start_ms'],s['market']['window_end_ms']]==list(window)
 return s

def post_fork_metrics(states,source,qref,hit):
 times=[int(x['t']) for x in states]
 by={}
 for aa in source['targetActions']:
  if aa.get('quote_type')=='BID' and aa.get('side') in ('UP','DOWN') and float(aa.get('shares') or 0)>0:
   by.setdefault(int(aa['event_ms']),[]).append(aa)
 tinv={'UP':0.,'DOWN':0.};tcost=0.;terms=[0.,0.,0.,0.];n=0
 for t,arr in sorted(by.items()):
  for aa in arr:tinv[aa['side']]+=float(aa['shares']);tcost+=float(aa['price'])*float(aa['shares'])
  if t<int(hit['t']):continue
  j=bisect.bisect_right(times,t)-1
  own=states[j] if j>=0 else {'inv':{'UP':0.,'DOWN':0.},'cost':0.}
  tv=[tinv['UP']/qref,tinv['DOWN']/qref,(tinv['UP']-tcost)/qref,(tinv['DOWN']-tcost)/qref]
  oi=own['inv'];oc=float(own['cost']);ov=[oi['UP']/qref,oi['DOWN']/qref,(oi['UP']-oc)/qref,(oi['DOWN']-oc)/qref]
  for k in range(4):terms[k]+=(ov[k]-tv[k])**2
  n+=1
 coord=[x/n for x in terms] if n else [None]*4
 path=sum(coord)/4 if n else None
 side=hit['side'];baseq=float(hit['trigger_inventory'][side]);res=float(hit['residual']);basecost=float(hit['trigger_cost']);aft=[x for x in states if int(x['t'])>=int(hit['t'])]
 comp=None
 for idx,st in enumerate(aft):
  if float(st['inv'][side])-baseq+1e-9>=res:
   comp=dict(state_steps=idx,t=int(st['t']),elapsed_ms=int(st['t'])-int(hit['t']),cost_delta=float(st['cost'])-basecost,side_qty_delta=float(st['inv'][side])-baseq);break
 end=aft[-1] if aft else states[-1];end_delta=max(0.,float(end['inv'][side])-baseq);floors=[min(float(x['inv']['UP']),float(x['inv']['DOWN']))-float(x['cost']) for x in aft];bests=[max(float(x['inv']['UP']),float(x['inv']['DOWN']))-float(x['cost']) for x in aft]
 return dict(target_points=n,post_path_mse=path,post_coordinate_mse=coord,responsibility_side=side,responsibility_qty=res,completion=comp,end_completion_fraction=min(1.,end_delta/max(res,1e-12)),end_side_qty_delta=end_delta,min_post_floor=min(floors) if floors else None,max_post_best=max(bests) if bests else None,terminal_floor=(min(float(end['inv']['UP']),float(end['inv']['DOWN']))-float(end['cost'])),terminal_best=(max(float(end['inv']['UP']),float(end['inv']['DOWN']))-float(end['cost'])))


def adaptive_rate_accept(mode,filled,qty,prior_rate):
 unfilled=max(0.,float(qty)-float(filled))
 if not (float(filled)>1e-12 and unfilled>1e-12):return False,'ADMISSION_NOT_PARTIAL_SOURCE'
 if prior_rate is None:return False,'ADMISSION_NO_STRICT_PAST_PASSIVE_RATE'
 rate=float(filled)/max(float(qty),1e-12)
 if mode=='PARTIAL_BELOW_PRIOR_PASSIVE_RATE':return (rate<prior_rate),('' if rate<prior_rate else 'ADMISSION_NOT_BELOW_PRIOR_PASSIVE_RATE')
 if mode=='PARTIAL_ABOVE_PRIOR_PASSIVE_RATE':return (rate>=prior_rate),('' if rate>=prior_rate else 'ADMISSION_NOT_ABOVE_PRIOR_PASSIVE_RATE')
 raise ValueError('unknown adaptive rate mode')

EPS_ATOMIC=1e-9
class AtomicResponsibilityLedger:
 def __init__(self):
  self.q={'UP':[],'DOWN':[]};self.next_id=1;self.events=[];self.born_qty={'UP':0.0,'DOWN':0.0};self.repaired_qty={'UP':0.0,'DOWN':0.0};self.births=0;self.completed=0;self.stacking_births=0;self.same_clock_repair_then_birth=0;self.direct_pair_qty=0.0;self.total_fill={'UP':0.0,'DOWN':0.0};self.max_queue={'UP':0,'DOWN':0};self.completed_rows=[]
 def outstanding(self,side):return sum(float(r['remaining']) for r in self.q[side])
 def totals(self):return {s:self.outstanding(s) for s in ('UP','DOWN')}
 def _pay(self,target_side,qty,t,fill_side,payments):
  rem=max(0.0,float(qty))
  while rem>EPS_ATOMIC and self.q[target_side]:
   r=self.q[target_side][0];p=min(rem,float(r['remaining']))
   if p<=EPS_ATOMIC:break
   r['remaining']-=p;r['paid']+=p;r['payment_clocks']+=1;self.repaired_qty[target_side]+=p;rem-=p
   payments.append({'responsibility_id':r['id'],'responsibility_side':target_side,'fill_side':fill_side,'qty':p,'remaining_after':max(0.0,r['remaining'])})
   if r['remaining']<=EPS_ATOMIC:
    r['remaining']=0.0;r['completed_t']=int(t);self.completed+=1;self.completed_rows.append(dict(r));self.q[target_side].pop(0)
  return rem
 def _birth(self,side,qty,t,births):
  q=max(0.0,float(qty))
  if q<=EPS_ATOMIC:return
  stacked=bool(self.q[side]);self.stacking_births+=int(stacked);r={'id':self.next_id,'side':side,'born_t':int(t),'initial':q,'remaining':q,'paid':0.0,'payment_clocks':0,'stacked_at_birth':stacked};self.next_id+=1;self.q[side].append(r);self.born_qty[side]+=q;self.births+=1;self.max_queue[side]=max(self.max_queue[side],len(self.q[side]));births.append({'responsibility_id':r['id'],'side':side,'qty':q,'stacked':stacked})
 def process_batch(self,t,up_fill,down_fill,fill_rows=None):
  u=max(0.0,float(up_fill));d=max(0.0,float(down_fill))
  if u<=EPS_ATOMIC and d<=EPS_ATOMIC:return None
  self.total_fill['UP']+=u;self.total_fill['DOWN']+=d;before=self.totals();payments=[];births=[]
  u=self._pay('DOWN',u,t,'UP',payments);d=self._pay('UP',d,t,'DOWN',payments)
  pair=min(u,d);u-=pair;d-=pair;self.direct_pair_qty+=pair
  self._birth('UP',u,t,births);self._birth('DOWN',d,t,births)
  if payments and births:self.same_clock_repair_then_birth+=1
  ev={'t':int(t),'fill_up':float(up_fill),'fill_down':float(down_fill),'payments':payments,'direct_pair_qty':pair,'births':births,'outstanding_before':before,'outstanding_after':self.totals(),'fill_rows':fill_rows or []};self.events.append(ev);return ev
 def summary(self):
  out=self.totals();birth=sum(self.born_qty.values());repaired=sum(self.repaired_qty.values());fill=sum(self.total_fill.values());allocated=repaired+2*self.direct_pair_qty+birth;multi=sum(int(r['payment_clocks']>=2) for r in self.completed_rows);dur=sorted(int(r['completed_t'])-int(r['born_t']) for r in self.completed_rows)
  return {'births':self.births,'completed':self.completed,'outstanding':out,'born_qty':dict(self.born_qty),'repaired_qty':dict(self.repaired_qty),'direct_pair_qty':self.direct_pair_qty,'total_fill':dict(self.total_fill),'stacking_births':self.stacking_births,'same_clock_repair_then_birth':self.same_clock_repair_then_birth,'max_queue':dict(self.max_queue),'completed_multi_payment':multi,'completed_multi_payment_rate':multi/max(1,len(self.completed_rows)),'completion_duration_ms_median':(dur[len(dur)//2] if dur else None),'responsibility_conservation_error':birth-repaired-sum(out.values()),'fill_allocation_error':fill-allocated,'pass':abs(birth-repaired-sum(out.values()))<1e-7 and abs(fill-allocated)<1e-7 and all(v>=-EPS_ATOMIC for v in out.values())}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market',type=int,required=True);ap.add_argument('--route-mode',choices=ROUTES,required=True);ap.add_argument('--rearm-mode',choices=('TERMINAL','ALL','RESPONSIBILITY_OR_TERMINAL','RESPONSIBILITY_TERMINAL_CAPACITY','RESPONSIBILITY_DUAL_CAPACITY'),default='ALL');ap.add_argument('--exact-active-source',default='');ap.add_argument('--exact-active-time',type=int,default=-1);ap.add_argument('--exact-frontier-time',type=int,default=-1);ap.add_argument('--exact-frontier-side',choices=('UP','DOWN'),default=None);ap.add_argument('--exact-frontier-kind',choices=('REPAIR','FRESH'),default=None);ap.add_argument('--exact-frontier-route',choices=('PASSIVE','ACTIVE'),default='PASSIVE');ap.add_argument('--active-admission',choices=('ANY_RESIDUAL','ZERO_FILL_SOURCE','PARTIAL_SOURCE','REPAIR_ONLY','EXPAND_ONLY','PARTIAL_REPAIR','PARTIAL_EXPAND','PARTIAL_UNFILLED_DOMINANT','PARTIAL_FILLED_DOMINANT','PARTIAL_BELOW_PRIOR_PASSIVE_RATE','PARTIAL_ABOVE_PRIOR_PASSIVE_RATE'),default='ANY_RESIDUAL');a=ap.parse_args()
 assert adaptive_rate_accept('PARTIAL_BELOW_PRIOR_PASSIVE_RATE',4.,10.,.6)[0]
 assert not adaptive_rate_accept('PARTIAL_ABOVE_PRIOR_PASSIVE_RATE',4.,10.,.6)[0]
 assert adaptive_rate_accept('PARTIAL_ABOVE_PRIOR_PASSIVE_RATE',8.,10.,.6)[0]
 assert not adaptive_rate_accept('PARTIAL_BELOW_PRIOR_PASSIVE_RATE',8.,10.,.6)[0]
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);started=time.monotonic();sim=None
 result=dict(version='TARGET_CORE_CYCLE_FRONTIER_ROUTE_FORK_V21',status='RUNNING',market_id=a.market,route_mode=a.route_mode,
  purpose='EXACT_SAME_FRONTIER_PASSIVE_VS_ACTIVE_ROUTE_VALUE_CONSUMED8_LINEAGE_STALL_TEST',target_runtime_access=False,target_scoring_only=True,
  dream_fill=False,funding_mode='VIRTUAL_NONBINDING_RESEARCH',capital_cap=None,clock='SOURCE_EVENT',progression='FORWARD',
  own_exposure_feedback=True,residual='PERSIST',rearm=a.rearm_mode,objective='JOINT',market_direction=False,
  active_trigger='NEW_PASSIVE_TERMINAL_WITH_UNFILLED_SOURCE_AND_SAME_SIDE_PERSISTENT_RESIDUAL',
  active_timing_seconds=None,active_threshold=None,active_admission=a.active_admission,active_quantity_rule='MIN_SOURCE_UNFILLED_AND_CURRENT_PERSISTENT_RESIDUAL',
  coefficient_tuning=False,exact_active_source=a.exact_active_source,exact_active_time=a.exact_active_time,live_changes=0,worker=os.environ.get('COMPUTERNAME'))
 try:
  assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
  assert ADAPTER_STAGED.exists()
  helper=load_module('active_v8_helper',HELPER_PATH);job=out.name;root=Path(r'C:/BTC5M-worker/.tmp')/('target_core_cycle_active_v8_'+job)
  if root.exists():shutil.rmtree(root)
  shutil.copytree(TEMPLATE_ROOT,root);assert sha(root/'tools/minimal_student_open_funding_v1.py')==OPEN_POLICY_SHA;assert sha(root/'tools/open_funding_recovery_runtime_v3.py')==RECOVERY_SHA
  shutil.copy2(ADAPTER_STAGED,root/'tools/open_funding_native_active_adapter_v1.py')
  old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')/'MANIFEST.json').read_text(encoding='utf-8'))
  transfer=json.loads((TRANSFER_BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
  assert tuple(int(x) for x in transfer['markets'])==TRANSFER_MIDS
  if a.market in TRANSFER_MIDS:
   run_bundle=TRANSFER_BUNDLE;run_window=transfer['marketWindows'][str(a.market)];run_window_sha=transfer['marketWindowSourceSha256']
   tpfile=TRANSFER_BUNDLE/'tapes'/f'{a.market}.json.xz';assert tpfile.exists();(root/'tapes').mkdir(parents=True,exist_ok=True);shutil.copy2(tpfile,root/'tapes'/f'{a.market}.json.xz')
  elif a.market in ORIGINAL_MIDS:
   run_bundle=TRAIN_BUNDLE;run_window=old['marketWindows'][str(a.market)];run_window_sha=old['marketWindowSourceSha256']
  else:raise RuntimeError('market outside fixed consumed8')
  sys.path.insert(0,str(root));os.chdir(root);binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
  sys.path.insert(0,str(BACKEND));import hftbacktest as h;assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
  from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
  from tools.hft244_minimal_pair_accounting_v1 import install
  from tools.minimal_student_quantity_seam_v1 import make_student_class
  from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
  from tools.minimal_student_open_funding_v1 import OpenFundingProfile,initial_parameters,training_reference,path_loss
  from tools.minimal_student_joint_policy_train_v1 import stable_features,softplus
  from tools.pair_core_economic_grant_ledger_v1 import Grant
  from tools.pair_core_asset_route_sizing_v2 import validate_size
  from tools.minimal_student_training_world_v2 import envelope,passive_ask,training_frame_id
  from tools.open_funding_native_active_adapter_v1 import make_mixed_training_class
  install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
  def strict_send(*args,**kw):
   rc=oldsend(*args,**kw)
   if int(rc)!=0:raise RuntimeError('passive native submit rejected/uncertain:'+str(rc))
   return rc
  base.ex.submit_native=strict_send;Student=make_mixed_training_class(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim),base);profile=OpenFundingProfile(max_live_owners=4096)
  train_sources={m:load_source_from(TRAIN_BUNDLE,m,old['marketWindows'][str(m)]) for m in (2022527,2022538)};qref=training_reference(train_sources);theta=helper.theta_for_mask(initial_parameters(qref),10)
  source=load_source_from(run_bundle,a.market,run_window);start,end=run_window;tp=helper.target_profile(source,start,end)
  class Policy:
   provenance='ACTIVE_ROUTE_V8_NO_TARGET_RUNTIME_DATA'
   def __init__(self):
    self.theta=theta;self.policy_id='FRONTIER_ROUTE_V21_FROZEN_MANAGER';self.continuation_id=self.policy_id+':COMPLETE_EPISODE';self.calls=0;self.declines={};self.total_frames=1
    self.prev_terminal_keys=set();self.initial_new_sent=False;self.bidirectional_plans=0;self.multi_new_plans=0;self.new_eligible_frames=0;self.new_suppressed=0
    self.active_source_attempted=set();self.active_births=[];self.active_blocks={};self.exact_trigger_hits=[];self.candidate_checkpoints=[];self.passive_births=0;self.source_terminal_events=0;self.parallel_same_side_births=0;self.max_epoch_residual_overfill=0.0;self.passive_hist_requested=0.0;self.passive_hist_filled=0.0;self.passive_hist_keys=set();self.atomic=AtomicResponsibilityLedger();self.fill_seen={};self.last_atomic_event=None;self.atomic_transition_wakes=0;self.repair_capacity_frontier_wakes=0;self.repair_capacity_frontier_qty=0.0;self.repair_capacity_frontier_rows=[];self.fresh_capacity_frontier_wakes=0;self.fresh_capacity_frontier_qty=0.0;self.fresh_capacity_frontier_rows=[];self.frontier_exact_hits=[];self.exact_frontier_key=None;self.exact_frontier_lifecycle=[];self.exact_frontier_first_fill_t=None;self.exact_frontier_terminal_t=None;self.exact_frontier_seen_fill=0.0
   def decline(self,r):self.declines[r]=self.declines.get(r,0)+1
   def block(self,r):self.active_blocks[r]=self.active_blocks.get(r,0)+1
   def role_for(self,side,inv):
    u=float(inv['UP']);d=float(inv['DOWN'])
    if abs(u-d)<=1e-12:return 'NEUTRAL'
    weak='UP' if u<d else 'DOWN';return 'REPAIR' if side==weak else 'EXPAND'
   def produce(self,f):
    self.calls+=1;ops=[];v=f['own_view'];ledger=f['ledger'];live={k:c for k,c in ledger.carriers.items() if c.state!='TERMINAL'}
    if self.exact_frontier_key is not None:
     ec=ledger.carriers.get(self.exact_frontier_key)
     if ec is not None:
      cur=float(ec.filled);self.exact_frontier_lifecycle.append({'t':int(f['t']),'state':ec.state,'filled':cur,'qty':float(ec.qty),'payment':float(ec.payment),'fees':float(ec.fees)})
      if cur>self.exact_frontier_seen_fill+1e-12 and self.exact_frontier_first_fill_t is None:self.exact_frontier_first_fill_t=int(f['t'])
      self.exact_frontier_seen_fill=max(self.exact_frontier_seen_fill,cur)
      if ec.state=='TERMINAL' and self.exact_frontier_terminal_t is None:self.exact_frontier_terminal_t=int(f['t'])
    inc={'UP':0.0,'DOWN':0.0};fill_rows=[];atomic_transition=False
    for fk,fc in ledger.carriers.items():
     cur=float(fc.filled);prev=float(self.fill_seen.get(fk,0.0));di=max(0.0,cur-prev)
     if di>EPS_ATOMIC:
      fs=ledger.grants[fc.parent_id].side;inc[fs]+=di;fill_rows.append({'key':fk,'side':fs,'route':fc.route,'fill_increment':di,'filled_cumulative':cur,'qty':float(fc.qty)})
     self.fill_seen[fk]=cur
    if inc['UP']>EPS_ATOMIC or inc['DOWN']>EPS_ATOMIC:
     self.last_atomic_event=self.atomic.process_batch(f['t'],inc['UP'],inc['DOWN'],fill_rows);atomic_transition=bool(self.last_atomic_event and (self.last_atomic_event.get('payments') or self.last_atomic_event.get('births')))
    term_map={k:c for k,c in ledger.carriers.items() if c.state=='TERMINAL'};new_terminal_keys=set(term_map)-self.prev_terminal_keys;terminal_changed=bool(new_terminal_keys);self.source_terminal_events+=len(new_terminal_keys)
    allow=(not self.initial_new_sent) or terminal_changed or a.rearm_mode=='ALL' or (a.rearm_mode in ('RESPONSIBILITY_OR_TERMINAL','RESPONSIBILITY_TERMINAL_CAPACITY','RESPONSIBILITY_DUAL_CAPACITY') and atomic_transition)
    if f['t']>=f['end']:
     for k,c in live.items():
      if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='ACTUAL_MARKET_END'))
     self.prev_terminal_keys=set(term_map);return envelope(f,self,ops)
    x=stable_features(f)
    if f['t']<f['start'] or x is None:
     self.prev_terminal_keys=set(term_map);return envelope(f,self,[])
    w=self.theta;p=max(0.,min(1.,float(f['index'])/max(1.,self.total_frames-1)));progress=2*p-1
    exposure=math.tanh(w[3]*x['own_net']);up=(1.+exposure)/2.;share={'UP':up,'DOWN':1.-up};gross=math.exp(w[5]+w[6]*progress)
    desired={s:gross*share[s] for s in ('UP','DOWN')};pid={'UP':1,'DOWN':2};tick=f['world_profile']['tick'];step=f['world_profile']['quantity_step'];prices={};tickets={};deficit={};owned_map={}
    for s in pid:
     acc=ledger.account(pid[s]);owned=float(v['inv'][s])+acc['reserved_qty'];owned_map[s]=owned;deficit[s]=max(0.,desired[s]-owned)
     bid=x['up_bid'] if s=='UP' else round(1.-x['up_ask'],10);prices[s]=round(math.floor((bid-softplus(w[9])*tick+1e-10)/tick)*tick,10);tickets[s]=math.exp(w[7])
    if self.last_atomic_event is not None and int(self.last_atomic_event['t'])==int(f['t']) and 'manager_state' not in self.last_atomic_event:
     ao=self.atomic.totals();self.last_atomic_event['manager_state']={'desired':dict(desired),'deficit':dict(deficit),'inv':dict(v['inv']),'cost':float(v['cost']),'repair_need':{'UP':ao['DOWN'],'DOWN':ao['UP']},'responsibility_outstanding':ao,'progress':p}
    for ck in sorted(new_terminal_keys):
     sc=term_map[ck]
     if sc.route!='PASSIVE':continue
     ss=ledger.grants[sc.parent_id].side;unf=max(0.,float(sc.qty)-float(sc.filled));res=float(deficit[ss])
     if unf<=1e-12 or res<=1e-12:continue
     fid=training_frame_id(f);gid=f['gateway_state_id'];dg=hashlib.sha256((fid+'|'+gid+'|'+ck).encode()).hexdigest()
     opp='DOWN' if ss=='UP' else 'UP';active_ask=((f.get('quotes') or {}).get(ss) or {}).get('ask');opp_passive_price=prices.get(opp);pair_sum=(float(active_ask)+float(opp_passive_price)) if active_ask is not None and opp_passive_price is not None else None;item=dict(t=int(f['t']),index=int(f['index']),source_key=ck,side=ss,source_qty=float(sc.qty),source_filled=float(sc.filled),source_unfilled=unf,source_fill_fraction=float(sc.filled)/max(float(sc.qty),1e-12),residual=res,role=self.role_for(ss,v['inv']),frame_id=fid,gateway_state_id=gid,prefix_digest=dg,trigger_inventory=dict(v['inv']),trigger_cost=float(v['cost']),progress=p,desired=dict(desired),deficit=dict(deficit),owned_map=dict(owned_map),passive_prices=dict(prices),ticket=dict(tickets),active_ask=active_ask,opposite_side=opp,opposite_passive_price=opp_passive_price,prospective_pair_sum=pair_sum,prospective_pair_edge=(1.-pair_sum if pair_sum is not None else None),mid=float(x['mid']),depth_imbalance=float(x['depth_imbalance']),own_net=float(x['own_net']),own_gross=float(x['own_gross']),up_bid=float(x['up_bid']),up_ask=float(x['up_ask']),pending_count=len(live),wall_phase=max(0.,min(1.,(int(f['t'])-int(f['start']))/max(1,int(f['end'])-int(f['start'])))))
     self.candidate_checkpoints.append(item)
     if ck==a.exact_active_source and int(f['t'])==int(a.exact_active_time):self.exact_trigger_hits.append(dict(item))
    draft=deepcopy(ledger)
    for k,c in live.items():
     s=ledger.grants[c.parent_id].side;stale=abs(c.limit-prices[s])>tick*(1.+softplus(w[11]))+1e-9;surplus=(desired[s]-owned_map[s])<-tickets[s]*(1/(1+math.exp(-w[11])))
     if (stale or surplus) and c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='V8_MAINTENANCE'));draft.request_cancel(k)
    if a.rearm_mode in ('RESPONSIBILITY_OR_TERMINAL','RESPONSIBILITY_TERMINAL_CAPACITY','RESPONSIBILITY_DUAL_CAPACITY') and atomic_transition:self.atomic_transition_wakes+=1
    if not allow and a.rearm_mode=='RESPONSIBILITY_TERMINAL_CAPACITY':
     ao=self.atomic.totals();repair_need={'UP':float(ao['DOWN']),'DOWN':float(ao['UP'])};live_unfilled={'UP':0.0,'DOWN':0.0}
     for lk,lc in live.items():
      ls=ledger.grants[lc.parent_id].side;live_unfilled[ls]+=max(0.0,float(lc.qty)-float(lc.filled))
     repair_gap={ss:max(0.0,repair_need[ss]-live_unfilled[ss]) for ss in ('UP','DOWN')};slots=f['world_profile']['max_live_owners']-len(live);frontier_new=[];n=v['n'];mids={'UP':x['mid'],'DOWN':1.-x['mid']}
     for ss in sorted(('UP','DOWN'),key=lambda z:(-repair_gap[z]*mids[z],z)):
      if slots<=0:break
      raw=min(float(tickets[ss]),float(deficit[ss]),float(repair_gap[ss]))
      if raw<=1e-12:continue
      price=prices[ss];ask=passive_ask(f['book'],ss)
      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue
      qty=round(math.floor((raw+1e-10)/step)*step,8)
      if qty<=0:continue
      try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)
      except ValueError:continue
      key=f'{ss}_{n}'
      try:draft.reserve(key,pid[ss],'PASSIVE',qty,price,0.,now_ms=f['t'],market_end_ms=f['end'])
      except ValueError:continue
      frontier_new.append(dict(kind='NEW',key=key,parent_id=pid[ss],side=ss,route='PASSIVE',price=price,qty=qty,role='PASSIVE_REPAIR_CAPACITY_FRONTIER'));self.passive_births+=1;n+=1;slots-=1
      self.repair_capacity_frontier_wakes+=1;self.repair_capacity_frontier_qty+=qty;self.repair_capacity_frontier_rows.append({'t':int(f['t']),'side':ss,'qty':qty,'price':price,'atomic_repair_need':repair_need[ss],'live_unfilled_before':live_unfilled[ss],'repair_gap':repair_gap[ss],'controller_deficit':float(deficit[ss]),'progress':p})
     if frontier_new:
      self.initial_new_sent=True;self.new_eligible_frames+=1;self.multi_new_plans+=len(frontier_new)>1;self.bidirectional_plans+=len({o['side'] for o in frontier_new})>1;self.prev_terminal_keys=set(term_map);return envelope(f,self,ops+frontier_new)
    if not allow and a.rearm_mode=='RESPONSIBILITY_DUAL_CAPACITY':
     ao=self.atomic.totals();repair_need={'UP':float(ao['DOWN']),'DOWN':float(ao['UP'])};live_unfilled={'UP':0.0,'DOWN':0.0}
     for lk,lc in live.items():
      ls=ledger.grants[lc.parent_id].side;live_unfilled[ls]+=max(0.0,float(lc.qty)-float(lc.filled))
     repair_gap={};fresh_desire={};fresh_gap={}
     for ss in ('UP','DOWN'):
      need=repair_need[ss];lv=live_unfilled[ss];df=float(deficit[ss]);repair_gap[ss]=max(0.0,need-lv);fresh_desire[ss]=max(0.0,df-need);live_after_repair=max(0.0,lv-need);fresh_gap[ss]=max(0.0,fresh_desire[ss]-live_after_repair)
     slots=f['world_profile']['max_live_owners']-len(live);frontier_new=[];n=v['n'];mids={'UP':x['mid'],'DOWN':1.-x['mid']}
     for ss in sorted(('UP','DOWN'),key=lambda z:(-(repair_gap[z]+fresh_gap[z])*mids[z],z)):
      if slots<=0:break
      kind=None;raw=0.0
      if repair_gap[ss]>1e-12:
       raw=min(float(tickets[ss]),float(deficit[ss]),float(repair_gap[ss]));kind='REPAIR'
      elif fresh_gap[ss]>1e-12:
       raw=min(float(tickets[ss]),float(fresh_gap[ss]));kind='FRESH'
      if raw<=1e-12:continue
      price=prices[ss];ask=passive_ask(f['book'],ss)
      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue
      qty=round(math.floor((raw+1e-10)/step)*step,8)
      if qty<=0:continue
      try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)
      except ValueError:continue
      exact_match=(a.exact_frontier_time>=0 and int(f['t'])==int(a.exact_frontier_time) and ss==a.exact_frontier_side and kind==a.exact_frontier_kind)
      exec_route='PASSIVE';exec_price=float(price)
      if exact_match:
       aq=((f.get('quotes') or {}).get(ss) or {}).get('ask')
       if aq is None or not 0<float(aq)<1:raise RuntimeError('EXACT_FRONTIER_NO_ACTIVE_ASK')
       validate_size(f['world_profile']['asset'],'ACTIVE',float(aq),qty,quantity_step=step)
       fid=training_frame_id(f);gid=f['gateway_state_id'];self.frontier_exact_hits.append({'t':int(f['t']),'index':int(f['index']),'side':ss,'kind':kind,'qty':qty,'passive_price':float(price),'active_ask':float(aq),'frame_id':fid,'gateway_state_id':gid,'prefix_digest':hashlib.sha256((fid+'|'+gid+'|'+ss+'|'+kind).encode()).hexdigest(),'atomic_repair_need':repair_need[ss],'repair_gap':repair_gap[ss],'fresh_desire':fresh_desire[ss],'fresh_gap':fresh_gap[ss],'live_unfilled_before':live_unfilled[ss],'controller_deficit':float(deficit[ss]),'inv':dict(v['inv']),'cost':float(v['cost']),'progress':p})
       if a.exact_frontier_route=='ACTIVE':exec_route='ACTIVE';exec_price=float(aq)
      key=f'{ss}_{n}'
      if exact_match:self.exact_frontier_key=key;self.frontier_exact_hits[-1]['child_key']=key;self.frontier_exact_hits[-1]['exec_route']=exec_route;self.frontier_exact_hits[-1]['exec_price']=exec_price
      try:draft.reserve(key,pid[ss],exec_route,qty,exec_price,0.,now_ms=f['t'],market_end_ms=f['end'])
      except ValueError as ex:
       if exact_match:raise RuntimeError('EXACT_FRONTIER_RESERVE:'+str(ex))
       continue
      role=(exec_route+'_REPAIR_CAPACITY_FRONTIER' if kind=='REPAIR' else exec_route+'_FRESH_CAPACITY_FRONTIER');frontier_new.append(dict(kind='NEW',key=key,parent_id=pid[ss],side=ss,route=exec_route,price=exec_price,qty=qty,role=role));self.passive_births+=int(exec_route=='PASSIVE');n+=1;slots-=1
      row={'t':int(f['t']),'side':ss,'qty':qty,'price':price,'kind':kind,'atomic_repair_need':repair_need[ss],'live_unfilled_before':live_unfilled[ss],'repair_gap':repair_gap[ss],'fresh_desire':fresh_desire[ss],'fresh_gap':fresh_gap[ss],'controller_deficit':float(deficit[ss]),'progress':p}
      if kind=='REPAIR':self.repair_capacity_frontier_wakes+=1;self.repair_capacity_frontier_qty+=qty;self.repair_capacity_frontier_rows.append(row)
      else:self.fresh_capacity_frontier_wakes+=1;self.fresh_capacity_frontier_qty+=qty;self.fresh_capacity_frontier_rows.append(row)
     if frontier_new:
      self.initial_new_sent=True;self.new_eligible_frames+=1;self.multi_new_plans+=len(frontier_new)>1;self.bidirectional_plans+=len({o['side'] for o in frontier_new})>1;self.prev_terminal_keys=set(term_map);return envelope(f,self,ops+frontier_new)
    if not allow:
     self.new_suppressed+=sum(deficit[s]>0 for s in pid);self.prev_terminal_keys=set(term_map);return envelope(f,self,ops)
    # Optional one ACTIVE child for a newly terminal PASSIVE source. ACTIVE sources never chain to ACTIVE.
    active_side=None;active_op=None
    if a.route_mode in ('PASSIVE_TERMINAL_ACTIVE_RESIDUAL','PARALLEL_ACTIVE_PASSIVE_RESIDUAL') and new_terminal_keys:
     candidates=[]
     prior_passive_rate=(self.passive_hist_filled/self.passive_hist_requested) if self.passive_hist_requested>1e-12 else None
     history_updates=[]
     for key in sorted(new_terminal_keys):
      c=term_map[key]
      if c.route!='PASSIVE' or key in self.active_source_attempted:continue
      if not a.exact_active_source or key!=a.exact_active_source or int(f['t'])!=int(a.exact_active_time):continue
      history_updates.append((key,c))
      self.active_source_attempted.add(key);side=ledger.grants[c.parent_id].side;unfilled=max(0.,float(c.qty)-float(c.filled));res=float(deficit[side]);qty=min(unfilled,res);role=self.role_for(side,v['inv'])
      if qty<=1e-12:
       self.block('NO_SOURCE_UNFILLED_OR_RESIDUAL');continue
      if a.active_admission=='ZERO_FILL_SOURCE' and float(c.filled)>1e-12:self.block('ADMISSION_NOT_ZERO_FILL_SOURCE');continue
      if a.active_admission=='PARTIAL_SOURCE' and not (float(c.filled)>1e-12 and unfilled>1e-12):self.block('ADMISSION_NOT_PARTIAL_SOURCE');continue
      if a.active_admission=='REPAIR_ONLY' and role!='REPAIR':self.block('ADMISSION_NOT_REPAIR_ROLE');continue
      if a.active_admission=='EXPAND_ONLY' and role!='EXPAND':self.block('ADMISSION_NOT_EXPAND_ROLE');continue
      if a.active_admission in ('PARTIAL_REPAIR','PARTIAL_EXPAND'):
       if not (float(c.filled)>1e-12 and unfilled>1e-12):self.block('ADMISSION_NOT_PARTIAL_SOURCE');continue
       want='REPAIR' if a.active_admission=='PARTIAL_REPAIR' else 'EXPAND'
       if role!=want:self.block('ADMISSION_NOT_'+want+'_ROLE');continue
      if a.active_admission in ('PARTIAL_UNFILLED_DOMINANT','PARTIAL_FILLED_DOMINANT'):
       if not (float(c.filled)>1e-12 and unfilled>1e-12):self.block('ADMISSION_NOT_PARTIAL_SOURCE');continue
       unfilled_dominant=unfilled>float(c.filled)
       if a.active_admission=='PARTIAL_UNFILLED_DOMINANT' and not unfilled_dominant:self.block('ADMISSION_FILLED_DOMINANT');continue
       if a.active_admission=='PARTIAL_FILLED_DOMINANT' and unfilled_dominant:self.block('ADMISSION_UNFILLED_DOMINANT');continue
      if a.active_admission in ('PARTIAL_BELOW_PRIOR_PASSIVE_RATE','PARTIAL_ABOVE_PRIOR_PASSIVE_RATE'):
       accept,reason=adaptive_rate_accept(a.active_admission,float(c.filled),float(c.qty),prior_passive_rate)
       if not accept:self.block(reason);continue
      mid=x['mid'] if side=='UP' else 1-x['mid'];candidates.append((qty*mid,key,side,c,qty,unfilled,res,role))
     for hk,hc in history_updates:
      if hk not in self.passive_hist_keys:
       self.passive_hist_keys.add(hk);self.passive_hist_requested+=float(hc.qty);self.passive_hist_filled+=float(hc.filled)
     for _,source_key,side,c,raw_qty,unfilled,res,role in sorted(candidates,reverse=True):
      q=((f.get('quotes') or {}).get(side) or {});ask=q.get('ask')
      if ask is None or not 0<float(ask)<1:
       self.block('NO_CURRENT_ACTIVE_ASK');continue
      qty=round(math.floor((raw_qty+1e-10)/step)*step,8)
      if qty<=0:
       self.block('ACTIVE_QTY_BELOW_STEP_AFTER_FLOOR');continue
      try:validate_size(f['world_profile']['asset'],'ACTIVE',float(ask),qty,quantity_step=step)
      except ValueError as ex:self.block('ACTIVE_SIZE:'+str(ex));continue
      key=f'{side}_{v["n"]}'
      try:draft.reserve(key,pid[side],'ACTIVE',qty,float(ask),0.,now_ms=f['t'],market_end_ms=f['end'])
      except ValueError as ex:self.block('ACTIVE_RESERVE:'+str(ex));continue
      active_op=dict(kind='NEW',key=key,parent_id=pid[side],side=side,route='ACTIVE',price=float(ask),qty=qty,role='ACTIVE_RESIDUAL_'+role,source_terminal_key=source_key,source_unfilled=unfilled,residual_before=res);active_side=side
      self.active_births.append(dict(t=int(f['t']),source_terminal_key=source_key,child_key=key,side=side,qty=qty,price=float(ask),source_unfilled=unfilled,residual_before=res,source_filled=float(c.filled),source_qty=float(c.qty),role=role,strict_past_passive_fill_rate=prior_passive_rate));break
    new=[];slots=f['world_profile']['max_live_owners']-len(live);n=v['n']
    if active_op is not None:
     new.append(active_op);n+=1;slots-=1
    mid={'UP':x['mid'],'DOWN':1.-x['mid']}
    for s in sorted(pid,key=lambda z:(-deficit[z]*mid[z],z)):
     if s==active_side and a.route_mode=='PASSIVE_TERMINAL_ACTIVE_RESIDUAL':continue
     # In PARALLEL mode the Active reservation already occupies part of this
     # same persistent residual in draft. Passive may use only the remainder.
     if a.route_mode=='PARALLEL_ACTIVE_PASSIVE_RESIDUAL' and active_op is not None:
      da=draft.account(pid[s]);service_deficit=max(0.,desired[s]-float(v['inv'][s])-float(da['reserved_qty']))
     else:service_deficit=deficit[s]
     if service_deficit<=0:continue
     if slots<=0:self.decline('RESOURCE_CENSOR');break
     price=prices[s];ask=passive_ask(f['book'],s)
     if not tick<=price<1 or ask is None or price>=ask-1e-10:continue
     qty=max(0.,min(tickets[s],service_deficit));qty=round(math.floor((qty+1e-10)/step)*step,8)
     try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)
     except ValueError:self.decline('POLICY_INCREMENT_BELOW_VENUE_MINIMUM');continue
     key=f'{s}_{n}'
     try:draft.reserve(key,pid[s],'PASSIVE',qty,price,0.,now_ms=f['t'],market_end_ms=f['end'])
     except ValueError as ex:self.decline('PASSIVE_FEASIBILITY:'+str(ex));continue
     role=self.role_for(s,v['inv']);new.append(dict(kind='NEW',key=key,parent_id=pid[s],side=s,route='PASSIVE',price=price,qty=qty,role='PASSIVE_'+role));self.passive_births+=1;n+=1;slots-=1
    if active_op is None and a.route_mode in ('PASSIVE_TERMINAL_ACTIVE_RESIDUAL','PARALLEL_ACTIVE_PASSIVE_RESIDUAL') and new_terminal_keys:
     # No Active materialization was feasible; ordinary persistent passive continuation is allowed on all sides.
     existing={o['side'] for o in new if o['kind']=='NEW'}
     for s in sorted(pid,key=lambda z:(-deficit[z]*mid[z],z)):
      if s in existing or deficit[s]<=0 or slots<=0:continue
      price=prices[s];ask=passive_ask(f['book'],s)
      if not tick<=price<1 or ask is None or price>=ask-1e-10:continue
      qty=max(0.,min(tickets[s],deficit[s]));qty=round(math.floor((qty+1e-10)/step)*step,8)
      try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)
      except ValueError:continue
      key=f'{s}_{n}'
      try:draft.reserve(key,pid[s],'PASSIVE',qty,price,0.,now_ms=f['t'],market_end_ms=f['end'])
      except ValueError:continue
      role=self.role_for(s,v['inv']);new.append(dict(kind='NEW',key=key,parent_id=pid[s],side=s,route='PASSIVE',price=price,qty=qty,role='PASSIVE_'+role));self.passive_births+=1;n+=1;slots-=1
    actual_new=[o for o in new if o['kind']=='NEW']
    if actual_new:
     byside={s:sum(float(o['qty']) for o in actual_new if o['side']==s) for s in pid}
     self.max_epoch_residual_overfill=max(self.max_epoch_residual_overfill,max(max(0.,byside[s]-deficit[s]) for s in pid))
     if active_op is not None and a.route_mode=='PARALLEL_ACTIVE_PASSIVE_RESIDUAL' and any(o['side']==active_side and o.get('route')=='PASSIVE' for o in actual_new):self.parallel_same_side_births+=1
     self.initial_new_sent=True;self.new_eligible_frames+=1;self.multi_new_plans+=len(actual_new)>1;self.bidirectional_plans+=len({o['side'] for o in actual_new})>1
    self.prev_terminal_keys=set(term_map);return envelope(f,self,ops+new)
  ledger=FastOpenFundingLedger(profile)
  for pid,side in ((1,'UP'),(2,'DOWN')):ledger.issue(Grant(pid,'ACTIVE_ROUTE_V8',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING'))
  producer=Policy();tr=helper.TraceLite();sim=Student(root/f'tapes/{a.market}.json.xz',profile.max_live_owners,False,producer=producer,ledger=ledger,trace=tr,verified_window=[start,end],window_source_sha256=run_window_sha);producer.total_frames=max(1,len(sim.payload['updates']));sim.bt=helper.AuditBT(sim.bt,tr)
  sim.run_whole(base);sim.drain_queued_responses(base);actual=sim.gateway.ledger
  finc={'UP':0.0,'DOWN':0.0};frows=[]
  for fk,fc in actual.carriers.items():
   cur=float(fc.filled);prev=float(producer.fill_seen.get(fk,0.0));di=max(0.0,cur-prev)
   if di>EPS_ATOMIC:
    fs=actual.grants[fc.parent_id].side;finc[fs]+=di;frows.append({'key':fk,'side':fs,'route':fc.route,'fill_increment':di,'filled_cumulative':cur,'qty':float(fc.qty)});producer.fill_seen[fk]=cur
  if finc['UP']>EPS_ATOMIC or finc['DOWN']>EPS_ATOMIC:producer.atomic.process_batch(end,finc['UP'],finc['DOWN'],frows)
  actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
  if sim._receipt_invalid:raise RuntimeError('receipt invalid')
  unresolved=sum(c.state!='TERMINAL' for c in actual.carriers.values())
  if unresolved:raise RuntimeError('unresolved owners after native drain:'+str(unresolved))
  pending=sum(actual.account(pid)['reserved_cash'] for pid in actual.grants);terminal=dict(inv=dict(sim.inv),cost=float(sim.cost),pending_cash=float(pending));pl=path_loss(tr.states,source,terminal,qref);op=helper.own_profile(tr.states,start,end);score=helper.structural_score(pl['path_mse'],tp,op);owners=list(actual.carriers.values())
  active=[c for c in owners if c.route=='ACTIVE'];passive=[c for c in owners if c.route=='PASSIVE']
  result.update(status='COMPLETE',fixed_train_share_unit=qref,theta=theta,target_profile=tp,our_profile=op,path_mse=pl['path_mse'],coordinate_mse=pl['coordinate_mse'],**score,source_frames=producer.total_frames,
   submits=int(sim.submits),passive_native_submits=int(sim.passive_native_submits),active_native_submits=int(sim.active_native_submits),native_receipts=len(sim._receipt_ledger.seen),economic_filled_orders=sum(c.filled>0 for c in owners),zero_fill_orders=sum(c.filled==0 for c in owners),cancel_requests=sum(x['kind']=='CANCEL' for x in tr.actions),receipt_conditioned_continuations=int(sim.post_receipt_plans),new_eligible_frames=producer.new_eligible_frames,new_suppressed=producer.new_suppressed,
   passive_carriers=len(passive),passive_filled_orders=sum(c.filled>0 for c in passive),passive_fill_qty=sum(float(c.filled) for c in passive),active_carriers=len(active),active_filled_orders=sum(c.filled>0 for c in active),active_zero_fill_orders=sum(c.filled<=1e-12 for c in active),active_partial_orders=sum(1 for c in active if 1e-12<c.filled<c.qty-1e-9),active_full_orders=sum(c.filled>=c.qty-1e-9 for c in active),active_requested_qty=sum(float(c.qty) for c in active),active_fill_qty=sum(float(c.filled) for c in active),
   active_birth_count=len(producer.active_births),active_births=producer.active_births[:200],exact_trigger_hits=producer.exact_trigger_hits,candidate_checkpoint_count=len(producer.candidate_checkpoints),candidate_checkpoints=producer.candidate_checkpoints[:1000],active_blocks=producer.active_blocks,active_source_attempts=len(producer.active_source_attempted),passive_births=producer.passive_births,source_terminal_events=producer.source_terminal_events,passive_history_requested=producer.passive_hist_requested,passive_history_filled=producer.passive_hist_filled,parallel_same_side_births=producer.parallel_same_side_births,max_epoch_residual_overfill=producer.max_epoch_residual_overfill,
   active_responsibility_overfill=sum(max(0.,b['qty']-min(b['source_unfilled'],b['residual_before'])) for b in producer.active_births),
   atomic_responsibility_summary=producer.atomic.summary(),atomic_responsibility_events=producer.atomic.events[:5000],atomic_transition_wakes=int(producer.atomic_transition_wakes),
   repair_capacity_frontier_wakes=int(producer.repair_capacity_frontier_wakes),repair_capacity_frontier_qty=float(producer.repair_capacity_frontier_qty),repair_capacity_frontier_rows=producer.repair_capacity_frontier_rows[:5000],
   fresh_capacity_frontier_wakes=int(producer.fresh_capacity_frontier_wakes),fresh_capacity_frontier_qty=float(producer.fresh_capacity_frontier_qty),fresh_capacity_frontier_rows=producer.fresh_capacity_frontier_rows[:5000],frontier_exact_hits=producer.frontier_exact_hits,exact_frontier_route=a.exact_frontier_route,exact_frontier_time=a.exact_frontier_time,exact_frontier_side=a.exact_frontier_side,exact_frontier_kind=a.exact_frontier_kind,
   final_inventory=dict(sim.inv),final_cost=float(sim.cost),terminal_worst=min(sim.inv.values())-float(sim.cost),peak_current_cash_requirement=actual.funding_demand()['current_cash_requirement'],unresolved_owners=unresolved,
   execution_accounting_valid=abs(sum(c.payment+c.fees for c in owners)-sim.cost)<1e-7 and all(abs(sum(c.filled for c in owners if actual.grants[c.parent_id].side==s)-sim.inv[s])<1e-7 for s in ('UP','DOWN')))
  if producer.exact_frontier_key is not None:
   ec=actual.carriers.get(producer.exact_frontier_key);rr=[dict(r) for r in sim._receipt_delta_rows if r.get('key')==producer.exact_frontier_key];hit0=(producer.frontier_exact_hits[0] if producer.frontier_exact_hits else None)
   result['exact_frontier_carrier']=dict(key=producer.exact_frontier_key,route=(ec.route if ec is not None else None),qty=(float(ec.qty) if ec is not None else None),filled=(float(ec.filled) if ec is not None else None),fill_fraction=(float(ec.filled)/max(float(ec.qty),1e-12) if ec is not None else None),payment=(float(ec.payment) if ec is not None else None),fees=(float(ec.fees) if ec is not None else None),state=(ec.state if ec is not None else None),first_fill_t=producer.exact_frontier_first_fill_t,terminal_t=producer.exact_frontier_terminal_t,first_fill_latency_ms=((producer.exact_frontier_first_fill_t-int(hit0['t'])) if producer.exact_frontier_first_fill_t is not None and hit0 is not None else None),terminal_latency_ms=((producer.exact_frontier_terminal_t-int(hit0['t'])) if producer.exact_frontier_terminal_t is not None and hit0 is not None else None),canonical_receipts=rr,lifecycle=producer.exact_frontier_lifecycle[:500])
  if len(producer.exact_trigger_hits)==1:result['post_fork_metrics']=post_fork_metrics(tr.states,source,qref,producer.exact_trigger_hits[0])
  result['safety_gate']=dict(unresolved_zero=unresolved==0,accounting_valid=result['execution_accounting_valid'],active_responsibility_overfill_zero=result['active_responsibility_overfill']<=1e-9,epoch_residual_overfill_zero=result['max_epoch_residual_overfill']<=1e-9,passive_only_has_no_active=(a.route_mode!='PASSIVE_ONLY' or len(active)==0 or a.exact_frontier_route=='ACTIVE'),atomic_responsibility_conservation=bool(result['atomic_responsibility_summary']['pass']),exact_frontier_seen=(a.exact_frontier_time<0 or len(producer.frontier_exact_hits)==1));result['safety_gate']['pass']=all(result['safety_gate'].values())
 except Exception as ex:
  result['status']='ERROR';result['error']=type(ex).__name__+':'+str(ex);result['traceback']=traceback.format_exc(limit=14)
 finally:
  if sim is not None:
   try:sim.close()
   except Exception:pass
  result['elapsed_seconds']=time.monotonic()-started;(out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
 print(json.dumps({k:result.get(k) for k in ('status','market_id','route_mode','core_similarity','path_mse','passive_native_submits','active_native_submits','active_birth_count','active_fill_qty','terminal_worst','safety_gate','error','elapsed_seconds')},ensure_ascii=False),flush=True)
 if result['status']!='COMPLETE' or not result['safety_gate']['pass']:raise SystemExit(2)
if __name__=='__main__':main()
