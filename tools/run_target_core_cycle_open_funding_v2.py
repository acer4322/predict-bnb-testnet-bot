"""BTC5M Target core-cycle micro-world V2: uncapped research funding.

Research purpose ONLY: discover reusable Target-like feedback mechanisms.
This is explicitly not parameter graduation, PnL optimization or live policy work.

Properties:
- Target actions are scoring-only and never enter executable policy frames.
- Full causal native replay + canonical receipts; no dream fill.
- Uses the already accepted OpenFunding world (capital_cap=None).
- Numeric coefficients are frozen at the accepted INITIAL values derived from the
  two consumed TRAIN markets; the experiment only toggles mechanism groups.
- Heavy execution is intended for the LAN worker.
"""
from __future__ import annotations

import argparse
import bisect
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

BUNDLE=Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/open_funding_recovery_train_20260911_v3')
TEMPLATE_ROOT=Path(r'C:/BTC5M-worker/.tmp/open_funding_recovery_train_20260911_v3')
BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
OPEN_POLICY_SHA='507bd3724ec534e6c9c64a479aa3985f77304f796529bad765780e9e538ca1dd'
RECOVERY_SHA='2468fd631c277e114c3f943bfc9f4867c4f06bdab5f69a26b24d8a8685dd00f1'

GROUPS=[
    ('MARKET_STATE_DIRECTION',(1,2)),
    ('OWN_EXPOSURE_FEEDBACK',(3,)),
    ('PHASE_DIRECTION_BIAS',(4,)),
    ('GROSS_PHASE_SCALING',(6,)),
    ('DEFICIT_TICKET_FEEDBACK',(8,)),
    ('INVENTORY_QUOTE_FEEDBACK',(10,)),
]


def sha(p:Path)->str:
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''): h.update(b)
    return h.hexdigest()


def target_path(source):
    by={}
    for a in source['targetActions']:
        if a.get('quote_type')!='BID' or a.get('side') not in ('UP','DOWN'): continue
        q=float(a['shares']);p=float(a['price'])
        if q>0 and 0<p<1: by.setdefault(int(a['event_ms']),[]).append(a)
    return by


def target_profile(source,start,end,bins=6):
    seq=[];hist=[0.]*bins
    for t,aa in sorted(target_path(source).items()):
        uq=sum(float(a['shares']) for a in aa if a['side']=='UP')
        dq=sum(float(a['shares']) for a in aa if a['side']=='DOWN')
        seq.append('UP' if uq>=dq else 'DOWN')
        ph=max(0.,min(.999999,(t-start)/max(1,end-start)))
        hist[min(bins-1,int(ph*bins))]+=uq+dq
    z=sum(hist);hist=[x/z if z else 0. for x in hist]
    sw=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
    return dict(event_batches=len(seq),switch_rate=sw,phase_hist=hist)


def own_profile(states,start,end,bins=6):
    seq=[];hist=[0.]*bins;prev={'UP':0.,'DOWN':0.}
    for st in states:
        du=max(0.,float(st['inv']['UP'])-prev['UP']); dd=max(0.,float(st['inv']['DOWN'])-prev['DOWN'])
        prev=dict(st['inv'])
        if du+dd<=1e-12: continue
        seq.append('UP' if du>=dd else 'DOWN')
        ph=max(0.,min(.999999,(int(st['t'])-start)/max(1,end-start)))
        hist[min(bins-1,int(ph*bins))]+=du+dd
    z=sum(hist);hist=[x/z if z else 0. for x in hist]
    sw=sum(a!=b for a,b in zip(seq,seq[1:]))/max(1,len(seq)-1)
    return dict(event_batches=len(seq),switch_rate=sw,phase_hist=hist)


def structural_score(path_mse,tp,op):
    geometry=math.exp(-6.0*float(path_mse))
    phase=max(0.,1.-0.5*sum(abs(a-b) for a,b in zip(tp['phase_hist'],op['phase_hist'])))
    switch=max(0.,1.-abs(tp['switch_rate']-op['switch_rate']))
    activity=math.exp(-abs(math.log((op['event_batches']+1)/(tp['event_batches']+1))))
    return dict(core_similarity=.60*geometry+.15*phase+.15*switch+.10*activity,
                geometry_similarity=geometry,phase_similarity=phase,
                switch_similarity=switch,activity_similarity=activity)


def theta_for_mask(initial,mask):
    th=list(map(float,initial))
    for bit,(_,idxs) in enumerate(GROUPS):
        if not(mask&(1<<bit)):
            for i in idxs: th[i]=0.0
    return th


class TraceLite:
    def __init__(self):
        self.now=0;self.states=[];self.actions=[];self.plans=0
        self.new_plans=0;self.cancel_plans=0;self.terminal_logged=set()
    def emit(self,kind,x):
        return None
    def action(self,x): self.actions.append(x)
    def process(self,sim,t):
        self.now=int(t);self.states.append(dict(t=int(t),inv=dict(sim.inv),cost=float(sim.cost)))
    def plan(self,event):
        self.plans+=1;ops=event['operations']
        self.new_plans+=int(any(o['kind']=='NEW' for o in ops))
        self.cancel_plans+=int(any(o['kind']=='CANCEL' for o in ops))


class AuditBT:
    def __init__(self,bt,tr): self.native=bt;self.tr=tr
    def __getattr__(self,n): return getattr(self.native,n)
    def cancel(self,asset,n,wait):
        rc=self.native.cancel(asset,n,wait)
        self.tr.action(dict(kind='CANCEL',t=self.tr.now,n=int(n),rc=int(rc)))
        return rc


def load_source(mid,old):
    p=BUNDLE/f'input_{mid}.json.gz'
    with gzip.open(p,'rb') as f: data=f.read(8*1024*1024+1)
    if len(data)>8*1024*1024: raise RuntimeError('source too large')
    s=json.loads(data); assert int(s['market']['market_id'])==mid
    assert [s['market']['window_start_ms'],s['market']['window_end_ms']]==old['marketWindows'][str(mid)]
    return s


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--market',type=int,default=2022527)
    ap.add_argument('--mask-start',type=int,default=0)
    ap.add_argument('--mask-count',type=int,default=16)
    ap.add_argument('--masks',default=None)
    ap.add_argument('--gross-phase-sign',type=int,choices=(-1,1),default=1,help='architecture falsification only: reverse lifecycle gross progression without tuning magnitude')
    a=ap.parse_args()
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);out.mkdir(parents=True,exist_ok=True)
    job=out.name
    root=Path(r'C:/BTC5M-worker/.tmp')/('target_core_cycle_open_v2_'+job)
    result=dict(version='TARGET_CORE_CYCLE_OPEN_FUNDING_V2',status='RUNNING',market_id=a.market,
        purpose='DISCOVER_CORE_LOOP_MECHANISMS_NOT_PARAMETER_GRADUATION',target_runtime_access=False,
        target_scoring_only=True,dream_fill=False,funding_mode='VIRTUAL_NONBINDING_RESEARCH',capital_cap=None,
        mechanism_groups=[dict(bit=i,name=n,theta_indices=list(x)) for i,(n,x) in enumerate(GROUPS)],
        requested_masks=a.masks,gross_phase_sign=a.gross_phase_sign,rows=[],worker=os.environ.get('COMPUTERNAME'))
    started=time.monotonic();sim=None
    try:
        assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
        assert BUNDLE.exists() and TEMPLATE_ROOT.exists() and BACKEND.exists()
        if root.exists(): shutil.rmtree(root)
        shutil.copytree(TEMPLATE_ROOT,root)
        assert sha(root/'tools/minimal_student_open_funding_v1.py')==OPEN_POLICY_SHA
        assert sha(root/'tools/open_funding_recovery_runtime_v3.py')==RECOVERY_SHA
        old=json.loads((Path(r'C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')/'MANIFEST.json').read_text(encoding='utf-8'))
        sys.path.insert(0,str(root));os.chdir(root)
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class
        from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger,make_recovered_student
        from tools.minimal_student_open_funding_v1 import OpenFundingProfile,OpenFundingWholePolicy,initial_parameters,training_reference,path_loss
        from tools.pair_core_economic_grant_ledger_v1 import Grant
        install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
        def strict_send(*args,**kw):
            rc=oldsend(*args,**kw)
            if int(rc)!=0: raise RuntimeError('native submit rejected/uncertain:'+str(rc))
            return rc
        base.ex.submit_native=strict_send
        Student=make_recovered_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
        profile=OpenFundingProfile(max_live_owners=4096)
        train_sources={m:load_source(m,old) for m in (2022527,2022538)}
        qref=training_reference(train_sources);initial=initial_parameters(qref)
        source=train_sources.get(a.market) or load_source(a.market,old)
        start,end=old['marketWindows'][str(a.market)];tp=target_profile(source,start,end)
        result['fixed_train_share_unit']=qref;result['initial_theta']=initial;result['target_profile']=tp
        if a.masks:
            masks=sorted({int(x.strip()) for x in a.masks.split(',') if x.strip()})
            if not masks or any(x<0 or x>63 for x in masks): raise ValueError('mask out of range')
        else: masks=list(range(max(0,a.mask_start),min(64,a.mask_start+a.mask_count)))
        for mask in masks:
            t0=time.monotonic();theta=theta_for_mask(initial,mask)
            if mask & (1<<3): theta[6]=abs(theta[6])*a.gross_phase_sign
            ledger=FastOpenFundingLedger(profile)
            for pid,side in ((1,'UP'),(2,'DOWN')):
                ledger.issue(Grant(pid,'MICROWORLD',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING'))
            producer=OpenFundingWholePolicy(theta,f'MASK_{mask:02d}');tr=TraceLite()
            sim=Student(root/f'tapes/{a.market}.json.xz',profile.max_live_owners,False,
                producer=producer,ledger=ledger,trace=tr,verified_window=[start,end],
                window_source_sha256=old['marketWindowSourceSha256'])
            sim.bt=AuditBT(sim.bt,tr);row=None
            try:
                sim.run_whole(base);sim.drain_queued_responses(base)
                actual=sim.gateway.ledger;actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                if sim._receipt_invalid: raise RuntimeError('receipt invalid')
                unresolved=sum(c.state!='TERMINAL' for c in actual.carriers.values())
                if unresolved: raise RuntimeError('unresolved owners after native drain:'+str(unresolved))
                pending=sum(actual.account(pid)['reserved_cash'] for pid in actual.grants)
                terminal=dict(inv=dict(sim.inv),cost=float(sim.cost),pending_cash=float(pending))
                pl=path_loss(tr.states,source,terminal,qref);op=own_profile(tr.states,start,end)
                score=structural_score(pl['path_mse'],tp,op)
                owners=list(actual.carriers.values())
                row=dict(mask=mask,enabled=[n for bit,(n,_) in enumerate(GROUPS) if mask&(1<<bit)],theta=theta,status='PASS',
                    path_mse=pl['path_mse'],coordinate_mse=pl['coordinate_mse'],**score,target_profile=tp,our_profile=op,
                    submits=int(sim.submits),native_receipts=len(sim._receipt_ledger.seen),economic_filled_orders=sum(c.filled>0 for c in owners),
                    zero_fill_orders=sum(c.filled==0 for c in owners),cancel_requests=sum(x['kind']=='CANCEL' for x in tr.actions),
                    receipt_conditioned_continuations=int(sim.post_receipt_plans),bidirectional_plans=producer.bidirectional_plans,
                    multi_new_plans=producer.multi_new_plans,final_inventory=dict(sim.inv),final_cost=float(sim.cost),
                    terminal_worst=min(sim.inv.values())-float(sim.cost),peak_current_cash_requirement=actual.funding_demand()['current_cash_requirement'],
                    unresolved_owners=unresolved,elapsed_seconds=time.monotonic()-t0)
            except Exception as ex:
                row=dict(mask=mask,enabled=[n for bit,(n,_) in enumerate(GROUPS) if mask&(1<<bit)],theta=theta,status='FAIL',
                    error=type(ex).__name__+':'+str(ex),elapsed_seconds=time.monotonic()-t0)
            finally:
                try: sim.close()
                except Exception: pass
                sim=None
            result['rows'].append(row)
            print(json.dumps({'mask':mask,'status':row['status'],'score':row.get('core_similarity'),'loss':row.get('path_mse'),
                'filled':row.get('economic_filled_orders'),'events':(row.get('our_profile') or {}).get('event_batches'),'elapsed':round(row['elapsed_seconds'],2)}),flush=True)
        good=[r for r in result['rows'] if r['status']=='PASS']
        result['best']=sorted(good,key=lambda r:(r['core_similarity'],-r['path_mse']),reverse=True)[:8]
        result['status']='COMPLETE'
    except Exception as ex:
        result['status']='ERROR';result['error']=type(ex).__name__+':'+str(ex);result['traceback']=traceback.format_exc(limit=12)
    finally:
        result['elapsed_seconds']=time.monotonic()-started
        (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(json.dumps({'status':result['status'],'rows':len(result['rows']),'best':(result.get('best') or [{}])[0].get('mask'),'error':result.get('error')},ensure_ascii=False),flush=True)
    if result['status']!='COMPLETE': raise SystemExit(2)

if __name__=='__main__': main()
