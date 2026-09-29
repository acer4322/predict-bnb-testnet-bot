"""Synthetic contract tests only; no network, HFT or model work."""
import copy
import tempfile
import unittest
from pathlib import Path
import research_case_batch_v1 as batch


class CaseBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.spec = dict(version=batch.VERSION, execution_identity={'policy_sha':'a','data_sha':'b'},
            arms=[{'id':'A','config':{'feature':False}},{'id':'B','config':{'feature':True}}],
            cases=[{'id':'C0','parameters':[0]},{'id':'C1','parameters':[1]}], max_new_evaluations=4)
        self.calls = []

    def execute(self, cell, directory):
        self.calls.append(cell['id'])
        path = directory/'evidence.json'
        batch.atomic_json(path, {'cell':cell['id'],'identity':cell['identity']})
        return {'metrics':{'value':len(self.calls)},'artifacts':{'evidence.json':batch.file_hash(path)}}

    def test_full_cartesian_product_is_executed(self):
        state=batch.run_matrix(self.spec,self.out,self.execute)
        self.assertEqual(self.calls,['A__C0','A__C1','B__C0','B__C1'])
        self.assertEqual(state['new_evaluations'],4)
        self.assertEqual(len(state['cells']),4)

    def test_complete_reload_reuses_without_execution(self):
        first=batch.run_matrix(self.spec,self.out,self.execute)
        again=batch.run_matrix(self.spec,self.out,lambda *a: self.fail('Unexpected rerun'))
        self.assertEqual(first,again)

    def test_verified_external_reuse(self):
        def reuse(cell,d):
            if cell['id']!='A__C0': return None
            p=d/'source.json';batch.atomic_json(p,{'original':True})
            return dict(identity=cell['identity'],metrics={'value':7},
                artifacts={'source.json':batch.file_hash(p)},source={'job':'completed-parent'})
        state=batch.run_matrix(self.spec,self.out,self.execute,reuse)
        self.assertEqual(state['new_evaluations'],3)
        self.assertEqual(state['reused_cells'],1)
        self.assertEqual(state['cells']['A__C0']['metrics']['value'],7)

    def test_reuse_identity_mismatch_fails_closed(self):
        def reuse(cell,d):
            return dict(identity='wrong',metrics={},artifacts={},source={})
        with self.assertRaises(ValueError): batch.run_matrix(self.spec,self.out,self.execute,reuse)
        self.assertEqual(self.calls,[])

    def test_failure_is_preserved_and_not_retried(self):
        def fail(cell,d):
            (d/'prefix.txt').write_text('partial receipt prefix')
            raise RuntimeError('native rejection')
        with self.assertRaises(RuntimeError):batch.run_matrix(self.spec,self.out,fail)
        state=batch.read_json(self.out/'BATCH_STATE.json')
        self.assertEqual(state['cells']['A__C0']['state'],'FAILED_NEEDS_REVIEW')
        with self.assertRaises(RuntimeError):batch.run_matrix(self.spec,self.out,self.execute)
        self.assertFalse(self.calls)
        self.assertTrue((self.out/'cells/A__C0/prefix.txt').exists())

    def test_interrupted_running_cell_is_not_retried(self):
        cell=batch.cells(self.spec)[0]
        state=dict(version=batch.VERSION,spec_hash=batch.digest(self.spec),
            cells={cell['id']:{'state':'RUNNING'}},new_evaluations=1,reused_cells=0)
        batch.atomic_json(self.out/'BATCH_STATE.json',state)
        with self.assertRaises(RuntimeError):batch.run_matrix(self.spec,self.out,self.execute)
        self.assertFalse(self.calls)

    def test_tampered_evidence_is_rejected(self):
        batch.run_matrix(self.spec,self.out,self.execute)
        (self.out/'cells/A__C0/evidence.json').write_text('changed')
        with self.assertRaises(ValueError):batch.run_matrix(self.spec,self.out,self.execute)
        self.assertEqual(len(self.calls),4)

    def test_changed_context_requires_new_matrix(self):
        batch.run_matrix(self.spec,self.out,self.execute)
        self.spec['execution_identity']['policy_sha']='new'
        with self.assertRaises(ValueError):batch.run_matrix(self.spec,self.out,self.execute)

    def test_duplicate_ids_rejected(self):
        self.spec['cases'].append(copy.deepcopy(self.spec['cases'][0]))
        with self.assertRaises(ValueError):batch.cells(self.spec)

    def test_untrusted_identifier_rejected(self):
        self.spec['arms'][0]['id']='../outside'
        with self.assertRaises(ValueError):batch.cells(self.spec)

    def test_budget_stops_before_extra_execution(self):
        self.spec['max_new_evaluations']=1
        with self.assertRaises(RuntimeError):batch.run_matrix(self.spec,self.out,self.execute)
        self.assertEqual(len(self.calls),1)

    def test_nonfinite_metrics_rejected(self):
        def bad(cell,d):
            r=self.execute(cell,d);r['metrics']['value']=float('nan');return r
        with self.assertRaises(ValueError):batch.run_matrix(self.spec,self.out,bad)

    def test_outside_artifact_rejected(self):
        outside=self.out/'outside.txt';outside.write_text('x')
        with self.assertRaises(ValueError):
            batch.verify_payload({'metrics':{},'artifacts':{'../outside.txt':batch.file_hash(outside)}},self.out/'cells')

    def test_progress_matches_saved_completion(self):
        progress=[]
        batch.run_matrix(self.spec,self.out,self.execute,on_progress=progress.append)
        self.assertEqual(progress[-1]['phase'],'MATRIX_COMPLETE')
        self.assertEqual(progress[-1]['completed'],4)
        self.assertEqual(progress[-1]['total'],4)

    def test_parameter_change_alters_execution_identity(self):
        old=batch.cells(self.spec)[0]['identity']
        self.spec['arms'][0]['config']['feature']=True
        self.assertNotEqual(old,batch.cells(self.spec)[0]['identity'])

    def test_missing_artifacts_cannot_count_as_pass(self):
        with self.assertRaises(ValueError):
            batch.verify_payload({'metrics':{'score':10},'artifacts':{}},self.out)


if __name__=='__main__':
    unittest.main(verbosity=2)
