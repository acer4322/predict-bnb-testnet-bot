from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
import importlib.util
_v5p=ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_pipeline_authoritysafe_v5.py' if (ROOT/'.lan_worker_v1/staging/test_r4_maker_continuous_pipeline_authoritysafe_v5.py').exists() else ROOT/'tools/test_r4_maker_continuous_pipeline_authoritysafe_v5.py'
_spec=importlib.util.spec_from_file_location('r4_v5_frozen',_v5p); v5=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(v5)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);a=ap.parse_args()
    ids=[int(x) for x in a.ids.split(',') if x.strip()]
    pool=v2.choose_files(400)
    by={int(d['marketId']):d for d in pool}
    rows=[];errors=[]
    for mid in ids:
        d=by.get(mid)
        if d is None:
            errors.append({'marketId':mid,'error':'MARKET_NOT_AVAILABLE_IN_FROZEN_SOURCE_POOL'});continue
        for cfg in ('FLEX_W5000','CONT_STATE_PIPELINE'):
            try:
                r=v5.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:
                errors.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'marketId':mid,'rows':len(rows),'errors':len(errors)}),flush=True)
    rep={'version':'R4_CONTINUOUS_PIPELINE_AUTHSAFE_V5_FROZEN_IDS','researchOnly':True,'winnerUsed':False,'ids':ids,'rows':rows,'errors':errors}
    p=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(rep,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':len(rows),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
