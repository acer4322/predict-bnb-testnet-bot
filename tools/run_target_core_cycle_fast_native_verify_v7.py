"""Native-HFT selective verification of Fast Surrogate V1 deltas.

Frozen core (no tuning): SOURCE_EVENT progress, own exposure feedback,
persistent residual, FORWARD desired-gross progression, JOINT objectives.
Only two binary mechanisms vary:
  rearm: TERMINAL vs ALL market frames
  market_direction: OFF vs ON
Target data is scoring-only. Research-only, no live authority.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import gzip,hashlib,importlib.util,json,math,os
from pathlib import Path
import shutil,sys,time,traceback

HELPER_PATH=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/run_target_core_cycle_rearm_v3.py')
BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_recovery_train_20260911_v3')
TEMPLATE_ROOT=Path(r'C:/BTC5M-worker/.tmp/open_funding_recovery_train_20260911_v3')
BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
OPEN_POLICY_SHA='507bd3724ec534e6c9c64a479aa3985f77304f796529bad765780e9e538ca1dd'
RECOVERY_SHA='2468fd631c277e114c3f943bfc9f4867c4f06bdab5f69a26b24d8a8685dd00f1'

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(262144),b''):h.update(b)
 return h.hexdigest()

def load_module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def load_source(mid,old):
 p=BUNDLE/f'input_{mid}.json.gz'
 with gzip.open(p,'rb') as f:data=f.read(8*1024*1024+1)
 if len(data)>8*1024*1024:raise RuntimeError('source too large')
 s=json.loads(data);assert int(s['market']['market_id'])==mid
 assert [s['market']['window_start_ms'],s['market']['window_end_ms']]==old['marketWindows'][str(mid)]
 return s

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market',type=int,required=True);ap.add_argument('--rearm',choices=('TERMINAL','ALL'),required=True);ap.add_argument('--market-direction',type=int,choices=(0,1),required=True);a=ap.parse_args()
 out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True);started=time.monotonic();sim=None
 result=dict(version='TARGET_CORE_CYCLE_FAST_NATIVE_VERIFY_V7',status='RUNNING',market_id=a.market,rearm=a.rearm,market_direction=bool(a.market_direction),
  purpose='NATIVE_VERIFY_FAST_SURROGATE_ARCHITECTURE_DELTA_NO_TUNING',target_runtime_access=False,target_scoring_only=True,dream_fill=False,
  funding_mode='VIRTUAL_NONBINDING_RESEARCH',capital_cap=None,clock='SOURCE_EVENT',residual='PERSIST',progression='FORWARD',objective='JOINT',
  own_exposure_feedback=True,coefficient_tuning=False,live_changes=0,worker=os.environ.get('COMPUTERNAME'))
 try:
  assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
  helper=load_module('v7_helper',HELPER_PATH);job=out.name;root=Path(r'C:/BTC5M-worker/.tmp')/('target_core_cycle_v7_'+job)
  if root.exists():shutil.rmtree(root)
  shutil.copytree(TEMPLATE_ROOT,root);assert sha(root/'tools/minimal_student_open_funding_v1.py')==OPEN_POLICY_SHA;assert sha(root/'tools/open_funding_recovery_runtime_v3.py')==RECOVERY_SHA
  old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')/'MANIFEST.json').read_text(encoding='utf-8'))
  sys.path.insert(0,str(root));os.chdir(root);binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
  sys.path.insert(0,str(BACKEND));import hftbacktest as h;assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
  from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
  from tools.hft244_minimal_pair_accounting_v1 import install
  from tools.minimal_student_quantity_seam_v1 import make_student_class
  from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger,make_recovered_student
  from tools.minimal_student_open_funding_v1 import OpenFundingProfile,initial_parameters,training_reference,path_loss
  from tools.minimal_student_joint_policy_train_v1 import stable_features,softplus
  from tools.pair_core_economic_grant_ledger_v1 import Grant
  from tools.pair_core_asset_route_sizing_v2 import validate_size
  from tools.minimal_student_training_world_v2 import envelope,passive_ask
  install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
  def strict_send(*args,**kw):
   rc=oldsend(*args,**kw)
   if int(rc)!=0:raise RuntimeError('native submit rejected/uncertain:'+str(rc))
   return rc
  base.ex.submit_native=strict_send;Student=make_recovered_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim));profile=OpenFundingProfile(max_live_owners=4096)
  train_sources={m:load_source(m,old) for m in (2022527,2022538)};qref=training_reference(train_sources);theta=helper.theta_for_mask(initial_parameters(qref),10)
  # Restore only the frozen public-market directional coefficients when requested.
  initial=initial_parameters(qref)
  if a.market_direction:theta[1]=initial[1];theta[2]=initial[2]
  source=train_sources.get(a.market) or load_source(a.market,old);start,end=old['marketWindows'][str(a.market)];tp=helper.target_profile(source,start,end)
  class Policy:
   provenance='FAST_NATIVE_VERIFY_V7_NO_TARGET_RUNTIME_DATA'
   def __init__(self):
    self.theta=theta;self.policy_id=f'FAST_NATIVE_V7:{a.rearm}:MD{a.market_direction}';self.continuation_id=self.policy_id+':COMPLETE_EPISODE';self.calls=0;self.declines={};self.total_frames=1
    self.prev_terminal=0;self.initial_new_sent=False;self.bidirectional_plans=0;self.multi_new_plans=0;self.new_eligible_frames=0;self.new_suppressed=0
   def decline(self,r):self.declines[r]=self.declines.get(r,0)+1
   def produce(self,f):
    self.calls+=1;ops=[];v=f['own_view'];ledger=f['ledger'];live={k:c for k,c in ledger.carriers.items() if c.state!='TERMINAL'}
    terminals=sum(c.state=='TERMINAL' for c in ledger.carriers.values());terminal_changed=terminals>self.prev_terminal;allow=(not self.initial_new_sent) or a.rearm=='ALL' or terminal_changed;self.prev_terminal=terminals
    if f['t']>=f['end']:
     for k,c in live.items():
      if c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='ACTUAL_MARKET_END'))
     return envelope(f,self,ops)
    x=stable_features(f)
    if f['t']<f['start'] or x is None:return envelope(f,self,[])
    w=self.theta;p=max(0.,min(1.,float(f['index'])/max(1.,self.total_frames-1)));progress=2*p-1
    z=w[3]*x['own_net']
    if a.market_direction:z+=w[1]*(2*x['mid']-1)+w[2]*x['depth_imbalance']
    exposure=math.tanh(z);up=(1.+exposure)/2.;share={'UP':up,'DOWN':1.-up};gross=math.exp(w[5]+w[6]*progress)
    desired={s:gross*share[s] for s in share};pid={'UP':1,'DOWN':2};tick=f['world_profile']['tick'];step=f['world_profile']['quantity_step'];prices={};tickets={};deficit={};owned_map={}
    for s in pid:
     acc=ledger.account(pid[s]);owned=float(v['inv'][s])+acc['reserved_qty'];owned_map[s]=owned;deficit[s]=max(0.,desired[s]-owned)
     bid=x['up_bid'] if s=='UP' else round(1.-x['up_ask'],10);prices[s]=round(math.floor((bid-softplus(w[9])*tick+1e-10)/tick)*tick,10);tickets[s]=math.exp(w[7])
    draft=deepcopy(ledger)
    for k,c in live.items():
     s=ledger.grants[c.parent_id].side;stale=abs(c.limit-prices[s])>tick*(1.+softplus(w[11]))+1e-9;surplus=(desired[s]-owned_map[s])<-tickets[s]*(1/(1+math.exp(-w[11])))
     if (stale or surplus) and c.state!='CANCEL_PENDING' and f['cancellable'].get(k,False):ops.append(dict(kind='CANCEL',key=k,origin='WHOLE_POLICY',reason='V7_MAINTENANCE'));draft.request_cancel(k)
    if not allow:
     self.new_suppressed+=sum(deficit[s]>0 for s in pid);return envelope(f,self,ops)
    slots=f['world_profile']['max_live_owners']-len(live);n=v['n'];new=[];mid={'UP':x['mid'],'DOWN':1.-x['mid']}
    for s in sorted(pid,key=lambda z:(-deficit[z]*mid[z],z)):
     if deficit[s]<=0:continue
     if slots<=0:self.decline('RESOURCE_CENSOR');break
     price=prices[s];ask=passive_ask(f['book'],s)
     if not tick<=price<1 or ask is None or price>=ask-1e-10:continue
     qty=max(0.,min(tickets[s],deficit[s]));qty=round(math.floor((qty+1e-10)/step)*step,8)
     try:validate_size(f['world_profile']['asset'],'PASSIVE',price,qty,quantity_step=step)
     except ValueError:self.decline('POLICY_INCREMENT_BELOW_VENUE_MINIMUM');continue
     key=f'{s}_{n}'
     try:draft.reserve(key,pid[s],'PASSIVE',qty,price,0.,now_ms=f['t'],market_end_ms=f['end'])
     except ValueError as e:self.decline('JOINT_FEASIBILITY:'+str(e));continue
     new.append(dict(kind='NEW',key=key,parent_id=pid[s],side=s,price=price,qty=qty,role='V7_JOINT_CONTINUATION'));n+=1;slots-=1
    if new:self.initial_new_sent=True;self.new_eligible_frames+=1;self.multi_new_plans+=len(new)>1;self.bidirectional_plans+=len({o['side'] for o in new})>1
    return envelope(f,self,ops+new)
  ledger=FastOpenFundingLedger(profile)
  for pid,side in ((1,'UP'),(2,'DOWN')):ledger.issue(Grant(pid,'FAST_NATIVE_V7',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING'))
  producer=Policy();tr=helper.TraceLite();sim=Student(root/f'tapes/{a.market}.json.xz',profile.max_live_owners,False,producer=producer,ledger=ledger,trace=tr,verified_window=[start,end],window_source_sha256=old['marketWindowSourceSha256']);producer.total_frames=max(1,len(sim.payload['updates']));sim.bt=helper.AuditBT(sim.bt,tr)
  sim.run_whole(base);sim.drain_queued_responses(base);actual=sim.gateway.ledger;actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
  if sim._receipt_invalid:raise RuntimeError('receipt invalid')
  unresolved=sum(c.state!='TERMINAL' for c in actual.carriers.values())
  if unresolved:raise RuntimeError('unresolved owners after native drain:'+str(unresolved))
  pending=sum(actual.account(pid)['reserved_cash'] for pid in actual.grants);terminal=dict(inv=dict(sim.inv),cost=float(sim.cost),pending_cash=float(pending));pl=path_loss(tr.states,source,terminal,qref);op=helper.own_profile(tr.states,start,end);score=helper.structural_score(pl['path_mse'],tp,op);owners=list(actual.carriers.values())
  result.update(status='COMPLETE',fixed_train_share_unit=qref,theta=theta,target_profile=tp,our_profile=op,path_mse=pl['path_mse'],coordinate_mse=pl['coordinate_mse'],**score,source_frames=producer.total_frames,submits=int(sim.submits),native_receipts=len(sim._receipt_ledger.seen),economic_filled_orders=sum(c.filled>0 for c in owners),zero_fill_orders=sum(c.filled==0 for c in owners),cancel_requests=sum(x['kind']=='CANCEL' for x in tr.actions),receipt_conditioned_continuations=int(sim.post_receipt_plans),new_eligible_frames=producer.new_eligible_frames,new_suppressed=producer.new_suppressed,final_inventory=dict(sim.inv),final_cost=float(sim.cost),terminal_worst=min(sim.inv.values())-float(sim.cost),peak_current_cash_requirement=actual.funding_demand()['current_cash_requirement'],unresolved_owners=unresolved)
 except Exception as ex:
  result['status']='ERROR';result['error']=type(ex).__name__+':'+str(ex);result['traceback']=traceback.format_exc(limit=12)
 finally:
  if sim is not None:
   try:sim.close()
   except Exception:pass
  result['elapsed_seconds']=time.monotonic()-started;(out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
 print(json.dumps({k:result.get(k) for k in ('status','market_id','rearm','market_direction','core_similarity','path_mse','error','elapsed_seconds')},ensure_ascii=False),flush=True)
 if result['status']!='COMPLETE':raise SystemExit(2)
if __name__=='__main__':main()
