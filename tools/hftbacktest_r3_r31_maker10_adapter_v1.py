from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
P=ROOT/'data/research/r4_v0/p0_provenance_v1'

def run_market(mid:int, maker_first_repair_mode:str="NONE", maker_first_wait_ms:int=2200):
    old_shares=float(base.mod.SHARES); old_run=base.run_market
    def run_wrap(market_id:int,*args,**kwargs):
        kwargs['taker_sizing_mode']='r3_rawq'
        return old_run(market_id,*args,**kwargs)
    base.mod.SHARES=10.0;base.run_market=run_wrap
    try:
        rep=r3ctl.run_market(int(mid),True,maker_first_repair_mode=str(maker_first_repair_mode),maker_first_wait_ms=int(maker_first_wait_ms))
    finally:
        base.run_market=old_run;base.mod.SHARES=old_shares
    order_meta=rep.get('orderMeta') or {}
    maker_qty=[float(v.get('shares') or 0.) for v in order_meta.values()]
    takers=rep.get('takerAttempts') or []
    taker_qty=[float(x.get('requestedShares') or 0.) for x in takers if x.get('requestedShares') is not None]
    sr=rep.get('studentRollout') or {}
    audit={
      'marketId':int(mid),'makerOrders':len(maker_qty),'makerQtyUnique':sorted(set(round(x,10) for x in maker_qty)),
      'allMakerQty10':bool(maker_qty) and all(abs(x-10.0)<=1e-9 for x in maker_qty),
      'takerAttempts':len(takers),'takerRequestedQtyUnique':sorted(set(round(x,10) for x in taker_qty))[:50],
      'takerDynamicNon10Observed':any(abs(x-10.0)>1e-9 for x in taker_qty),
      'takerSizingMode':str((rep.get('config') or {}).get('takerSizingMode')),
      'makerFilledShares':float(sr.get('makerFilledShares') or 0.),'takerFilledShares':float(sr.get('takerFilledShares') or 0.),
      'finalPortfolio':sr.get('finalPortfolio'),'r3Control':rep.get('r3Control'),
    }
    rep['r3R31Maker10Adapter']={'version':'R3_R31_MAKER10_HFT_ADAPTER_V1','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','audit':audit,'contract':'r3_r31_maker10_hft_adapter_contract_v1.json'}
    return rep,audit

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--out',default=None);a=ap.parse_args()
    rep,audit=run_market(a.market_id)
    out=Path(a.out) if a.out else P/f'r3_r31_maker10_hft_adapter_smoke_market{a.market_id}_v1.json'
    out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    try: disp=str(out.relative_to(ROOT))
    except Exception: disp=str(out)
    print(json.dumps({'artifact':disp,'audit':audit},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
