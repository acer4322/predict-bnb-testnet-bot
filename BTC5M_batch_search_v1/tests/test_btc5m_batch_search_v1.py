"""Local synthetic/component tests. No market replay, fitting, or remote calls."""
import copy
import importlib
import json
import math
import os
import sys
import tempfile
import types
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
LIB=ROOT/'tools/btc5m_batch_search_lib'
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(LIB))
from btc5m_batch_search_lib import engine as e
from btc5m_batch_search_lib.attempt_memory import AttemptMemory,curve,effective_config,extra_cost
from btc5m_batch_search_lib import prepare as prep


def spec(count=32):
    return dict(seed=20260921,trials=count,startup=6,exploration_probability=.25,objectives=['floor','upside'],
                space={'family':dict(type='choice',values=['a','b','c']),
                       'weight':dict(type='log_float',low=.25,high=4.),
                       'offset':dict(type='float',low=-1.,high=1.)})


def finish(study):
    row=study.ask();study.begin(row['number'])
    x=row['params']['weight'];y=row['params']['offset']
    study.tell(row['number'],[-(x-.8)**2-y*y,-(x-2.)**2-.5*y*y],dict(synthetic=True))
    return row


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'STUDY.json'

    def test_128_points_in_range_unique(self):
        s=e.Study(self.path,spec(128))
        for _ in range(128):
            row=finish(s);e.validate_parameters(s.spec['space'],row['params'])
        self.assertEqual(128,len({r['parameter_hash'] for r in s.trials}))
        self.assertTrue(any(r['sampling']=='PARETO_NEIGHBOR' for r in s.trials))

    def test_resume_exact_rng_and_history(self):
        a=e.Study(self.path,spec());[finish(a) for _ in range(9)]
        b=e.Study(self.path,spec());[finish(b) for _ in range(23)]
        c=e.Study(Path(self.temp.name)/'other.json',spec());[finish(c) for _ in range(32)]
        self.assertEqual(b.data,c.data)

    def test_json_key_order_does_not_change_sampling(self):
        a=e.Study(self.path,spec());[finish(a) for _ in range(12)]
        reordered=json.loads(json.dumps(spec(),sort_keys=True))
        b=e.Study(Path(self.temp.name)/'reordered.json',reordered);[finish(b) for _ in range(12)]
        self.assertEqual(a.trials,b.trials)

    def test_pending_proposal_is_persisted_and_reused(self):
        a=e.Study(self.path,spec());row=a.ask()
        b=e.Study(self.path,spec());self.assertEqual(row,b.ask());self.assertEqual(1,len(b.trials))

    def test_running_requires_explicit_recovery(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0)
        with self.assertRaises(RuntimeError):e.Study(self.path,spec())
        recovered=e.Study(self.path,spec(),recover_interrupted=True)
        self.assertEqual('PENDING',recovered.trials[0]['state'])
        self.assertEqual(1,recovered.trials[0]['attempts'])
        recovered.begin(0);self.assertEqual(2,recovered.trials[0]['attempts'])

    def test_no_parallel_inflight(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0)
        with self.assertRaises(RuntimeError):s.ask()

    def test_no_duplicate_tell(self):
        s=e.Study(self.path,spec());finish(s)
        with self.assertRaises(RuntimeError):s.tell(0,[1.,2.],{})

    def test_tell_before_begin_rejected(self):
        s=e.Study(self.path,spec());s.ask()
        with self.assertRaises(RuntimeError):s.tell(0,[1.,2.],{})

    def test_nan_objective_rejected_without_completion(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0)
        with self.assertRaises(ValueError):s.tell(0,[float('nan'),1.],{})
        self.assertEqual('RUNNING',s.trials[0]['state'])

    def test_nan_metric_rejected_before_mutation(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0)
        with self.assertRaises(ValueError):s.tell(0,[1.,2.],{'bad':float('nan')})
        self.assertEqual('RUNNING',s.trials[0]['state'])

    def test_objective_dimension_checked(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0)
        with self.assertRaises(ValueError):s.tell(0,[1.],{})

    def test_failed_trial_not_selected_as_parent(self):
        s=e.Study(self.path,spec());s.ask();s.begin(0);s.fail(0,'synthetic failure')
        for _ in range(31):finish(s)
        self.assertNotIn(0,[r['parent_trial'] for r in s.trials])
        self.assertNotIn(0,s.shortlist(3))

    def test_all_parents_completed_before_child(self):
        s=e.Study(self.path,spec());[finish(s) for _ in range(32)]
        for row in s.trials:
            if row['parent_trial'] is not None:
                self.assertLess(row['parent_trial'],row['number'])
                self.assertIn(row['parent_trial'],row['completed_results_used'])

    def test_budget_exhaustion(self):
        s=e.Study(self.path,spec(1));finish(s)
        with self.assertRaises(StopIteration):s.ask()

    def test_different_spec_cannot_resume(self):
        e.Study(self.path,spec());other=spec();other['seed']+=1
        with self.assertRaises(ValueError):e.Study(self.path,other)

    def test_saved_parameter_corruption_rejected(self):
        s=e.Study(self.path,spec());s.ask()
        data=e.read_json(self.path);data['trials'][0]['params']['weight']=.3;e.atomic_json(self.path,data)
        with self.assertRaises(ValueError):e.Study(self.path,spec())

    def test_pareto_keeps_tradeoff_and_drops_dominated(self):
        rows=[dict(number=i,state='COMPLETE',objectives=x) for i,x in enumerate([[5.,1.],[1.,5.],[0.,0.]])]
        self.assertEqual([0,1],[r['number'] for r in e.pareto(rows)])

    def test_ties_do_not_dominate(self):
        self.assertFalse(e.dominates([1.,1.],[1.,1.]))

    def test_pareto_dimension_error(self):
        with self.assertRaises(ValueError):e.dominates([1.],[1.,2.])

    def test_invalid_range_rejected(self):
        for low,high in [(1.,1.),(4.,1.),(float('nan'),2.),(0.,1.)]:
            bad=spec();bad['space']['weight'].update(low=low,high=high)
            with self.subTest(low=low,high=high),self.assertRaises(ValueError):e.validate_spec(bad)

    def test_no_eval_formula_strings(self):
        bad=spec();bad['space']['unsafe']=dict(type='python',source='raise RuntimeError')
        with self.assertRaises(ValueError):e.validate_spec(bad)

    def test_invalid_choice_rejected(self):
        bad=spec();bad['space']['family']['values']=['a','a']
        with self.assertRaises(ValueError):e.validate_spec(bad)

    def test_writer_lock_exclusive_and_released(self):
        p=Path(self.temp.name)/'writer.lock'
        with e.WriterLock(p):
            with self.assertRaises(RuntimeError):
                with e.WriterLock(p):pass
        with e.WriterLock(p):pass

    def test_atomic_write_keeps_previous_on_replace_failure(self):
        e.atomic_json(self.path,{'v':1})
        with patch.object(e.os,'replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):e.atomic_json(self.path,{'v':2})
        self.assertEqual({'v':1},e.read_json(self.path))


class MemoryTests(unittest.TestCase):
    def setUp(self):self.m=AttemptMemory(lambda q,p:0.)
    def add(self,key='1',side='UP',qty=15.,route='PASSIVE',birth=0,price=.4):
        self.m.register(key,dict(side=side,qty=qty,route=route,price=price),{'now_ms':birth})
    def fill(self,key='1',seq=1,qty=15.,side=1,price=.4,ms=1000,maker=True):
        r=dict(sequence=seq,order_id=int(key),qty=qty,side=side,price=price,receive_ts=ms*1000000,maker=maker)
        self.m.consume([r],ms);return r
    def view(self,ms=3000):return self.m.snapshot(dict(now_ms=ms,remaining_seconds=300-ms/1000))
    def open_and_attempt(self):
        self.add();self.fill();self.add('2','DOWN',15.,'ACTIVE',2000,.55)

    def test_unknown_is_not_confirmed_zero(self):
        self.open_and_attempt();self.assertFalse(self.m.terminal('2','UNKNOWN',0.,2500))
        self.assertEqual(0,self.view()['confirmed_zero_attempts'])
        self.assertEqual(1,self.view()['pending_attempts_not_zero'])

    def test_cancel_pending_is_not_terminal(self):
        self.open_and_attempt();self.m.terminal('2','CANCEL_PENDING',0.,2500)
        self.assertEqual(0,self.view()['confirmed_zero_attempts'])

    def test_terminal_zero_is_recorded_once(self):
        self.open_and_attempt();self.assertTrue(self.m.terminal('2','EXPIRED',0.,2500))
        self.assertFalse(self.m.terminal('2','EXPIRED',0.,2600))
        self.assertEqual(1,self.view()['confirmed_zero_attempts'])

    def test_partial_not_zero(self):
        self.open_and_attempt();self.fill('2',2,5.,-1,.45,2200,False)
        self.m.terminal('2','EXPIRED',5.,2500)
        self.assertEqual(0,self.view()['confirmed_zero_attempts'])
        self.assertEqual(1,self.view()['partial_terminals'])

    def test_full_terminal_inconsistent_zero_rejected(self):
        self.open_and_attempt()
        with self.assertRaises(ValueError):self.m.terminal('2','FILLED',0.,2500)

    def test_conflicting_duplicate_terminal_rejected(self):
        self.open_and_attempt();self.m.terminal('2','EXPIRED',0.,2500)
        with self.assertRaises(ValueError):self.m.terminal('2','EXPIRED',1.,2500)

    def test_receipt_idempotent(self):
        self.add();r=self.fill();self.m.consume([r],1000)
        self.assertEqual(15.,self.view()['sides']['UP']['open_net_qty'])

    def test_conflicting_receipt_rejected(self):
        self.add();r=self.fill();r=dict(r,qty=14.)
        with self.assertRaises(ValueError):self.m.consume([r],1000)

    def test_future_receipt_rejected(self):
        self.add();r=dict(sequence=1,order_id=1,qty=15.,side=1,price=.4,maker=True,receive_ts=4000*1000000)
        with self.assertRaises(ValueError):self.m.consume([r],3000)

    def test_receipt_before_order_birth_rejected(self):
        self.add(birth=2000)
        with self.assertRaises(ValueError):self.fill(ms=1000)

    def test_each_partial_fill_has_actual_age(self):
        self.add(qty=10.);self.fill(qty=5.,ms=1000);self.fill(seq=2,qty=5.,ms=2000)
        self.assertEqual(1.5,self.view()['sides']['UP']['weighted_fill_age_seconds'])

    def test_failure_only_targets_existing_lots(self):
        self.add();self.add('2','DOWN',15.,'ACTIVE',500,.55)
        self.m.terminal('2','EXPIRED',0.,700);self.fill(ms=1000)
        self.assertEqual(0.,self.view()['sides']['UP']['failed_coverage_per_open_share'])

    def test_failed_attempt_capacity_respected(self):
        self.add(qty=30.);self.fill(qty=30.);self.add('2','DOWN',5.,'ACTIVE',2000,.55)
        self.m.terminal('2','EXPIRED',0.,2500)
        self.assertAlmostEqual(5/30,self.view()['sides']['UP']['failed_coverage_per_open_share'])

    def test_closed_lots_clear_failure_signal(self):
        self.open_and_attempt();self.m.terminal('2','EXPIRED',0.,2500)
        self.add('3','DOWN',15.,'ACTIVE',3000,.55);self.fill('3',2,15.,-1,.45,4000,False)
        self.assertEqual(0.,self.view(4000)['sides']['UP']['failed_coverage_per_open_share'])
        self.assertEqual(1,self.view(4000)['confirmed_zero_attempts'])

    def test_net_share_fee_not_gross_used(self):
        self.m=AttemptMemory(lambda q,p:q*.02)
        self.add(route='ACTIVE');self.fill(maker=False)
        self.assertAlmostEqual(14.7,self.view()['sides']['UP']['open_net_qty'])

    def test_authoritative_fifo_mismatch_rejected(self):
        self.add();self.fill();model=types.SimpleNamespace(queues={'UP':[],'DOWN':[]})
        with self.assertRaises(ValueError):self.m.snapshot(dict(now_ms=3000,remaining_seconds=297.),model)

    def test_lookahead_metadata_ignored(self):
        self.open_and_attempt();self.m.terminal('2','EXPIRED',0.,2500)
        a=dict(now_ms=3000,remaining_seconds=297.);b=dict(a,winner='UP',target='BUY',future_book=[1,2,3])
        self.assertEqual(self.m.snapshot(a),self.m.snapshot(b))

    def test_snapshot_rejects_future_terminal(self):
        self.open_and_attempt();self.m.terminal('2','EXPIRED',0.,4000)
        with self.assertRaises(ValueError):self.view()

    def test_history_changes_soft_cost_not_hardban(self):
        self.open_and_attempt();before=self.view()['sides']['UP'];self.m.terminal('2','EXPIRED',0.,2500)
        after=self.view()['sides']['UP'];c=dict(history='attempts',curve='linear',failure_weight=1.,age_weight=1.)
        self.assertEqual(0.,extra_cost(c,before,.02,15.));self.assertAlmostEqual(.3,extra_cost(c,after,.02,15.))
        self.assertTrue(math.isfinite(extra_cost(c,after,.02,15.)))

    def test_curve_families(self):
        self.assertEqual(4.,curve(4.,'linear'));self.assertEqual(2.,curve(4.,'sqrt'));self.assertEqual(.8,curve(4.,'saturating'))

    def test_curve_invalid_rejected(self):
        for value in (-1.,float('inf')):
            with self.assertRaises(ValueError):curve(value,'linear')
        with self.assertRaises(ValueError):curve(1.,'execute_python')

    def test_inactive_parameter_explicit_zero(self):
        c=dict(history='attempts',curve='linear',failure_weight=1.,age_weight=4.)
        self.assertEqual(0.,effective_config(c)['age_weight']);self.assertEqual(4.,c['age_weight'])

    def test_zero_new_risk_no_cost(self):
        c=dict(history='attempts_age',curve='sqrt',failure_weight=4.,age_weight=4.)
        s=dict(failed_coverage_per_open_share=5.,fill_age_over_remaining_horizon=3.)
        self.assertEqual(0.,extra_cost(c,s,.1,0.))


class IntegrationComponentTests(unittest.TestCase):
    def test_load_only_batch_checks(self):
        from btc5m_batch_search_lib.component_checks import run
        result=run();self.assertEqual('PASS',result['status']);self.assertEqual(0,result['native_executed'])

    def test_default_spec_supported(self):
        value=e.read_json(LIB/'default_spec.json');e.validate_spec(value)
        self.assertIsNone(value['scope']['capital_cap']);self.assertFalse(value['scope']['live_authority'])
        self.assertEqual(128,value['trials'])

    def test_native_worker_refuses_this_host(self):
        from btc5m_batch_search_lib import worker_template as w
        with patch.object(w.socket,'gethostname',return_value='NOT_THE_WORKER'):
            with self.assertRaises(RuntimeError):w.check_package()

    def test_pending_payoff_projection_not_free_hedge(self):
        from btc5m_batch_search_lib.worker_template import summarize
        o=dict(payoff={'UP':10.,'DOWN':-2.},owners=[dict(side='UP',qty=15.,limit=.4)],capital_cap=None,cost=10.)
        t=dict(final={'observation':o},replay={'status':'PASS'},decisions=[dict(ms=1000,state={'elapsed_seconds':1.},executed=[])],
               events=[],online_matches=[],net_pairing={'paired_surplus':1.},residual_cost=3.,unresolved=1,receipts=1,new_orders=1,
               endpoint_reaches_expiry=True,censored=None,negative_floor_area=5.,worst_floor=-2.,peak_committed_cash=16.,active_outcomes={})
        result=summarize(t);self.assertEqual(-8.,result['pending_conservative_floor']);self.assertFalse(result['closed'])

    def test_patch_uses_canonical_terminal_anchor_only(self):
        text="""def example():
    if True:
        if True:
            if True:
                w.terminal(key,status[int(o.status)],float(o.qty-o.leaves_qty))
    row=dict(responsibility_observer=policy.last_observer);records.append(row)
    return dict(public_summary=policy.public_cost.snapshot(),x=1)
"""
        new=prep.patch_loop(text)
        self.assertIn('policy.observe_terminal',new)
        self.assertIn('attempt_observer=policy.last_attempt_view',new)
        self.assertIn('attempt_summary=',new)
        with self.assertRaises(ValueError):prep.patch_loop(new)

    def test_patch_drift_rejected(self):
        with self.assertRaises(ValueError):prep.patch_loop('print("different version")')

    def test_completed_artifact_hash_checked(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'file.py').write_text('x=1')
            e.atomic_json(p/'MANIFEST.json',dict(version='R88_BATCH_SEARCH_V1',files={'file.py':e.file_hash(p/'file.py')}))
            prep.verify_package(p);(p/'file.py').write_text('x=2')
            with self.assertRaises(ValueError):prep.verify_package(p)

    def test_package_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'secret').write_text('x');p=root/'package';p.mkdir()
            e.atomic_json(p/'MANIFEST.json',dict(version='R88_BATCH_SEARCH_V1',files={'../secret':e.file_hash(root/'secret')}))
            with self.assertRaises(ValueError):prep.verify_package(p)

    def test_overlay_active_gain_and_legality_delegated(self):
        # Minimal stand-in verifies overlay routing, not actual R87 integration.
        class Base:
            def __init__(self,arm,latency):
                self.arm=arm;self.entry_seconds=latency/1000.;self.cost_model=types.SimpleNamespace(queues={'UP':[],'DOWN':[]})
            def gain(self,*args,**kwargs):return 7.
            def allowed(self,*args,**kwargs):return 'INHERITED_LEGALITY'
        modules={'controller_r87':types.SimpleNamespace(Controller=Base),
                 'net_world':types.SimpleNamespace(share_fee=lambda q,p:0.),
                 'responsibility':types.SimpleNamespace(prospective=lambda *a,**k:{'new_unpaired_same_side':15.})}
        with patch.dict(sys.modules,modules):
            name='_batch_overlay_test'
            loader=importlib.util.spec_from_file_location(name,LIB/'controller_overlay.py')
            module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
            c=module.Controller(dict(history='attempts_age',curve='linear',failure_weight=4.,age_weight=4.),250)
            self.assertEqual(7.,c.gain({},'UP',15.,.4,'ACTIVE'))
            self.assertEqual('INHERITED_LEGALITY',c.allowed())


if __name__=='__main__':unittest.main(verbosity=2)
