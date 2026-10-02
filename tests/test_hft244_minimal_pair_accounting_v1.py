import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import hft244_minimal_pair_accounting_v1 as adapter


class MinimalIdentityTests(unittest.TestCase):
    def test_original_cell_identity(self):
        path = Path(__file__).resolve().parents[1]/'tools/run_eth_role_separated_minimal_pair_safety_smoke.py'
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MinimalPairRoleSim')
        self.assertEqual(ast.unparse(cls.bases[0]),'v3.RoleSeparatedMultiSlotSim')
        self.assertIn("('B_PAIR_ONLY_MAX4',False)",source)
        self.assertNotIn('r247',source.lower())
        init = next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
        self.assertEqual([ast.literal_eval(n) for n in init.args.defaults],[4,False])

    def test_physical_only_install(self):
        class Sim:
            def __init__(self): self.bt=object()
        base = SimpleNamespace(Sim=Sim,ex=SimpleNamespace(advance_to=None))
        with patch.dict(os.environ,{'BTC5M_LAN_RESULT_DIR':'unit-fixture'}), patch.object(adapter,'Reader',return_value='reader'):
            adapter.install(base,'not-a-native-binary')
            sim = Sim()
            self.assertEqual(sim._receipt_reader,'reader')
            self.assertFalse(sim._receipt_invalid)
            self.assertIs(base.Sim.process,adapter.physical_process)
            self.assertIs(base.ex.advance_to,adapter.strict_advance_to)
            for name in ('scopeSide','serviceLedger','repairLots','_receipt_v2_process'):
                self.assertFalse(hasattr(sim,name))
            with self.assertRaises(RuntimeError): adapter.install(base,'unused')

    def test_local_install_forbidden(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError): adapter.install(None,None)


if __name__=='__main__': unittest.main()
