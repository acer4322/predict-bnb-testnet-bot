from __future__ import annotations
import argparse,os,runpy,sys,tempfile,zipfile
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--code-bundle',required=True);ap.add_argument('--entry',required=True);a,rest=ap.parse_known_args()
    root=Path(tempfile.mkdtemp(prefix='btc5m_code_snapshot_'))
    zipfile.ZipFile(a.code_bundle).extractall(root)
    os.chdir(root)
    sys.path.insert(0,str(root))
    sys.argv=[str(root/a.entry),*rest]
    runpy.run_path(str(root/a.entry),run_name='__main__')
if __name__=='__main__':main()
