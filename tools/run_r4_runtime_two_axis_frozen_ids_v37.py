from __future__ import annotations
import argparse,json,os,sys,lzma
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import importlib.util
vp=ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_runtime_two_axis_v37.py'
spec=importlib.util.spec_from_file_location('r4_v37',vp); v=importlib.util.module_from_spec(spec); spec.loader.exec_module(v)
CFGS=('CONT_STATE_H2_SUSPEND_RUNTIME_TWO_AXIS_TOUCH1',)

def load_market(root:Path,mid:int):
    p=root/'data/hft_forward_paper_v1/markets'/f'{mid}_r2_hft_closed_loop_v1.json.xz'
    if not p.exists(): return None
    with lzma.open(p,'rt',encoding='utf-8') as f:return json.load(f)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--seam-map',required=True);ap.add_argument('--public-map',required=True);a=ap.parse_args()
    root=Path(a.data_root).resolve(); em=json.loads(Path(a.seam_map).read_text(encoding='utf-8'))
    # Seam map supplies only the development-time exact first-late anchor (t/logical). Eligibility is recomputed in runtime.
    v.ROOT=root; v.EXACT_EVENT_MAP=em.get('events') or em
    pm=json.loads(Path(a.public_map).read_text(encoding='utf-8')).get('rows') or {}
    v.public_source_rows=lambda mid: ([(int(pm[str(mid)]['sampledAtMs']),pm[str(mid)]['snapshot'])] if str(mid) in pm else [])
    if hasattr(v.tape,'ARCHIVE_DIR'): v.tape.ARCHIVE_DIR=root/'data/execution_tape_v1/markets'
    ids=[int(x) for x in a.ids.split(',') if x.strip()];rows=[];errors=[]
    for mid in ids:
        d=load_market(root,mid)
        if d is None: errors.append({'marketId':mid,'error':'MARKET_FILE_MISSING'});continue
        for cfg in CFGS:
            try:r=v.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errors.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'marketId':mid,'rows':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
    rep={'version':'R4_RUNTIME_TWO_AXIS_V37_FROZEN_IDS','researchOnly':True,'strictPastRuntimeEligibility':True,'winnerUsed':False,'eligibleMapUsed':False,'ids':ids,'configs':CFGS,'rows':rows,'errors':errors}
    p=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':len(rows),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
