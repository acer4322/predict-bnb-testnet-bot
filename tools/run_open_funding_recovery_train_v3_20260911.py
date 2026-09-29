"""Bounded native whole-episode policy optimization. Worker only,10 replay maximum.
Target records exist in scorer only, never in the executable policy's frame.
"""
import bisect
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
import importlib.util

BUNDLE=Path(__file__).resolve().parent
OLD=Path('C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
ROOT=Path('C:/BTC5M-worker/.tmp/open_funding_recovery_train_20260911_v3')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m




def loss_from_trajectories(states,source,terminal,qref):
    from tools.minimal_student_open_funding_v1 import path_loss
    return path_loss(states,source,terminal,qref)


class Trace:
    def __init__(self,out,name):
        self.path=out/(name+'.jsonl.gz');self.f=gzip.open(self.path,'wt',encoding='utf-8',newline='\n')
        self.now=0;self.states=[];self.actions=[];self.receipts=0;self.receipt_hash=hashlib.sha256()
        self.plans=0;self.bytes=0;self.non_displayed_new=0;self.new_plan_count=0;self.cancel_plan_count=0;self.peak_cash_requirement=0.;self.terminal_logged=set();self.prefix_hashes={k:hashlib.sha256() for k in ('native_action','canonical_receipt','own_state')};self.prefix_counts={k:0 for k in self.prefix_hashes}
    def emit(self,kind,x):
        text=json.dumps(dict(kind=kind,data=x),separators=(',',':'),allow_nan=False)+'\n'
        self.bytes+=len(text.encode());assert self.bytes<=32*1024**2,'bounded trace exceeded'
        self.f.write(text)
        if kind in self.prefix_hashes:
            self.prefix_hashes[kind].update((json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
            self.prefix_counts[kind]+=1
    def action(self,x):
        self.actions.append(x);assert len(self.actions)<=20000
        self.emit('native_action',x)
    def process(self,sim,t):
        for r in sim._receipt_delta_rows:
            self.receipts+=1;self.receipt_hash.update(json.dumps(r,sort_keys=True,separators=(',',':')).encode())
            self.emit('canonical_receipt',r)
        funding=sim.gateway.ledger.funding_demand()
        self.peak_cash_requirement=max(self.peak_cash_requirement,funding['current_cash_requirement'])
        s=dict(t=int(t),inv=dict(sim.inv),cost=float(sim.cost),funding_demand=funding);self.states.append(s)
        assert len(self.states)<=7000;self.emit('own_state',s)
    def plan(self,event):
        from tools.minimal_student_joint_policy_train_v1 import stable_features
        self.plans+=1;fr=event['input_frame'];actions=event['operations']
        if self.plans%250==0:print(json.dumps(dict(stage='policy_heartbeat',trace=self.path.name,frames=self.plans)),flush=True)
        accounts=event['own_after_plan']['accounts']
        self.peak_cash_requirement=max(self.peak_cash_requirement,sum(a['spent']+a['reserved_cash'] for a in accounts.values()))
        self.new_plan_count+=any(o['kind']=='NEW' for o in actions)
        self.cancel_plan_count+=any(o['kind']=='CANCEL' for o in actions)
        for o in actions:
            if o['kind']=='NEW':
                lev=fr['book']['bids'] if o['side']=='UP' else fr['book']['asks']
                np=o['price'] if o['side']=='UP' else round(1-o['price'],10)
                self.non_displayed_new+=not any(abs(float(k)-np)<1e-8 for k in lev)
        # Store exactly the actor's causal numeric input and all plan/authority
        # decisions, not a Target label or a fake private state.
        book={side:{float(k):v for k,v in fr['book'][side].items()} for side in ('bids','asks')}
        ff=dict(fr,book=book)
        features=stable_features(ff)
        # Terminal owners are serialized ONCE, not included in every subsequent
        # pending-owner snapshot. All native fills and cash still remain intact.
        for key,owner in fr['authority']['carriers'].items():
            if owner['state']=='TERMINAL' and key not in self.terminal_logged:
                self.emit('terminal_owner_once',dict(key=key,owner=owner,t=fr['t']))
                self.terminal_logged.add(key)
        self.emit('complete_policy_step',dict(t=fr['t'],frame_id=fr['frame_id'],features=features,
            own_inventory=fr['own_view']['inv'],own_cost=fr['own_view']['cost'],
            pending_carriers={k:c for k,c in fr['authority']['carriers'].items() if c['state']!='TERMINAL'},accounts=fr['authority']['accounts'],
            plan=event['plan'],operations=actions,policy_supervision_mask=False,
            target_data_in_model_input=False))
    def close(self):
        self.f.close();return dict(path=self.path.name,bytes=self.path.stat().st_size,sha256=sha(self.path),
            receipt_count=self.receipts,receipt_sha256=self.receipt_hash.hexdigest(),plans=self.plans,
            non_displayed_new_orders=self.non_displayed_new,new_action_plans=self.new_plan_count,
            cancel_action_plans=self.cancel_plan_count,peak_cash_requirement=self.peak_cash_requirement,capital_cap=None,terminal_owner_records=len(self.terminal_logged))


class AuditBT:
    def __init__(self,bt,tr):self.native=bt;self.tr=tr
    def __getattr__(self,n):return getattr(self.native,n)
    def cancel(self,asset,n,wait):
        rc=self.native.cancel(asset,n,wait);self.tr.action(dict(kind='CANCEL',t=self.tr.now,n=int(n),rc=int(rc)))
        return rc


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        load('bounded_supervisor',p).bounded('open-funding-episode-train',[sys.executable,str(Path(__file__).resolve()),'--child'],1200)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(version='OPEN_FUNDING_RECOVERY_TRAIN_V3',verdict='RUNNING',
        native_attempts=0,native_complete=0,joint_parameter_updates=0,evaluations=[],
        training_unit='FULL_CAUSAL_NATIVE_EPISODE',native_capabilities=['PASSIVE'],
        private_target_action_teacher=False,live_changes=0,new_markets=0,
        worker=os.environ.get('COMPUTERNAME'),max_threads=4,post_check_retune=False)
    tr=None;sim=None
    def save():
        result['elapsed_seconds']=time.monotonic()-started
        data=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False).encode();assert len(data)<120000
        (out/'COMPACT.json').write_bytes(data)
    try:
        assert not ROOT.exists(),'immutable training source root exists'
        pack=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in pack['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE) and p.stat().st_size==m['bytes'] and sha(p)==m['sha256'],rel
        assert sha(OLD/'MANIFEST.json')==pack['world_manifest_sha256']
        old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        stage=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text(encoding='utf-8'))
        for rel,want in stage['files'].items():
            if not rel.endswith('.py'):continue
            p=(FROZEN/rel).resolve();assert p.is_relative_to(FROZEN) and p.stat().st_size<2*1024**2 and sha(p)==want
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for rel,m in old['files'].items():
            if not rel.startswith(('tools/','tapes/')):continue
            p=OLD/rel;assert sha(p)==m['sha256']
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        dest=ROOT/'tools/minimal_student_joint_policy_train_v1.py'
        shutil.copy2(BUNDLE/'minimal_student_joint_policy_train_v1.py',dest)
        dest=ROOT/'tools/minimal_student_open_funding_v1.py'
        shutil.copy2(BUNDLE/'minimal_student_open_funding_v1.py',dest)
        runtime_path=ROOT/'tools/open_funding_recovery_runtime_v3.py'
        shutil.copy2(BUNDLE/'open_funding_recovery_runtime_v3.py',runtime_path)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools.open_funding_recovery_runtime_v3 import self_tests as recovery_tests,FastOpenFundingLedger,make_recovered_student,source_signature,audit_receipt_support,scalar_asdict
        result['recovery_tests']=recovery_tests()
        print(json.dumps(dict(stage='recovery_unit_tests_pass',tests=result['recovery_tests']['passed'])),flush=True)
        from tools.minimal_student_open_funding_v1 import self_tests
        result['unit_tests']=self_tests()
        save();print(json.dumps(dict(stage='funding_unit_tests_pass',tests=result['unit_tests']['passed'])),flush=True)
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class
        from tools.minimal_student_training_world_v2 import make_training_class
        from tools.minimal_student_open_funding_v1 import OpenFundingProfile as WorldProfile, OpenFundingLedger
        from tools.minimal_student_open_funding_v1 import OpenFundingWholePolicy as JointWholePolicy,initial_parameters,training_reference,PARAMETER_NAMES
        from tools.pair_core_economic_grant_ledger_v1 import Grant
        install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
        def strict_send(*a,**kw):
            rc=oldsend(*a,**kw)
            if int(rc)!=0:raise RuntimeError('native submit rejected/uncertain:'+str(rc))
            return rc
        base.ex.submit_native=strict_send
        Student=make_recovered_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
        OpenFundingLedger=FastOpenFundingLedger
        profile=WorldProfile(max_live_owners=4096)
        hashes={n:sha(Path(m.__file__)) for n,m in list(sys.modules.items())
            if n.startswith(('tools.','src.')) and getattr(m,'__file__',None)}
        for n,m in list(sys.modules.items()):
            if n in hashes:assert Path(m.__file__).resolve().is_relative_to(ROOT),n
        assert not any('r2_47' in n for n in hashes)
        result.update(world_profile=asdict(profile),native_sha256=NATIVE_SHA,loaded_source_hashes=hashes,
            world_manifest_sha256=pack['world_manifest_sha256'],parameter_names=PARAMETER_NAMES)
        train_sources={}
        def source(mid):
            p=BUNDLE/f'input_{mid}.json.gz'
            with gzip.open(p,'rb') as f:data=f.read(8*1024**2+1)
            assert len(data)<=8*1024**2
            s=json.loads(data);assert s['market']['market_id']==mid
            assert [s['market']['window_start_ms'],s['market']['window_end_ms']]==old['marketWindows'][str(mid)]
            return s
        for mid in (2022527,2022538):train_sources[mid]=source(mid)
        qref=training_reference(train_sources);INITIAL=initial_parameters(qref)
        result.update(fixed_train_share_unit=qref,capital_cap=None,funding_mode='VIRTUAL_NONBINDING_RESEARCH',utilization_reward_present=False)
        save()
        def run_case(theta,variant,mid,s,split,resource_limit=4096):
            nonlocal sim,tr
            t0=time.monotonic();result['native_attempts']+=1;assert result['native_attempts']<=11;save()
            case_profile=WorldProfile(max_live_owners=resource_limit)
            ledger=OpenFundingLedger(case_profile)
            for pid,side in ((1,'UP'),(2,'DOWN')):ledger.issue(Grant(pid,'LEARNING_OWNERS',side,0.,0.,0.,'USER_AUTHORIZED_VIRTUAL_NONBINDING_FUNDING'))
            producer=JointWholePolicy(theta,variant)
            tr=Trace(out,f'{split}_{variant}_{mid}')
            sim=Student(ROOT/f'tapes/{mid}.json.xz',case_profile.max_live_owners,False,
                producer=producer,ledger=ledger,trace=tr,
                verified_window=old['marketWindows'][str(mid)],window_source_sha256=old['marketWindowSourceSha256'])
            sim.bt=AuditBT(sim.bt,tr)
            assert len(sim.payload['updates'])<2048,'source observation bound exceeds proven nonbinding engineering capacity'
            sim.run_whole(base)
            prefix=dict(counts=dict(tr.prefix_counts),sha256={k:v.hexdigest() for k,v in tr.prefix_hashes.items()})
            if split=='GOLDEN':
                old_trace=Path('C:/BTC5M-worker/.lan_worker_v1/results/minimal-student-open-funding-train-logfix-20260911-v2/TRAIN_INITIAL_2022527.jsonl.gz')
                assert sha(old_trace)=='194e817ab1d8d2332301edbf5f6bbe98cfc778facad9330cbd3b19bb04e1b6dc'
                expected=source_signature(old_trace)
                assert prefix==expected,'source-prefix physical actions/receipts/own state changed'
                result['golden_source_prefix_exact']=True;save()
                print(json.dumps(dict(stage='golden_source_prefix_exact',counts=prefix['counts'])),flush=True)
            drain=sim.drain_queued_responses(base)
            result['latest_terminal_drain']=drain
            if any(c.state!='TERMINAL' for c in sim.gateway.ledger.carriers.values()):
                raise RuntimeError('UNRESOLVED_AFTER_NATIVE_ONLY_DRAIN_NO_FABRICATED_TERMINAL')
            actual=sim.gateway.ledger;actual.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
            assert not sim._receipt_invalid
            assert all(abs(sum(c.filled for c in actual.carriers.values() if actual.grants[c.parent_id].side==side)-sim.inv[side])<1e-7 for side in ('UP','DOWN'))
            assert abs(sum(c.payment+c.fees for c in actual.carriers.values())-sim.cost)<1e-7
            pending=sum(actual.account(pid)['reserved_cash'] for pid in actual.grants)
            terminal=dict(inv=dict(sim.inv),cost=sim.cost,pending_cash=pending)
            loss=loss_from_trajectories(tr.states,s,terminal,qref)
            obs=loss.pop('observations')
            for o in obs:tr.emit('SCORING_ONLY_TARGET_AND_OUR',o)
            submitted=[a for a in tr.actions if a['kind']=='NEW']
            quantities=[a['qty'] for a in submitted]
            owners=list(actual.carriers.values())
            row=dict(variant=variant,market_id=mid,split=split,theta=list(theta),loss=loss,
                source_prefix=prefix,terminal_drain=drain,resource_limit=resource_limit,
                submits=sim.submits,cancel_requests=sum(a['kind']=='CANCEL' for a in tr.actions),
                native_receipts=len(sim._receipt_ledger.seen),zero_fill_orders=sum(c.filled==0 for c in owners),
                partial_orders=sum(0<c.filled<c.qty-1e-8 for c in owners),
                unresolved_owners=sum(c.state!='TERMINAL' for c in owners),pending_cash=pending,
                capital_cap=None,final_funding_demand=actual.funding_demand(),
                resource_censor_events=sum(v for k,v in producer.declines.items() if 'RESOURCE_CENSOR' in k),
                final_inventory=dict(sim.inv),final_cost=sim.cost,
                UP_branch=sim.inv['UP']-sim.cost,DOWN_branch=sim.inv['DOWN']-sim.cost,
                late_new_orders=sim.late_new_orders,max_live_owners=sim.max_simultaneous_slots,
                multi_new_plans=producer.multi_new_plans,bidirectional_plans=producer.bidirectional_plans,
                receipt_conditioned_continuations=sim.post_receipt_plans,
                min_requested_qty=min(quantities,default=None),max_requested_qty=max(quantities,default=None),
                declined_candidates=producer.declines,trace=tr.close(),elapsed_seconds=time.monotonic()-t0)
            tr=None
            row['receipt_support']=audit_receipt_support(out/row['trace']['path'],{k:scalar_asdict(c) for k,c in actual.carriers.items()})
            assert row['receipt_support']['canonical_zero_fill_orders']==row['zero_fill_orders']
            if split!='GOLDEN':assert row['resource_censor_events']==0,'engineering ceiling unexpectedly censored this policy'
            sim.close();sim=None
            result['evaluations'].append(row);result['native_complete']+=1;save()
            print(json.dumps(dict(stage='episode_complete',variant=variant,market=mid,split=split,
                loss=row['loss']['total_loss'],submits=row['submits'],receipts=row['native_receipts'],
                late_new=row['late_new_orders'],pending=row['unresolved_owners'])),flush=True)
            return row
        def evaluate(theta,name):
            rows=[run_case(theta,name,mid,train_sources[mid],'TRAIN') for mid in (2022527,2022538)]
            return sum(x['loss']['total_loss'] for x in rows)/2.
        golden=run_case(INITIAL,'RECOVERY_GOLDEN',2022527,train_sources[2022527],'GOLDEN',resource_limit=32)
        result['golden_terminal_closed']=golden['unresolved_owners']==0
        result['old_count_discrepancy_resolved']=golden['receipt_support']['numeric_residual_only_orders']==57
        save()
        rng=random.Random(20260911);direction=[rng.choice((-1.,1.)) for _ in INITIAL]
        candidates={'INITIAL':list(INITIAL),'PLUS':[x+.20*d for x,d in zip(INITIAL,direction)],
                    'MINUS':[x-.20*d for x,d in zip(INITIAL,direction)]}
        scores={name:evaluate(th,name) for name,th in candidates.items()}
        deriv=(scores['PLUS']-scores['MINUS'])/.40
        updated=[x-.25*deriv*d for x,d in zip(INITIAL,direction)]
        assert all(math.isfinite(x) for x in updated)
        candidates['JOINT_UPDATE']=updated;result['joint_parameter_updates']=1
        scores['JOINT_UPDATE']=evaluate(updated,'JOINT_UPDATE')
        selected=min(scores,key=lambda k:scores[k])
        frozen=dict(version='OPEN_FUNDING_WHOLE_POLICY_V1',theta=candidates[selected],
            parameter_names=PARAMETER_NAMES,selected=selected,initial=INITIAL,
            candidates=candidates,mean_train_loss=scores,gradient_direction=direction,
            directional_derivative=deriv,joint_parameter_updates=1,
            train_markets=[2022527,2022538],check_data_loaded=False,
            policy_scope='FULL_PASSIVE_MANAGER_NOT_ACTIVE_OR_PRIVATE_TARGET_TEACHER',
            world_profile=asdict(profile),native_sha256=NATIVE_SHA,
            core_sha256=sha(dest),fixed_train_share_unit=qref,capital_cap=None,objective='UNCAPPED_OBSERVED_SHARES_AND_PAYOFF_BRANCH_PATH_MSE',
            action_authority='RESEARCH_NATIVE_ONLY_NOT_LIVE')
        fp=out/'FROZEN_WHOLE_POLICY.json';assert not fp.exists()
        fp.write_text(json.dumps(frozen,indent=2),encoding='utf-8');freeze_hash=sha(fp)
        result.update(train_losses=scores,selected=selected,frozen_model_sha256=freeze_hash,
            selected_differs_from_initial=candidates[selected]!=list(INITIAL),freeze_before_check=True)
        save();print(json.dumps(dict(stage='policy_frozen',selected=selected,train_losses=scores,sha256=freeze_hash)),flush=True)
        # The consumed check cohort is loaded only after parameters are frozen.
        checksource=source(2022602)
        checkbase=run_case(INITIAL,'INITIAL',2022602,checksource,'PIPELINE_CHECK')
        checked=run_case(candidates[selected],'SELECTED_FROZEN',2022602,checksource,'PIPELINE_CHECK')
        assert sha(fp)==freeze_hash
        result.update(verdict='RECOVERED_OPEN_FUNDING_TRAINING_AND_FROZEN_CHECK_COMPLETED',
            train_loss_improvement=scores['INITIAL']-scores[selected],
            check_loss_improvement=checkbase['loss']['total_loss']-checked['loss']['total_loss'],
            frozen_model_unchanged_after_check=True,
            caveats=['Single joint optimization step on2consumed markets is not convergence or profitability.',
                'Target observation path defines a scoring-only researcher objective, not original order/cancel labels.',
                'Native Active remains unsupported; not full four-channel imitation.',
                'Zero actions, outstanding terminal owners and new quote support are reported, never masked as success.',
                'Source era remains9/7; larger recent Target sizes are not retroactively substituted.',
                'Execution queue/latency/fee assumptions unchanged; financial funding is nonbinding simulation, not live feasibility.',
                'No numeric comparability with the old capped100 score. Training reference fixes units, not a ceiling.',
                'Resource4096 is a proven nonbinding bound for max2new per less2048observations in this fixed policy/cohort; no order-censor events permitted.',
                'Golden32 source prefix exactly matches previous actions/receipts/balances before queued-response drain.',
                'Final EOF observation may be a conservative query-time upper bound, not exact native terminal time.'])
        for file in ('minimal_student_joint_policy_train_v1.py','minimal_student_open_funding_v1.py','open_funding_recovery_runtime_v3.py','PREREG.md'):
            shutil.copy2(BUNDLE/file,out/file)
    except Exception as exc:
        result.update(verdict='WHOLE_EPISODE_TRAINING_ERROR_STOPPED',error=type(exc).__name__+': '+str(exc),
            traceback=traceback.format_exc(limit=12))
    finally:
        if tr is not None:
            try:result['partial_trace']=tr.close()
            except Exception:pass
        if sim is not None:
            try:sim.close()
            except Exception:pass
        save()
    print(json.dumps({k:v for k,v in result.items() if k in ('verdict','native_complete','joint_parameter_updates','train_loss_improvement','check_loss_improvement','error','elapsed_seconds')}),flush=True)
    if result['verdict']=='WHOLE_EPISODE_TRAINING_ERROR_STOPPED':raise SystemExit(2)


if __name__=='__main__':main()
