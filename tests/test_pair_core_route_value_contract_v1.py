from copy import deepcopy
import math
import unittest
from tools.pair_core_route_value_contract_v1 import describe,payoff


def frame():
    return dict(t=1,owners=[],fifo={'UP':[[10.,.4]],'DOWN':[]},
                inventory={'UP':10.,'DOWN':0.},cost=4.,
                qv={'UP':{'ask':.5},'DOWN':{'ask':.7}})


class RouteValueContractTests(unittest.TestCase):
    def test_four_channels_without_collapsing_role_and_route(self):
        for role,purpose in [('SATELLITE_EXPAND','ADD'),('SATELLITE_REPAIR','REPAIR')]:
            d=describe(frame(),'DOWN',role,.6,2)
            self.assertEqual(d['passive']['channel'],'PASSIVE_'+purpose)
            self.assertEqual(d['active']['channel'],'ACTIVE_'+purpose)
    def test_same_quantity_premium_costs_both_endpoints(self):
        for side in ('UP','DOWN'):
            d=describe(frame(),side,'SATELLITE_EXPAND',.3,2)
            a=d['active'];self.assertEqual(a['qty'],d['passive']['qty'])
            for value in a['conditionalPayoffDifference'].values():
                self.assertAlmostEqual(value,-a['premiumOverPassive'])
    def test_active_partial_repair_can_help_floor_while_costing_more(self):
        d=describe(frame(),'DOWN','ECONOMIC_CORE',.6,2)
        self.assertGreater(d['active']['conditionalFillFloorChange'],0)
        self.assertFalse(d['active']['inheritedPairPricePass'])
        self.assertFalse(d['active']['withinOriginalQuotedTicket'])
    def test_fifo_quantity_is_not_manager_debt_or_authorized_add(self):
        d=describe(frame(),'DOWN','SATELLITE_REPAIR',.6,12)
        self.assertEqual(d['active']['fifoMatchedQtyIfOnlyThisFills'],10)
        self.assertEqual(d['active']['fifoUnmatchedQtyIfOnlyThisFills'],2)
        self.assertIsNone(d['managerDebt']);self.assertIsNone(d['authorizedAddQty'])
        self.assertFalse(d['counterfactualValueIdentified'])
    def test_cancel_pending_keeps_cross_and_occupancy(self):
        f=frame();f['owners']=[dict(key='old',side='UP',price=.5,remaining=2,status='NEW',cancelRequested=True)]
        d=describe(f,'DOWN','SATELLITE_REPAIR',.4,2)
        self.assertEqual(d['active']['potentialCrossOwners'],['old'])
        self.assertEqual(d['physicalOccupied'],1)
    def test_seed_is_not_silently_called_add(self):
        self.assertEqual(describe(frame(),'UP','PROBE_CORE',.4,2)['purpose'],'SEED')
    def test_input_is_unchanged(self):
        f=frame();saved=deepcopy(f);describe(f,'DOWN','ECONOMIC_CORE',.6,2)
        self.assertEqual(f,saved)
    def test_bad_prices_rejected_and_missing_ask_is_not_option(self):
        with self.assertRaises(ValueError):payoff('UP',math.nan,2)
        f=frame();f['qv']['UP']['ask']=None
        self.assertIsNone(describe(f,'UP','SATELLITE_EXPAND',.4,2)['active'])


if __name__=='__main__':unittest.main()
