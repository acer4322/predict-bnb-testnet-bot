import unittest
from types import SimpleNamespace
from tools.run_root_dual_legal_label_smoke_v1 import ReadOnlyBackend, preview_copy, signature


class DualLegalTests(unittest.TestCase):
    def test_preview_cannot_submit_or_cancel(self):
        bt=ReadOnlyBackend(SimpleNamespace(current_timestamp=12,submit_buy_order=lambda:None))
        self.assertEqual(bt.current_timestamp,12)
        with self.assertRaises(RuntimeError):bt.submit_buy_order()
        with self.assertRaises(RuntimeError):bt.cancel()

    def test_clone_preserves_aliases_without_mutating_source(self):
        class FakeSim:
            pass
        a=FakeSim()
        a.bt=SimpleNamespace(current_timestamp=12)
        a.x={'remaining':3}
        a._lab={'notCopied':True}
        a.y=[a.x]
        before=signature(a)
        b=preview_copy(a); b.x['remaining']=1
        self.assertEqual(b.y[0]['remaining'],1)
        self.assertEqual(a.x['remaining'],3)
        self.assertEqual(signature(a),before)
        self.assertFalse(hasattr(b,'_lab'))

    def test_signature_covers_authority_not_lab_diagnostics(self):
        a=SimpleNamespace(credit=1,_lab={'branch':'N'})
        b=SimpleNamespace(credit=1,_lab={'branch':'S'})
        self.assertEqual(signature(a),signature(b))
        b.credit=2
        self.assertNotEqual(signature(a),signature(b))


if __name__=='__main__':unittest.main()
