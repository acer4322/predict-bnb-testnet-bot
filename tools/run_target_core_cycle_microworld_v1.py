"""BTC5M Target core-cycle micro-world V1.

Purpose: mechanism discovery, NOT parameter graduation.
- One already-consumed development market only (default 2022527).
- Target transactions are scoring-only and never enter executable policy frames.
- Full native causal replay / receipt kernel; no dream fill.
- Factorial ablation of six adaptive feedback pathways in the existing minimal whole policy.
- Heavy execution belongs on the LAN worker.

This deliberately holds all numeric coefficients fixed and only toggles mechanism groups.
Later work may tune parameters only after core-loop mechanisms are identified.
"""
from __future__ import annotations

import argparse
import bisect
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

OLD=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')
FROZEN=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
POLICY_SRC=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_whole_episode_train_20260911_v1/minimal_student_joint_policy_train_v1.py')
BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
TEMPLATE_ROOT=Path(r'C:/BTC5M-worker/.tmp/minimal_student_whole_episode_train_20260911_v1')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'

GROUPS=[
    ('MARKET_STATE_DIRECTION',(1,2)),
    ('OWN_EXPOSURE_FEEDBACK',(3,)),
    ('PHASE_EXPOSURE',(4,)),
    ('PHASE_UTILIZATION',(6,)),
    ('DEFICIT_TICKET_FEEDBACK',(8,)),
    ('INVENTORY_QUOTE_FEEDBACK',(10,)),
]


def sha(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''): h.update(b)
    return h.hexdigest()


def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def vec(inv,cost):
    gross=inv['UP']+inv['DOWN']
    return [(inv['UP']-inv['DOWN'])/(1.+gross),
            (min(inv.values())-cost)/(1.+cost),
            (max(inv.values())-cost)/(1.+cost),
            min(cost/100.,1.)]


def target_path(source):
    bytime={}
    for a in source['targetActions']:
        if a.get('quote_type')!='BID' or a.get('side') not in ('UP','DOWN'):
            continue
        q=float(a['shares']);p=float(a['price'])
        if not(q>0 and 0<p<1): continue
        bytime.setdefault(int(a['event_ms']),[]).append(a)
    return bytime


def path_loss(states,source,terminal):
    times=[s['t'] for s in states]; assert times==sorted(times)
    inv={'UP':0.,'DOWN':0.};cost=0.;terms=[0.,0.,0.,0.];n=0
    bytime=target_path(source)
    for t,aa in sorted(bytime.items()):
        for a in aa:
            inv[a['side']]+=float(a['shares']);cost+=float(a['price'])*float(a['shares'])
        j=bisect.bisect_right(times,t)-1
        ours=states[j] if j>=0 else {'inv':{'UP':0.,'DOWN':0.},'cost':0.}
        tv=vec(inv,cost);ov=vec(ours['inv'],ours['cost'])
        for k in range(4): terms[k]+=(ov[k]-tv[k])**2
        n+=1
    comp=[v/max(1,n) for v in terms]
    path=sum(comp)/4.
    worst=min(terminal['inv'].values())-terminal['cost']
    return dict(path_mse=path,coordinate_mse=comp,target_event_batches=n,
                terminal_worst=worst)


def event_profile_target(source,start,end,bins=6):
    by=target_path(source); seq=[]; hist=[0.]*bins
    for t,aa in sorted(by.items()):
        uq=sum(float(a['shares']) for a in aa if a['side']=='UP')
        dq=sum(float(a['shares']) for a in aa if a['side']=='DOWN')
        side='UP' if uq>=dq else 'DOWN'; seq.append(side)
        ph=max(0.,min(.999999,(t-start)/max(1,end-start)))
        hist[min(bins-1,int(ph*bins))]+=uq+dq
    s=sum(hist);hist=[x/s if s else 0. for x in hist]
    flips=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
    return dict(event_batches=len(seq),switch_rate=flips,phase_hist=hist)


def event_profile_ours(states,start,end,bins=6):
    seq=[];hist=[0.]*bins;prev={'UP':0.,'DOWN':0.}
    for st in states:
        du=max(0.,float(st['inv']['UP'])-prev['UP']);dd=max(0.,float(st['inv']['DOWN'])-prev['DOWN'])
        prev=dict(st['inv'])
        if du+dd<=1e-12: continue
        side='UP' if du>=dd else 'DOWN';seq.append(side)
        ph=max(0.,min(.999999,(int(st['t'])-start)/max(1,end-start)))
        hist[min(bins-1,int(ph*bins))]+=du+dd
    s=sum(hist);hist=[x/s if s else 0. for x in hist]
    flips=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
    return dict(event_batches=len(seq),switch_rate=flips,phase_hist=hist)


def structural_similarity(path,tar,ours):
    geometry=math.exp(-6.0*float(path['path_mse']))
    phase=max(0.,1.-0.5*sum(abs(a-b) for a,b in zip(tar['phase_hist'],ours['phase_hist'])))
    switch=max(0.,1.-abs(tar['switch_rate']-ours['switch_rate']))
    activity=math.exp(-abs(math.log((ours['event_batches']+1)/(tar['event_batches']+1))))
    score=.60*geometry+.20*phase+.15*switch+.05*activity
    return dict(core_similarity=score,geometry_similarity=geometry,phase_similarity=phase,
                switch_similarity=switch,activity_similarity=activity)


class TraceLite:
    def __init__(self):
        self.states=[];self.actions=[];self.plans=0;self.new_plans=0;self.cancel_plans=0
    def action(self,x): self.actions.append(x)
    def process(self,sim,t): self.states.append(dict(t=int(t),inv=dict(sim.inv),cost=float(sim.cost)))
    def plan(self,event):
        self.plans+=1
        ops=event['operations']
        self.new_plans+=any(o['kind']=='NEW' for o in ops)
        self.cancel_plans+=any(o['kind']=='CANCEL' for o in ops)


class AuditBT:
    def __init__(self,bt,tr): self.native=bt;self.tr=tr
    def __getattr__(self,n): return getattr(self.native,n)
    def cancel(self,asset,n,wait):
        rc=self.native.cancel(asset,n,wait);self.tr.action(dict(kind='CANCEL',t=self.tr.states[-1]['t'] if self.tr.states else None,n=int(n),rc=int(rc)))
        return rc


def theta_for_mask(initial,mask):
    th=list(map(float,initial))
    for bit,(_,indices) in enumerate(GROUPS):
        if not(mask & (1<<bit)):
            for i in indices: th[i]=0.0
    return th


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--market',type=int,default=2022527)
    ap.add_argument('--mask-start',type=int,default=0)
    ap.add_argument('--mask-count',type=int,default=16)
    ap.add_argument('--masks',default=None,help='comma-separated explicit masks for zero-retune transfer')
    args=ap.parse_args()
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True)
    job=Path(os.environ['BTC5M_LAN_RESULT_DIR']).name
    root=Path(r'C:/BTC5M-worker/.tmp')/('target_core_cycle_microworld_'+job)
    result=dict(version='TARGET_CORE_CYCLE_MICROWORLD_V1',status='RUNNING',market_id=args.market,
                purpose='DISCOVER_TARGET_CORE_LOOP_MECHANISMS_NOT_PARAMETER_GRADUATION',
                target_runtime_access=False,target_scoring_only=True,dream_fill=False,
                mechanism_groups=[dict(bit=i,name=n,theta_indices=list(idx)) for i,(n,idx) in enumerate(GROUPS)],
                mask_start=args.mask_start,mask_count=args.mask_count,requested_masks=args.masks,rows=[],worker=os.environ.get('COMPUTERNAME'))
    started=time.monotonic(); sim=None
    try:
        assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
        assert OLD.exists() and POLICY_SRC.exists() and BACKEND.exists() and TEMPLATE_ROOT.exists()
        if root.exists(): shutil.rmtree(root)
        shutil.copytree(TEMPLATE_ROOT,root)
        old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
        # Pin the exact world/policy files from the previously audited native whole-episode run.
        assert sha(root/'tools/minimal_student_training_world_v2.py')=='e1c231f255ed8c77dc4f9a37464ec3960edc44effa7bd21b42ac170af608fa34'
        assert sha(root/'tools/minimal_student_joint_policy_train_v1.py')=='416321e95dbc7898c9579ebbc65404eef15173029f374ad5b5d0700c981446af'
        assert sha(root/'tools/minimal_student_native_system_plan_v1.py')=='1137d6d3128bf0cc81b633ec87b45a5d56306d341fbd6fa45e9010d8e61a0931'
        assert sha(root/'tools/minimal_student_quantity_seam_v1.py')=='af01af58c9222e9116f90444d84fbcefef88c8354e56e47f929c45b622c98050'
        sys.path.insert(0,str(root));os.chdir(root)
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class
        from tools.minimal_student_training_world_v2 import WorldProfile,TrainingGrantLedger,make_training_class
        from tools.minimal_student_joint_policy_train_v1 import JointWholePolicy,INITIAL
        from tools.pair_core_economic_grant_ledger_v1 import Grant
        install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
        def strict_send(*a,**kw):
            rc=oldsend(*a,**kw)
            if int(rc)!=0: raise RuntimeError('native submit rejected/uncertain:'+str(rc))
            return rc
        base.ex.submit_native=strict_send
        Student=make_training_class(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
        profile=WorldProfile()
        p=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_whole_episode_train_20260911_v1')/f'input_{args.market}.json.gz'
        with gzip.open(p,'rb') as f: source=json.loads(f.read(8*1024**2+1))
        start,end=old['marketWindows'][str(args.market)]
        tar_prof=event_profile_target(source,start,end)
        if args.masks:
            masks=sorted({int(x.strip()) for x in args.masks.split(',') if x.strip()})
            if not masks or any(x<0 or x>63 for x in masks): raise ValueError('explicit masks must be within 0..63')
        else:
            masks=list(range(max(0,args.mask_start),min(64,args.mask_start+args.mask_count)))
        for mask in masks:
            t0=time.monotonic();ledger=TrainingGrantLedger(100.,profile)
            for pid,side in ((1,'UP'),(2,'DOWN')): ledger.issue(Grant(pid,'TRAIN_FIXTURE',side,0.,110.,50.,'FIXED_INITIAL_CAP_NOT_TARGET_BANKROLL'))
            theta=theta_for_mask(INITIAL,mask);prod=JointWholePolicy(theta,f'MASK_{mask:02d}')
            tr=TraceLite();sim=Student(root/f'tapes/{args.market}.json.xz',profile.max_live_owners,False,
                producer=prod,ledger=ledger,trace=tr,verified_window=[start,end],
                window_source_sha256=old['marketWindowSourceSha256'])
            sim.bt=AuditBT(sim.bt,tr);status='PASS';err=None
            try:
                sim.run_whole(base)
                actual=sim.gateway.ledger;actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                if sim._receipt_invalid: raise RuntimeError('receipt invalid')
                pending=sum(actual.account(pid)['reserved_cash'] for pid in actual.grants)
                terminal=dict(inv=dict(sim.inv),cost=float(sim.cost),pending_cash=float(pending))
                pl=path_loss(tr.states,source,terminal);our_prof=event_profile_ours(tr.states,start,end)
                simscore=structural_similarity(pl,tar_prof,our_prof)
                enabled=[name for bit,(name,_) in enumerate(GROUPS) if mask&(1<<bit)]
                row=dict(mask=mask,enabled=enabled,theta=theta,status=status,**pl,**simscore,
                         target_profile=tar_prof,our_profile=our_prof,submits=int(sim.submits),
                         native_receipts=len(sim._receipt_ledger.seen),cancel_requests=sum(a['kind']=='CANCEL' for a in tr.actions),
                         unresolved_owners=sum(c.state!='TERMINAL' for c in actual.carriers.values()),
                         final_inventory=dict(sim.inv),final_cost=float(sim.cost),
                         bidirectional_plans=prod.bidirectional_plans,multi_new_plans=prod.multi_new_plans,
                         receipt_conditioned_continuations=int(sim.post_receipt_plans),
                         elapsed_seconds=time.monotonic()-t0)
            except Exception as ex:
                status='FAIL';err=type(ex).__name__+':'+str(ex)
                row=dict(mask=mask,enabled=[name for bit,(name,_) in enumerate(GROUPS) if mask&(1<<bit)],
                         theta=theta,status=status,error=err,elapsed_seconds=time.monotonic()-t0)
            finally:
                try: sim.close()
                except Exception: pass
                sim=None
            result['rows'].append(row)
            print(json.dumps({'mask':mask,'status':row['status'],'core_similarity':row.get('core_similarity'),
                              'path_mse':row.get('path_mse'),'receipts':row.get('native_receipts'),'elapsed':round(row['elapsed_seconds'],2)}),flush=True)
        result['status']='COMPLETE'
        good=[r for r in result['rows'] if r['status']=='PASS']
        result['best']=sorted(good,key=lambda r:r['core_similarity'],reverse=True)[:5]
    except Exception as ex:
        result['status']='ERROR';result['error']=type(ex).__name__+':'+str(ex);result['traceback']=traceback.format_exc(limit=12)
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':result['status'],'rows':len(result['rows']),'elapsed_seconds':round(result['elapsed_seconds'],2),
                      'best_mask':(result.get('best') or [{}])[0].get('mask'),'error':result.get('error')}),flush=True)
    if result['status']!='COMPLETE': raise SystemExit(2)

if __name__=='__main__': main()
