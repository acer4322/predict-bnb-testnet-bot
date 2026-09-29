from __future__ import annotations
import argparse,json,os,sys,lzma
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import importlib.util
v6p=ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_pairgroup_v13.py'
spec=importlib.util.spec_from_file_location('r4_v13',v6p); v6=importlib.util.module_from_spec(spec); spec.loader.exec_module(v6)
CFGS=('CONT_STATE_H2_SUSPEND','CONT_STATE_H2_SUSPEND_PAIRGROUP')

def load_market(root:Path,mid:int):
    cand=list((root/'data/hft_forward_paper_v1/markets').glob(f'{mid}_r2_hft_closed_loop_v1.json.xz'))
    if not cand:return None
    with lzma.open(cand[0],'rt',encoding='utf-8') as f:return json.load(f)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);a=ap.parse_args()
    root=Path(a.data_root).resolve()
    v6.ROOT=root; v6.PUBLIC_SOURCE_DB=Path(r'C:\BTC5M-worker\data\public_source_snapshot_archive_v2.db')
    if hasattr(v6.tape,'ARCHIVE_DIR'): v6.tape.ARCHIVE_DIR=root/'data/execution_tape_v1/markets'
    # v6 uses ROOT for tapes and output-independent source reads.
    ids=[int(x) for x in a.ids.split(',') if x.strip()];rows=[];errors=[]
    for mid in ids:
        d=load_market(root,mid)
        if d is None:
            errors.append({'marketId':mid,'error':'MARKET_FILE_MISSING'});continue
        for cfg in CFGS:
            try:
                r=v6.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errors.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'marketId':mid,'rows':len(rows),'errors':len(errors)}),flush=True)
    rep={'version':'R4_CONTINUOUS_PAIRGROUP_AUTHSAFE_V13_FROZEN_IDS','researchOnly':True,'winnerUsed':False,'ids':ids,'configs':CFGS,'rows':rows,'errors':errors}
    p=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':len(rows),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
