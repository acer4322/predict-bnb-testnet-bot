"""Public-only publishing must not accidentally include worker/private artifacts."""
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/sync_research_pack.py'


class PublicOnlyPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='public_pack_test_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.books = self.root / 'books'; self.books.mkdir()
        self.worker = self.root / 'worker'; self.worker.mkdir()
        (self.worker / 'result.json').write_text('{"market_id":123,"api_key":"test-decoy-only"}')
        self.book = self.books / 'public_123.json.gz'
        self.book.write_bytes(gzip.compress(json.dumps({'market':{'market_id':123},'books':[]}).encode(),mtime=0))
        self.label = self.root / 'labels.json'
        self.label.write_text('{"records":[{"market_id":123,"winner":"UP"}]}')
        self.out = self.root / 'out'

    def run_pack(self, *extra):
        return subprocess.run([sys.executable,str(SCRIPT),str(self.worker),'--public-only',
            '--public-root',str(self.books),'--markets','123','--out',str(self.out),*extra],capture_output=True,text=True)

    def test_only_public_and_explicit_labels_are_copied(self):
        r=self.run_pack('--extra',str(self.label)); self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertNotIn('FLAGGED',r.stdout)
        paths=sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob('*') if p.is_file())
        self.assertEqual(paths,['INDEX.json','labels/labels.json','public_markets/123/public_123.json.gz'])
        self.assertEqual((self.out/'public_markets/123/public_123.json.gz').read_bytes(),self.book.read_bytes())

    def test_dry_run_creates_no_export(self):
        r=self.run_pack('--extra',str(self.label),'--dry-run'); self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(self.out.exists())

    def test_missing_market_and_label_abort_before_copy(self):
        self.book.unlink();r=self.run_pack('--dry-run');self.assertEqual(r.returncode,2)
        self.assertFalse(self.out.exists())
        self.book.write_bytes(gzip.compress(b'{"market":{"market_id":123},"books":[]}'))
        r=self.run_pack('--extra',str(self.root/'missing.json'));self.assertEqual(r.returncode,2)
        self.assertFalse(self.out.exists())

    def test_size_and_flag_guards_apply_to_public_only(self):
        r=self.run_pack('--max-mb','0.000001');self.assertEqual(r.returncode,3)
        self.assertFalse(self.out.exists())
        self.label.write_text('{"Authorization":"test-decoy-only"}')
        r=self.run_pack('--extra',str(self.label));self.assertEqual(r.returncode,2)
        self.assertIn('FLAGGED',r.stdout);self.assertFalse(self.out.exists())


if __name__=='__main__': unittest.main()
