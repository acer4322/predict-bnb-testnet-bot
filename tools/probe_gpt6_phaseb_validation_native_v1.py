"""Read-only substrate parity probe for the fixed first validation market."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--bundle',required=True)
    ap.add_argument('--model-dir',required=True)
    ap.add_argument('--market',type=int,default=1829471)
    ap.add_argument('--source-dir',default='..')
    a=ap.parse_args()
    sys.path.insert(0,str(Path.cwd()))
    p=Path(a.source_dir)/'train_management_v1_current_v3b_execution_world.py'
    spec=importlib.util.spec_from_file_location('native_world_probe',p)
    world=importlib.util.module_from_spec(spec);spec.loader.exec_module(world)
    old=[json.loads(x) for x in (Path(a.model_dir)/'training_rows.jsonl').read_text().splitlines()]
    old=[r for r in old if r['marketId']==a.market]
    print(json.dumps({'market':a.market,'stage':'native_original_training_class','referenceRows':len(old)}),flush=True)
    with tempfile.TemporaryDirectory(prefix='phaseb_native_probe_') as td:
        with zipfile.ZipFile(a.bundle) as z:z.extract(f'tapes/{a.market}.json.xz',td)
        sim=world.TrainingTraceSim(Path(td)/'tapes'/f'{a.market}.json.xz')
        try:
            r=sim.run_qty('__UNSCORED__');new=sim.finalize_labels()
        finally:sim.close()
    import numpy as np
    keys=world.STATE+world.ACTION+['role','side','route']+[f'{k}{h}s' for k in ('anyFill','fillQty','repairPayQty','overflowQty','cancelReq','terminal') for h in (3,5)]
    ix={(x['t'],x['key']):x for x in old};mismatch=[]
    for row in new:
        prior=ix.get((row['t'],row['key']))
        if prior is None:mismatch.append({'key':row['key'],'missing':True});continue
        for k in keys:
            equal=np.isclose(row[k],prior[k],rtol=1e-11,atol=1e-9) if isinstance(row[k],(int,float)) else row[k]==prior[k]
            if not equal:mismatch.append({'key':row['key'],'field':k,'actual':row[k],'reference':prior[k]})
    out={'market':a.market,'newRows':len(new),'referenceRows':len(old),'mismatches':mismatch,'parity':len(new)==len(old) and not mismatch,'ledger':r['quantityLedgerSummary']}
    (Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json').write_text(json.dumps(out,indent=2))
    print(json.dumps({'market':a.market,'parity':out['parity'],'mismatches':len(mismatch)}),flush=True)

if __name__=='__main__':main()
