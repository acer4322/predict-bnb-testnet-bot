from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import build_eth_target_favorable_pair_safe_surplus_v1 as base
EPS=1e-9

def post_safe_labels(row,states):
    t=row['t'];pre=row['pre'];f30=[s for s in states if s['t']>=t and s['t']<=t+30000]
    if not f30:f30=[row['post']]
    val=np.nan
    if pre['floor']>=-EPS and row['rel'] in (-1,0):
        val=float(all(s['floor']>=-EPS for s in f30) and max(s['best'] for s in f30)>pre['best']+EPS)
    # Keep legacy slots stable; V1 trainer only consumes slot 3 as post-safe surplus value.
    old=base._ORIG_FUTURE_LABELS(row,states) if hasattr(base,'_ORIG_FUTURE_LABELS') else (np.nan,)*6
    return old[0],old[1],old[2],val,old[4],old[5]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--cutoff',type=int,default=1823545);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output',required=True);a=ap.parse_args()
    if not hasattr(base,'_ORIG_FUTURE_LABELS'):base._ORIG_FUTURE_LABELS=base.future_labels
    base.future_labels=post_safe_labels
    rows,ms=base.build(a.db,a.cutoff,a.max_markets)
    X=np.asarray([r['x'] for r in rows],np.float32);y=np.asarray([r['y'][3] for r in rows],np.float32);mid=np.asarray([r['market'] for r in rows],np.int32);end=np.asarray([r['end'] for r in rows],np.int64);rel=np.asarray([r['rel'] for r in rows],np.int8)
    f=base.FEATURES;ix={k:i for i,k in enumerate(f)};floor=X[:,ix['floor_ratio']];absr=X[:,ix['absnet_ratio']];side=X[:,ix['candidate_side_up']]
    side_mask=(floor>=-EPS)&(rel==0)&(absr<=.02)
    value_mask=np.isfinite(y)
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);np.savez_compressed(op,X=X,y_value=y,y_side=side,side_mask=side_mask.astype(np.int8),value_mask=value_mask.astype(np.int8),market_id=mid,end_ms=end,relation=rel)
    meta={'version':'ETH_TARGET_POST_SAFE_SURPLUS_TEACHER_V1_DATASET','cutoff':a.cutoff,'maxMarkets':a.max_markets,'features':f,'rows':int(len(X)),'markets':int(len(set(map(int,mid.tolist())))) if len(mid) else 0,'valueSupport':int(value_mask.sum()),'valuePositiveRate':float(np.nanmean(y)) if value_mask.any() else None,'balancedSideSupport':int(side_mask.sum()),'balancedSideUpRate':float(np.mean(side[side_mask])) if side_mask.any() else None,'boundary':['marketId<=cutoff','Target actual Maker fills only','winner/PnL absent','post-safe value uses future path only as offline label','side teacher excludes candidate side/price/future fields']}
    op.with_suffix('.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
