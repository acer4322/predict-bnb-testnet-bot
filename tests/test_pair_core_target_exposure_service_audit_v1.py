import unittest
from tools.pair_core_target_exposure_service_audit_v1 import analyze


def row(i,t,side,q,p,route='MAKER',order=None):
    return dict(id=i,event_ms=t,observed_at_ms=t+8000,side=side,shares=q,price=p,
                role=route,quote_type='BID',order_hash=order or str(i))


class ExposureServiceAuditTests(unittest.TestCase):
    def test_partial_paid_repair_leaves_both_residual_and_negative_floor(self):
        r=analyze([row(1,1,'UP',10,.4),row(2,2,'DOWN',2,.7,'TAKER')],0,300000)
        c=r['counts'];self.assertEqual(c['afterAccumulationFloorIMPROVE'],1)
        self.assertEqual(c['activeImprovementLeavesDirectionalResidual'],1)
        self.assertEqual(c['activeImprovementLeavesNegativeFloor'],1)
        self.assertAlmostEqual(r['activeBatches'][0]['activeFloorChange'],.6)
    def test_active_risk_increase_is_not_repair(self):
        r=analyze([row(1,1,'UP',10,.4),row(2,2,'UP',2,.5,'TAKER')],0,300000)
        self.assertEqual(r['counts']['underwaterActiveFloorWORSEN'],1)
        self.assertEqual(r['counts'].get('activeFloorIMPROVE',0),0)
    def test_same_timestamp_uses_common_prestate_not_invented_order(self):
        rows=[row(1,1,'UP',10,.4),row(2,2,'DOWN',2,.7,'TAKER'),row(3,2,'UP',10,.5)]
        a=analyze(rows,0,300000);b=analyze(list(reversed(rows)),0,300000)
        self.assertEqual(a,b);e=a['activeBatches'][0]
        self.assertGreater(e['activeFloorChange'],0)
        self.assertLess(min(e['afterJoint'].values()),min(e['before'].values()))
    def test_parent_split_conserves_route_attribution(self):
        a=analyze([row(1,1,'UP',10,.4),row(2,2,'DOWN',2,.7,'TAKER','same')],0,300000)
        b=analyze([row(1,1,'UP',10,.4),row(2,2,'DOWN',1,.7,'TAKER','same'),row(3,2,'DOWN',1,.7,'TAKER','same')],0,300000)
        self.assertEqual(a,b)
    def test_no_later_active_is_retained_not_claimed_repaired(self):
        r=analyze([row(1,1,'UP',10,.4)],0,300000)
        self.assertEqual(r['counts']['makerGrowthBatchesWithoutSubsequentActive'],1)
        self.assertIsNone(r['firstActivePhase'])
    def test_same_timestamp_route_bundle_not_sum_of_parent_floor_changes(self):
        # Starting flat: each buy alone lowers floor, their cheap pair improves it.
        r=analyze([row(1,1,'UP',2,.4,'TAKER'),row(2,1,'DOWN',2,.4,'TAKER')],0,300000)
        self.assertEqual(r['counts']['activeFloorIMPROVE'],1)
        self.assertAlmostEqual(r['activeBatches'][0]['activeFloorChange'],.4)
    def test_sell_is_cash_and_inventory_conserving(self):
        sell=row(2,2,'UP',2,.6,'TAKER');sell['quote_type']='ASK'
        r=analyze([row(1,1,'UP',10,.4),sell],0,300000)
        self.assertAlmostEqual(r['endpoints']['UP'],5.2)
        self.assertAlmostEqual(r['endpoints']['DOWN'],-2.8)
        self.assertEqual(r['counts']['activeFloorIMPROVE'],1)


if __name__=='__main__':unittest.main()
