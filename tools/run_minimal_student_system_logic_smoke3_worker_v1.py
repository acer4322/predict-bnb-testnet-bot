"""Three entire management/feedback protocol chains, second-worker only.
Synthetic receipts are fault injection, NOT HFT/dream-fill evidence or a teacher.
"""
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        sp=importlib.util.spec_from_file_location('bounded',helper);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
        m.bounded('minimal-student-system-logic',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_SYSTEM_LOGIC_SMOKE3_V1',verdict='RUNNING',
        scope='COMPLETE_PLAN_RESERVATION_AND_FEEDBACK_PROTOCOL_ONLY',
        fixture_kind='SYNTHETIC_FAULT_INJECTION_NOT_MARKET_SIMULATION',
        model_fits=0,HFT=0,real_market_episodes=0,live_changes=0,
        second_worker=os.environ.get('COMPUTERNAME'),max_threads=int(os.environ['OMP_NUM_THREADS']),
        native_policy_connected=False,Target_teacher_created=False,checks=[],scenarios=[])
    def check(name,value):
        if not value:raise AssertionError(name)
        result['checks'].append(name)
    def reject(name,fn,contains):
        try:fn()
        except (ValueError,AssertionError) as exc:
            check(name,contains in str(exc))
        else:raise AssertionError(name+' unexpectedly accepted')
    try:
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in manifest['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE)
            assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256'],rel
        sys.path.insert(0,str(BUNDLE))
        from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant
        from tools.minimal_student_system_plan_v1 import SystemPlanGateway,PlanAction
        for name,m in list(sys.modules.items()):
            if name.startswith('tools') and getattr(m,'__file__',None):
                assert Path(m.__file__).resolve().is_relative_to(BUNDLE),name
        result['loaded_sources']={name:dict(path=str(m.__file__),sha256=sha(Path(m.__file__)))
            for name,m in list(sys.modules.items()) if name.startswith('tools') and getattr(m,'__file__',None)}
        def gateway(capabilities=('PASSIVE','ACTIVE')):
            ledger=EconomicGrantLedger(84.) # disclosed free diagnostic capital; initial cost16 already spent
            ledger.issue(Grant(1,'repair-and-surplus','DOWN',30.,25.,40.,'EXPLICIT_SYNTHETIC_GRANT_NOT_TARGET'))
            ledger.issue(Grant(2,'concurrent-upside','UP',0.,20.,20.,'EXPLICIT_SYNTHETIC_GRANT_NOT_TARGET'))
            return SystemPlanGateway(ledger,asset='BTC',policy_id='SYSTEM_LOGIC_PROTOCOL_V1',
                capabilities=capabilities,initial_inventory={'UP':40.,'DOWN':0.},initial_cost=16.)
        def new(k,pid,route,p,q):return PlanAction('NEW',k,pid,route,p,q)
        def commit(g,decision,actions,continuation='REOBSERVE_OWN_STATE_KEEP_SAME_RESPONSIBILITY'):
            return g.commit(g.propose(decision,actions,continuation),now_ms=1000,market_end_ms=300000)
        def sent(g,*keys):
            for k in keys:g.record_send(k,'SENT',evidence='SYNTHETIC_TRANSPORT_ACK')
        def receipt(g,k,q,p,terminal=False):
            return g.receipt(k,filled=q,payment=p,terminal=terminal,evidence='SYNTHETIC_FAULT_INJECTION_RECEIPT_NOT_MARKET_FILL')
        def finish(name,g,notes):
            g.ledger.invariants()
            filename=name+'.json'
            payload=dict(scope='SYNTHETIC_PROTOCOL_NOT_PNL',events=g.events,final=g.own_state(),notes=notes)
            (out/filename).write_text(json.dumps(payload,indent=2),encoding='utf-8')
            result['scenarios'].append(dict(name=name,events=len(g.events),checks_complete=True,
                remaining_reservations=sum(c.reserved_qty for c in g.ledger.carriers.values()),
                trace=filename,trace_sha256=sha(out/filename),notes=notes))
            print(json.dumps(dict(scenario=name,stage='complete',checks=len(result['checks']))),flush=True)

        # A: plan-level legality differs from legality of isolated child decisions.
        g=gateway();before=g.snapshot_id()
        commit(gateway(),'single-p',[new('p',1,'PASSIVE',.4,30.)])
        commit(gateway(),'single-a',[new('a',1,'ACTIVE',.45,30.)])
        reject('A_individually_legal_children_rejected_as_overreserved_joint_plan',
            lambda:commit(g,'overlap',[new('p',1,'PASSIVE',.4,30.),new('a',1,'ACTIVE',.45,30.)]),'shared quantity')
        check('A_failed_joint_plan_has_no_half_commit',g.snapshot_id()==before and not g.ledger.carriers)
        check('A_no_false_realized_safety_before_commit',g.own_state()['floor']==-16.)
        event=commit(g,'parallel',[new('p',1,'PASSIVE',.4,30.),new('a',1,'ACTIVE',.45,25.),new('u',2,'PASSIVE',.45,20.)])
        check('A_repair_and_expand_allowed_concurrently',len(g.ledger.carriers)==3 and g.ledger.account(1)['repair_remaining']==30.)
        check('A_pending_is_not_realized_repair',g.own_state()['floor']==-16. and g.ledger.account(1)['repair_paid']==0.)
        check('A_local_atomic_reservation_not_atomic_venue_execution',event['venue_atomic'] is False)
        sent(g,'p','a','u');receipt(g,'a',10.,4.5);receipt(g,'p',15.,6.)
        check('A_two_routes_pay_one_shared_repair_debt',g.ledger.account(1)['repair_paid']==25. and g.ledger.account(1)['repair_remaining']==5.)
        check('A_expansion_owner_survives_unfinished_other_repair',g.transport['u']=='SENT' and g.ledger.account(1)['repair_remaining']>0)
        receipt(g,'a',25.,11.25,True);receipt(g,'p',30.,12.,True);receipt(g,'u',20.,9.,True)
        check('A_composite_fills_repair_first_authorized_overflow_second',g.ledger.account(1)['repair_paid']==30. and g.ledger.account(1)['add_filled']==25.)
        check('A_all_reservations_reconciled',all(c.reserved_qty==0 for c in g.ledger.carriers.values()))
        check('A_no_new_objective_or_budget_created',len(g.ledger.grants)==2 and g.ledger.capital==84.)
        finish('A_CONCURRENT_SHARED_PLAN',g,['Independent legality is insufficient for a multi-order plan.',
            'All numeric quantities/costs are disclosed fixtures, not Target policy constants.'])

        # B: partial -> cancel request -> racing fill -> terminal -> same family continuation.
        g=gateway();commit(g,'passive',[new('p',1,'PASSIVE',.3,55.)]);sent(g,'p')
        receipt(g,'p',5.,1.5)
        stale=g.propose('stale-keep',[PlanAction('KEEP','p')],'REOBSERVE')
        commit(g,'cancel',[PlanAction('CANCEL','p')]);receipt(g,'p',8.,2.4)
        reject('B_stale_proposal_rejected_after_new_feedback',lambda:g.commit(stale,now_ms=1000,market_end_ms=300000),'STALE_OWN_STATE')
        check('B_cancel_pending_retains_residual47',g.ledger.account(1)['reserved_qty']==47.)
        before=g.snapshot_id()
        reject('B_active_cannot_double_reserve_pending_residual',lambda:commit(g,'too-early',[new('a',1,'ACTIVE',.35,47.)]),'shared quantity')
        check('B_failed_relay_preserves_state',g.snapshot_id()==before)
        receipt(g,'p',8.,2.4,True)
        check('B_terminal_releases_only_reconciled_residual',g.ledger.account(1)['reserved_qty']==0. and g.ledger.account(1)['repair_remaining']==22.)
        before=g.snapshot_id();receipt(g,'p',8.,2.4,True)
        check('B_duplicate_terminal_no_extra_payment_or_credit',g.snapshot_id()==before)
        remaining=g.ledger.account(1)['repair_remaining']+g.ledger.account(1)['add_remaining']
        commit(g,'relay',[new('a',1,'ACTIVE',.35,remaining)])
        sent(g,'a');receipt(g,'a',47.,16.45,True)
        check('B_route_change_keeps_objective_and_generation',g.ledger.carriers['a'].parent_id==g.ledger.carriers['p'].parent_id==1)
        check('B_repair_and_expand_completion_exact',g.ledger.account(1)['repair_remaining']==0. and g.ledger.account(1)['add_remaining']==0.)
        check('B_no_fixed_delay_or_Nfail_trigger_in_plan',remaining==47. and g.active_continuation=='REOBSERVE_OWN_STATE_KEEP_SAME_RESPONSIBILITY')
        finish('B_PARTIAL_CANCEL_ROUTE_CONTINUATION',g,['Next plan uses confirmed residual, not initial requested quantity.',
            'No latency or probability inference from synthetic receipt ordering.'])

        # C: physical sends cannot share the atomicity of a local reservation transaction.
        g=gateway();commit(g,'joint',[new('p',1,'PASSIVE',.4,30.),new('u',2,'PASSIVE',.45,20.),new('a',1,'ACTIVE',.45,25.)])
        g.record_send('p','SENT',evidence='SYNTHETIC_TRANSPORT_ACK')
        g.record_send('u','NOT_SENT',evidence='SYNTHETIC_PRE_SEND_FAILURE_PROVENANCE')
        g.record_send('a','UNKNOWN',evidence='SYNTHETIC_TRANSPORT_RESPONSE_LOST')
        check('C_only_proven_unsent_reservation_released',g.ledger.account(2)['reserved_qty']==0. and g.ledger.account(1)['reserved_qty']==55.)
        reject('C_unknown_send_cannot_be_rolled_back_as_unsent',lambda:g.record_send('a','NOT_SENT',evidence='invalid guess'),'cannot declare existing')
        receipt(g,'p',6.,2.4)
        check('C_partial_execution_preserved_after_sibling_failure',g.own_state()['inventory']['DOWN']==6.)
        bad=replace(g.propose('mixed',[PlanAction('KEEP','p')],'OTHER_CONTINUATION'),policy_id='UNDECLARED_OLD_CONTROLLER')
        reject('C_mixed_policy_fragment_rejected',lambda:g.commit(bad,now_ms=1000,market_end_ms=300000),'MIXED_POLICY')
        missing=g.propose('missing',[PlanAction('KEEP','p')],'')
        reject('C_continuation_identity_mandatory',lambda:g.commit(missing,now_ms=1000,market_end_ms=300000),'CONTINUATION_REQUIRED')
        before=g.snapshot_id()
        reject('C_ungranted_objective_cannot_be_created_by_policy',lambda:commit(g,'fake',[new('z',99,'ACTIVE',.2,2.)]),'NO_ECONOMIC_AUTHORITY')
        check('C_invalid_objective_does_not_change_actual_state',g.snapshot_id()==before)
        commit(g,'replan',[PlanAction('CANCEL','p'),PlanAction('KEEP','a')]);receipt(g,'p',8.,3.2,True)
        receipt(g,'a',0.,0.,True)
        check('C_unknown_route_waits_for_canonical_zero_terminal',g.ledger.account(1)['reserved_qty']==0.)
        check('C_realized_fills_are_not_erased_by_replanning',g.own_state()['inventory']['DOWN']==8. and g.ledger.account(1)['repair_paid']==8.)
        limited=gateway(('PASSIVE',));snapshot=limited.snapshot_id()
        reject('C_missing_active_rejects_whole_plan_not_silent_partial_or_HOLD',
            lambda:commit(limited,'missing-channel',[new('p',1,'PASSIVE',.4,30.),new('a',1,'ACTIVE',.45,25.)]),'UNSUPPORTED_COMPLETE_PLAN_NOT_HOLD')
        check('C_missing_channel_leaves_no_half_plan',snapshot==limited.snapshot_id())
        late=gateway();proposal=late.propose('late-expand',[new('u',2,'PASSIVE',.45,20.)],'REOBSERVE')
        reject('C_explicit_OUR_late_speculative_fence_not_relaxed',lambda:late.commit(proposal,now_ms=200000,market_end_ms=300000),'NO_NEW_SPECULATIVE')
        finish('C_SEND_FAILURE_REPLAN_AND_CAPABILITY',g,['Reservation atomicity must not erase SENT/UNKNOWN/filled orders.',
            'Current Minimal native profile lacks Active; this gateway does not supply that adapter.'])
        check('all_three_complete_protocol_chains_exercised',len(result['scenarios'])==3)
        check('existing_sizing_and_grant_modules_remain_reused',len(result['loaded_sources'])>=5)
        assert not any(n.startswith(('hftbacktest','torch','sklearn','numpy')) for n in sys.modules)
        result.update(verdict='SYSTEM_PLAN_AND_FEEDBACK_PROTOCOL_SMOKE3_PASS_NOT_NATIVE_TRAINING',
            full_system_training_gate='NOT_READY_NATIVE_POLICY_CONSUMER_AND_ECONOMIC_LABELS_MISSING',
            novelty='One policy/state/continuation identity and jointly validated action transaction around existing primitives.',
            old_primitives_not_new_economic_discovery=True,
            next='Research-local full-plan native consumer and identity replay, not repeated classifier or quota tests.',
            limitations=['Synthetic fault injection only; not Target imitation or market payoff evidence.',
                'Three chains are not three real market episodes.',
                'No joint-policy model was fitted and no native controller was modified.',
                'No proof that all local changes fail or that joint policies necessarily improve economics.',
                'Local reservation validation cannot make exchange multi-order execution atomic.'])
    except Exception as exc:
        result.update(verdict='SYSTEM_PLAN_PROTOCOL_ERROR_STOPPED',error=type(exc).__name__+': '+str(exc),
            traceback=traceback.format_exc(limit=10))
    result['checks_passed']=len(result['checks']);result['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['verdict','checks_passed','elapsed_seconds']}),flush=True)
    if result['verdict']=='SYSTEM_PLAN_PROTOCOL_ERROR_STOPPED':raise SystemExit(2)


if __name__=='__main__':main()
