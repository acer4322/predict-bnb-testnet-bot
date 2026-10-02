import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from tools.pair_core_research_scope_preflight_v1 import validate,PROFILE_DEVIATIONS,RUNTIME_CHECKS,NATIVE


class ScopePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'frozen.py').write_text('x=1\n',encoding='utf-8')
        self.d={'profile':'X_CUTOFF_SEPARATE_POOL','acknowledged_deviations':sorted(PROFILE_DEVIATIONS['X_CUTOFF_SEPARATE_POOL']),
          'claims':['MECHANISM_ONLY','DESCRIPTIVE_TARGET_COMPARISON'],'native_sha256':NATIVE,
          'required_runtime_checks':sorted(RUNTIME_CHECKS),'target_runtime_inputs':False,'preserve_original_artifacts':True,
          'frozen_sources':{'frozen.py':hashlib.sha256((self.root/'frozen.py').read_bytes()).hexdigest()}}
    def tearDown(self):self.tmp.cleanup()
    def test_declared_restricted_diagnostic_passes(self):self.assertEqual(validate(self.d,self.root),[])
    def test_missing_180_deviation_blocks(self):
        self.d['acknowledged_deviations'].remove('ALL_ROLE_180_CUTOFF')
        self.assertTrue(any('UNDECLARED' in e for e in validate(self.d,self.root)))
    def test_source_mutation_blocks(self):
        (self.root/'frozen.py').write_text('x=2\n')
        self.assertTrue(any('HASH_DRIFT' in e for e in validate(self.d,self.root)))
    def test_unknown_native_blocks(self):
        self.d['native_sha256']='0'*64;self.assertTrue(validate(self.d,self.root))
    def test_whole_system_claim_on_one_shot_blocks(self):
        self.d['claims']=['WHOLE_LIFECYCLE_READINESS'];self.assertTrue(validate(self.d,self.root))
    def test_exact_target_claim_blocks(self):
        self.d['claims']=['FULL_TARGET_RULE_EQUIVALENCE'];self.assertTrue(validate(self.d,self.root))
    def test_net_promotion_claim_blocks(self):
        self.d['claims']=['NET_PROFITABILITY_PROMOTION'];self.assertTrue(validate(self.d,self.root))
    def test_missing_runtime_observations_blocks(self):
        self.d['required_runtime_checks']=[];self.assertTrue(validate(self.d,self.root))
    def test_unknown_new_profile_requires_audit(self):
        self.d['profile']='INVENTED_FULL_MANAGER';self.assertTrue(validate(self.d,self.root))
    def test_target_runtime_authority_blocks(self):
        self.d['target_runtime_inputs']=True;self.assertTrue(validate(self.d,self.root))
    def test_path_escape_blocks(self):
        self.d['frozen_sources']={'../outside.py':'0'*64}
        self.assertTrue(any('ESCAPES' in e for e in validate(self.d,self.root)))


if __name__=='__main__':unittest.main()
