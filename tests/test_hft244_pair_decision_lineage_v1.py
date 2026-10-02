import copy
import unittest
from tools.hft244_pair_decision_lineage_v1 import Recorder, categories, make_sim, pure_quotes


def frame(t=1, ready=False):
    return dict(t=t, tick=t, qv={'imb': 0.}, postReady=ready, state={'owners': []},
                roles=[], candidates=[], submits=[], cancels=[], receipts=[], expiryEligible=[])


class DecisionLineageTests(unittest.TestCase):
    def test_pure_quote_no_mutation_and_average_pair(self):
        book={'bids': {.6: 2, .4: 2}, 'asks': {.7: 3, .8: 2}}
        lots={'UP': [], 'DOWN': [[3, .5]]}; owners=[]
        before=copy.deepcopy((book,lots,owners))
        q=pure_quotes(book,lots,owners)
        self.assertEqual(q['UP']['first'], [.4, 2.5, None])
        self.assertEqual((book,lots,owners),before)

    def test_pending_owner_occupies_quote_and_capacity(self):
        owners=[dict(side='UP',price=.5) for _ in range(4)]
        q=pure_quotes({'bids': {.5: 2, .4: 2},'asks': {.7: 1}}, {'UP': [],'DOWN': []},owners)
        self.assertEqual(q['UP']['first'][0], .4)
        self.assertFalse(q['UP']['hasPhysicalSlot'])

    def test_control_frames_are_frozen(self):
        r=Recorder(keep=True);f=frame();r.append(f);f['qv']['imb']=1
        self.assertEqual(r.frames[0]['qv']['imb'],0)

    def test_prefix_tamper_stops(self):
        c=Recorder(keep=True);c.append(frame())
        t=Recorder(reference=c.frames);f=frame();f['state']['owners'].append({'side':'UP'})
        with self.assertRaisesRegex(ValueError,'prefix'):
            t.append(f)

    def test_clock_misalignment_stops(self):
        c=Recorder(keep=True);c.append(frame())
        with self.assertRaisesRegex(ValueError,'clock'):
            Recorder(reference=c.frames).append(frame(2))

    def test_capacity_before_candidate_not_pair_failure(self):
        c=frame(2,True);t=copy.deepcopy(c)
        for f in (c,t): f['roles']=[dict(scope='CONTINUATION',decision=['UP','ECONOMIC_CORE',True,False])]
        c['candidates']=[dict(side='UP',value=[.5,2,None])]
        t['state']['owners']=[{'side':'UP'}]*4
        self.assertEqual(categories(c,t),['PHYSICAL_CAPACITY'])

    def test_receipt_only_is_not_role_change(self):
        c=frame(2,True);t=copy.deepcopy(c);t['receipts']=[{'qty':1}]
        r=Recorder(reference=[c]);r.append(t)
        self.assertIsNone(r.first_endogenous)
        self.assertEqual(r.counts['NON_DIRECT_RECEIPT_DIVERGENCE'],1)

    def test_bounded_discrepancies_and_missing_tail(self):
        refs=[frame(t,True) for t in range(30)]
        r=Recorder(reference=refs)
        for f in refs:
            t=copy.deepcopy(f);t['cancels']=[{'reason':'test'}];r.append(t)
        self.assertEqual(len(r.first),24)
        self.assertEqual(r.summary()['differingClocks'],30)
        with self.assertRaisesRegex(ValueError,'suffix'):
            Recorder(reference=refs).summary()

    def test_wrapper_calls_mutating_candidate_once(self):
        class Parent:
            def _candidate_from_levels(self,*args):
                self.count+=1
                return (.5,2.,None)
        s=object.__new__(make_sim(Parent));s.count=0
        s._probe_trace_frame={'state':{'quoteMenu':{'UP':{'first':[.5,2.,None]}}},'candidates':[]}
        self.assertEqual(s._candidate_from_levels('UP'),(.5,2.,None))
        self.assertEqual(s.count,1)

    def test_native_ids_do_not_define_cross_branch_origin(self):
        Sim=make_sim(object);a=object.__new__(Sim);b=object.__new__(Sim)
        a.orders={'UP_1':dict(n=1,placed=12,side='UP',price=.5,qty=2)}
        b.orders={'UP_2':dict(n=2,placed=12,side='UP',price=.5,qty=2)}
        a.key_role={'UP_1':'ECONOMIC_CORE'};b.key_role={'UP_2':'ECONOMIC_CORE'}
        self.assertEqual(a._trace_origin('UP_1'),b._trace_origin('UP_2'))


if __name__=='__main__':unittest.main()
