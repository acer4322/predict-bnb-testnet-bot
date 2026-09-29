from __future__ import annotations
import json,sys
from copy import deepcopy
from pathlib import Path
import numpy as np, torch
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.train_eth_passive_lifecycle_memory_feasibility_v2 as v2
import tools.smoke_eth_passive_lifecycle_memory_feasibility_v2_local as smoke

def thin(z,n=3000):
    if len(z)<=n:return z
    idx=np.linspace(0,len(z)-1,n).astype(int)
    return [z[int(i)] for i in idx]

def truncate_history(rows, keep_last):
    out=deepcopy(rows)
    if keep_last>=v2.SEQ:return out
    for r in out:
        m=r['mask'].copy(); s=r['seq'].copy()
        valid=np.where(m>0)[0]
        if len(valid)>keep_last:
            drop=valid[:-keep_last] if keep_last>0 else valid
            m[drop]=0; s[drop]=0
        r['mask']=m; r['seq']=s
    return out

def run_variant(base_tr,base_te,keep_last):
    tr=truncate_history(base_tr,keep_last); te=truncate_history(base_te,keep_last)
    v2.standardize(tr,te)
    torch.manual_seed(v2.SEED); np.random.seed(v2.SEED)
    mm=smoke.quick_mem(v2.Memory(),tr,'cpu')
    return v2.evaluate(mm,te,'cpu',True)

def main():
    db=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
    rows,cut,nwin=v2.build(str(db)); tr=[r for r in rows if r['end']<cut]; te=[r for r in rows if r['end']>=cut]
    tr=thin(tr); te=thin(te)
    results={}
    for k in (1,8): results[f'last{k}']=run_variant(tr,te,k)
    out={'version':'ETH_PASSIVE_LIFECYCLE_MEMORY_DEPTH_ABLATION_V3_LOCAL','formalPassEligible':False,'trainRows':len(tr),'testRows':len(te),'chronologyCutoff':cut,'variants':results}
    p=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_passive_lifecycle_memory_depth_ablation_v3_local.json';p.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
