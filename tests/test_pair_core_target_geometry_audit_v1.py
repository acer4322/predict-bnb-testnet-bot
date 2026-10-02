import unittest
from tools.pair_core_target_geometry_audit_v1 import geometry


def row(i,side,qty,price,t=1,route='MAKER',quote='BID',parent='p'):
    return dict(id=i,side=side,shares=qty,price=price,event_ms=t,observed_at_ms=t+2000,
                role=route,quote_type=quote,order_hash=parent)


class GeometryTests(unittest.TestCase):
    def test_parent_fanout(self):
        g=geometry([row(1,'UP',1,.4),row(2,'UP',2,.4)])
        self.assertEqual(g['parentEvents'],1);self.assertAlmostEqual(g['endpoints']['UP'],1.8)

    def test_sale_cash(self):
        g=geometry([row(1,'UP',2,.4),row(2,'UP',1,.7,2,quote='ASK')])
        self.assertAlmostEqual(g['endpoints']['UP'],.9);self.assertAlmostEqual(g['endpoints']['DOWN'],-.1)

    def test_simultaneous_not_fake_sequential_repair(self):
        g=geometry([row(1,'UP',1,.4),row(2,'DOWN',1,.4,parent='q')])
        self.assertEqual(g['counts']['MAKER_floorWorsening'],2)
        self.assertAlmostEqual(g['endpoints']['UP'],.2)

    def test_opposite_is_not_private_debt(self):
        g=geometry([row(1,'UP',2,.4),row(2,'DOWN',1,.5,2,route='TAKER')])
        self.assertEqual(g['counts']['TAKER_floorImproving'],1)
        self.assertAlmostEqual(g['route']['TAKER']['DOWN'],.5)

    def test_unknown_rejected(self):
        with self.assertRaises(AssertionError):geometry([row(1,'UNKNOWN',1,.4)])


if __name__=='__main__':unittest.main()
