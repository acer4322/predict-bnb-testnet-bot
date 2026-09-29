"""Reuse verified dispatch operations, bound to one preregistered V25 arm."""
import argparse
import inspect
import json
import run_btc5m_commitment_repair_worker_v1 as original
from prepare_btc5m_repair_constraints_v1 import config,R,read,dump_for


def bound(arm):
    cfg=config(arm);source=inspect.getsource(original)
    source=source[:source.index("\nif __name__=='__main__':")]
    assert source.count('commitment_repair_wave_20260913.json')==2
    source=source.replace('commitment_repair_wave_20260913.json',cfg['WAVE'])
    namespace=dict(__name__='bounded_constraints_worker')
    exec(compile(source,'reuse_existing_named_dispatch','exec'),namespace)
    namespace.update(cfg,dump=lambda suffix,obj:dump_for(cfg['STEM'],suffix,obj))
    return namespace


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('arm',choices=('concurrent','quote','both'))
    p.add_argument('action',choices=('preflight','submit','status','collect'));a=p.parse_args()
    if a.action=='submit':
        order=['concurrent','quote','both']
        for before in order[:order.index(a.arm)]:
            assert read(R/(config(before)['STEM']+'_RESULT.json'))['verification']=='PASS', 'Audit previous arm before another submit'
    ns=bound(a.arm)
    result=ns['dispatch'].cmd_status(ns['HOST'],ns['JOB']) if a.action=='status' else ns[a.action]()
    print(json.dumps(result))
