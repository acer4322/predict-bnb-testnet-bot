"""Bind existing single-submit dispatch to the two V27 maintenance jobs."""
import argparse
import inspect
import json
import run_btc5m_commitment_repair_worker_v1 as original
from prepare_btc5m_repair_maintenance_scope_v1 import config,R,read,dump_for,ARMS


def bound(arm):
    c=config(arm);source=inspect.getsource(original);source=source[:source.index("\nif __name__=='__main__':")]
    assert source.count('commitment_repair_wave_20260913.json')==2
    source=source.replace('commitment_repair_wave_20260913.json',c['WAVE'])
    ns=dict(__name__='bound_maintenance_worker');exec(compile(source,'existing_dispatch_operations','exec'),ns)
    ns.update(c,dump=lambda suffix,obj:dump_for(c['STEM'],suffix,obj));return ns


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('arm',choices=ARMS);p.add_argument('action',choices=('preflight','submit','status','collect'));a=p.parse_args()
    if a.action=='submit' and a.arm=='legal':assert read(R/(config('raw')['STEM']+'_RESULT.json'))['verification']=='PASS'
    ns=bound(a.arm);result=ns['dispatch'].cmd_status(ns['HOST'],ns['JOB']) if a.action=='status' else ns[a.action]()
    print(json.dumps(result))
