from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np, torch
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.train_eth_passive_lifecycle_memory_feasibility_v2 as v2

def thin(z,n=25000):
    if len(z)<=n:return z
    idx=np.linspace(0,len(z)-1,n).astype(int)
    return [z[int(i)] for i in idx]

def quick_static(m,tr,dev):
    m.to(dev); opt=torch.optim.AdamW(m.parameters(),lr=1.5e-3,weight_decay=1e-4); rng=np.random.default_rng(v2.SEED); pools={t:v2.arrays(tr,t) for t in v2.TASKS}
    for _ in range(2):
        for __ in range(20):
            opt.zero_grad(); L=0
            for t in v2.TASKS:
                X,_,_,y=pools[t]; idx=rng.integers(0,len(y),size=min(256,len(y))); xt=torch.from_numpy(X[idx]); yt=torch.from_numpy(y[idx]); pos=max(float(yt.mean()),1e-4); L+=torch.nn.functional.binary_cross_entropy_with_logits(m(xt,t),yt,pos_weight=torch.tensor((1-pos)/pos).clamp(.25,4))
            L.backward();opt.step()
    return m

def quick_mem(m,tr,dev):
    m.to(dev); opt=torch.optim.AdamW(m.parameters(),lr=1.2e-3,weight_decay=1e-4); rng=np.random.default_rng(v2.SEED); pools={t:v2.arrays(tr,t) for t in v2.TASKS}
    for _ in range(2):
        for __ in range(20):
            opt.zero_grad(); L=0
            for t in v2.TASKS:
                X,S,M,y=pools[t]; idx=rng.integers(0,len(y),size=min(192,len(y))); xt=torch.from_numpy(X[idx]); st=torch.from_numpy(S[idx]); mt=torch.from_numpy(M[idx]); yt=torch.from_numpy(y[idx]); pos=max(float(yt.mean()),1e-4); L+=torch.nn.functional.binary_cross_entropy_with_logits(m(xt,st,mt,t),yt,pos_weight=torch.tensor((1-pos)/pos).clamp(.25,4))
            L.backward();torch.nn.utils.clip_grad_norm_(m.parameters(),5);opt.step()
    return m

def main():
    db=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
    rows,cut,nwin=v2.build(str(db)); tr=[r for r in rows if r['end']<cut]; te=[r for r in rows if r['end']>=cut]; tr=thin(tr,8000); te=thin(te,8000); v2.standardize(tr,te)
    sm=quick_static(v2.Static(),tr,'cpu'); mm=quick_mem(v2.Memory(),tr,'cpu'); a=v2.evaluate(sm,te,'cpu',False); b=v2.evaluate(mm,te,'cpu',True); delta={t:b[t]['auc']-a[t]['auc'] for t in v2.TASKS}
    out={'version':'ETH_PASSIVE_LIFECYCLE_MEMORY_V2_LOCAL_SMOKE','formalPassEligible':False,'trainRows':len(tr),'testRows':len(te),'static':a,'memory':b,'delta':delta}
    p=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_passive_lifecycle_memory_v2_local_smoke.json';p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
