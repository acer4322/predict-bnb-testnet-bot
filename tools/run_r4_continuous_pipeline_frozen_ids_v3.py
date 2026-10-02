from __future__ import annotations
import argparse,json,lzma,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import importlib.util
_v5p=ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_pipeline_authoritysafe_v5.py' if (ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_pipeline_authoritysafe_v5.py').exists() else ROOT/'tools/test_r4_maker_continuous_pipeline_authoritysafe_v5.py'
_spec=importlib.util.spec_from_file_location('r4_v5_frozen',_v5p); v5=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v5)

def configure_data_root(data_root:Path):
    data_root=data_root.resolve(); v5.ROOT=data_root
    v5.PUBLIC_SOURCE_DB=ROOT/'data/public_source_snapshot_archive_v2.db'
    if hasattr(v5.tape,'ARCHIVE_DIR'): v5.tape.ARCHIVE_DIR=data_root/'data/execution_tape_v1/markets'

def load_market(data_root:Path,mid:int):
    base=data_root/'data/hft_forward_paper_v1/markets'
    p=base/f'{mid}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists():
        hits=list(base.glob(f'{mid}*r2*.json.xz')) if base.exists() else []
        raise FileNotFoundError(f'paper_missing:{p};baseExists={base.exists()};hits={[x.name for x in hits]}')
    with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
    if int(d.get('marketId') or 0)!=mid: raise RuntimeError('market_id_mismatch')
    return d

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);a=ap.parse_args()
    dr=Path(a.data_root);configure_data_root(dr);ids=[int(x) for x in a.ids.split(',') if x.strip()]
    rows=[];errors=[]
    for mid in ids:
        try:d=load_market(dr,mid)
        except Exception as e:
            errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});continue
        for cfg in ('FLEX_W5000','CONT_STATE_PIPELINE'):
            try:r=v5.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errors.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'marketId':mid,'rows':len(rows),'errors':len(errors)}),flush=True)
    rep={'version':'R4_CONTINUOUS_PIPELINE_AUTHSAFE_V5_FROZEN_IDS_DIRECT_V3','researchOnly':True,'winnerUsed':False,'ids':ids,'dataRoot':str(dr),'rows':rows,'errors':errors}
    p=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':len(rows),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
