"""Bind the existing verified single-submit dispatcher to the V31 job."""
import argparse
import inspect
import json
import run_btc5m_commitment_repair_worker_v1 as original
from prepare_btc5m_held_amplitude_v1 import PACKAGE,JOB,STEM,WAVE,dump


def bound():
    source=inspect.getsource(original);source=source[:source.index("\nif __name__=='__main__':")]
    assert source.count('commitment_repair_wave_20260913.json')==2
    source=source.replace('commitment_repair_wave_20260913.json',WAVE)
    ns=dict(__name__='bound_held_amplitude_worker');exec(compile(source,'existing_dispatch_operations','exec'),ns)
    ns.update(PACKAGE=PACKAGE,JOB=JOB,STEM=STEM,dump=dump);return ns


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('preflight','submit','status','collect'));a=p.parse_args()
    ns=bound();result=ns['dispatch'].cmd_status(ns['HOST'],ns['JOB']) if a.action=='status' else ns[a.action]()
    print(json.dumps(result))
