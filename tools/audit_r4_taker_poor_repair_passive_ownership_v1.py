from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_overrepair_v1 as base
from tools import hftbacktest_r3_context_control_overrepair_v1 as r3ctl
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9

def run_audit(mid:int):
    rows=[]
    def hook(**kw):
        c=kw['c'];a=kw['adapter'];side=str(kw['side']);price=float(kw['price']);qty=float(kw['qty']);now=int(kw['now'])
        up=float(c.inventory.maker_up+c.inventory.taker_up);dn=float(c.inventory.maker_down+c.inventory.taker_down);upc=float(c.inventory.maker_up_cost+c.inventory.taker_up_cost);dnc=float(c.inventory.maker_down_cost+c.inventory.taker_down_cost);gap=up-dn;weak='DOWN' if gap>EPS else 'UP' if gap<-EPS else None
        if not weak or side!=weak:return False
        strong_sh=up if gap>0 else dn;strong_cost=upc if gap>0 else dnc;strong_avg=strong_cost/strong_sh if strong_sh>EPS else 0.;pair_cost=strong_avg+price;nup=up+(qty if side=='UP' else 0.);ndn=dn+(qty if side=='DOWN' else 0.);post_floor=min(nup,ndn)-(upc+dnc+qty*price);pre_floor=min(up,dn)-(upc+dnc)
        if pair_cost<=1.0+EPS or post_floor>0.0+EPS:return False
        live=[]
        for key,o in list(c.orders.items()):
            if str(o.side)!=side:continue
            s=a.snap(key);st=str(s.get('status') or '')
            if st in {'NEW','PARTIALLY_FILLED'}:
                q=max(0.,float(s.get('leavesQty') or 0.));cum=max(0.,float(s.get('cumExecQty') or 0.));tot=q+cum
                live.append({'orderId':o.id,'price':float(o.price),'status':st,'remaining':q,'cumExec':cum,'progress':cum/tot if tot>EPS else 0.,'ageMs':now-int(o.placed_at_ms)})
        rows.append({'marketId':mid,'atMs':now,'secondsLeft':kw['snapshot'].get('secondsLeft'),'side':side,'ask':price,'qty':qty,'preGap':gap,'preFloor':pre_floor,'postFloorIfFilled':post_floor,'pairCostProxy':pair_cost,'sameSideLiveCount':len(live),'sameSideLiveRemaining':sum(x['remaining'] for x in live),'maxProgress':max([x['progress'] for x in live],default=0.),'oldestAgeMs':max([x['ageMs'] for x in live],default=0),'liveCarriers':live})
        return False
    old=base.TAKER_PRE_SUBMIT_HOOK;base.TAKER_PRE_SUBMIT_HOOK=hook
    try:rep=r3ctl.run_market(mid,True)
    finally:base.TAKER_PRE_SUBMIT_HOOK=old
    return rows,rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets';ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())];rows=[];errs=[]
    for mid in ids:
        try:r,_=run_audit(mid);rows.extend(r);print(json.dumps({'marketId':mid,'poorTakerAttempts':len(r),'withLivePassive':sum(x['sameSideLiveCount']>0 for x in r)},ensure_ascii=False),flush=True)
        except Exception as e:errs.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errs[-1]),flush=True)
    n=len(rows);live=[x for x in rows if x['sameSideLiveCount']>0];out={'version':'R4_TAKER_POOR_REPAIR_PASSIVE_OWNERSHIP_V1','researchOnly':True,'actionAuthority':False,'rows':rows,'errors':errs,'aggregate':{'markets':len(ids)-len(errs),'poorTakerAttempts':n,'attemptMarkets':len(set(x['marketId'] for x in rows)),'withLivePassive':len(live),'withLivePassiveRate':len(live)/n if n else None,'meanLiveRemaining':float(np.mean([x['sameSideLiveRemaining'] for x in live])) if live else None,'meanMaxProgress':float(np.mean([x['maxProgress'] for x in live])) if live else None,'medianOldestAgeMs':float(np.median([x['oldestAgeMs'] for x in live])) if live else None,'liveCoversGap':sum(x['sameSideLiveRemaining']>=abs(float(x['preGap']))-EPS for x in live)}};Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
