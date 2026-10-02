from __future__ import annotations
import argparse,json,math
from collections import defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9

def age_bin(ms:float)->str:
    if ms<5000:return '<5s'
    if ms<15000:return '5-15s'
    if ms<30000:return '15-30s'
    if ms<60000:return '30-60s'
    return '>=60s'

def off_bin(x:float)->str:
    if not math.isfinite(x):return 'NA'
    if x<=0:return '<=0t'
    if x<2:return '0-2t'
    if x<5:return '2-5t'
    if x<10:return '5-10t'
    return '>=10t'

def agg(rows):
    n=len(rows); unc=[r for r in rows if not int(r.get('censoredBefore5s') or 0)]
    return {
      'rows':n,'orders':len(set(str(r.get('orderId')) for r in rows)),
      'uncensored5s':len(unc),
      'fill5sRate':(sum(int(r.get('labelAnyFill5s') or 0) for r in unc)/len(unc)) if unc else None,
      'eventualFillRate':(sum(float(r.get('eventualAdditionalFillShares') or 0)>EPS for r in rows)/n) if n else None,
      'meanRemainingQty':float(np.mean([float(r.get('remainingQty') or 0) for r in rows])) if n else None,
      'meanGap':float(np.mean([float(r.get('latestGap') or 0) for r in rows])) if n else None,
      'meanQuoteOffsetTicks':float(np.mean([float(r.get('quoteOffsetTicks')) for r in rows if r.get('quoteOffsetTicks') is not None and math.isfinite(float(r.get('quoteOffsetTicks')))])) if any(r.get('quoteOffsetTicks') is not None and math.isfinite(float(r.get('quoteOffsetTicks'))) for r in rows) else None,
      'meanAgeMs':float(np.mean([float(r.get('orderAgeMs') or 0) for r in rows])) if n else None,
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'
    ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())]
    allrows=[];markets=[];errors=[]
    for mid in ids:
        try:
            rep,audit=mk10.run_market(mid)
            seen=set(); z=[]
            for r0 in rep.get('orderStateRows') or []:
                if str(r0.get('context') or '')!='POST_DECISION_ACTIVE':continue
                if str(r0.get('hftStatus') or '') not in {'NEW','PARTIALLY_FILLED'}:continue
                key=(str(r0.get('orderId')),int(r0.get('checkpointMs') or 0))
                if key in seen:continue
                seen.add(key)
                r=dict(r0);port=r.get('portfolio') if isinstance(r.get('portfolio'),dict) else {}
                net=float(port.get('combined_net') or 0.0);gap=abs(net);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
                r['latestGap']=gap;r['weakSide']=weak;r['isWeakRepairCarrier']=bool(weak and str(r.get('side'))==weak)
                rem=float(r.get('remainingQty') or 0.0);pr=float(r.get('partialFillRatio') or 0.0);off=float(r.get('quoteOffsetTicks')) if r.get('quoteOffsetTicks') is not None else math.nan;age=float(r.get('orderAgeMs') or 0.0)
                r['remainingExceedsLatestGap']=bool(r['isWeakRepairCarrier'] and gap>EPS and rem>gap+EPS)
                r['remainingGapExcessShares']=max(0.0,rem-gap) if r['isWeakRepairCarrier'] else 0.0
                r['ageBin']=age_bin(age);r['offsetBin']=off_bin(off)
                r['stall15NoProgress']=bool(r['isWeakRepairCarrier'] and age>=15000 and pr<=EPS)
                r['auditZombie5t']=bool(r['stall15NoProgress'] and math.isfinite(off) and off>=5)
                z.append(r);allrows.append(r)
            wr=[r for r in z if r['isWeakRepairCarrier']]
            markets.append({'marketId':mid,'liveRows':len(z),'weakRepairRows':len(wr),'weakRepairOrders':len(set(str(r.get('orderId')) for r in wr)),'oversizeRows':sum(r['remainingExceedsLatestGap'] for r in wr),'stall15NoProgressRows':sum(r['stall15NoProgress'] for r in wr),'auditZombie5tRows':sum(r['auditZombie5t'] for r in wr),'makerQtyUnique':audit.get('makerQtyUnique'),'takerDynamicNon10Observed':audit.get('takerDynamicNon10Observed')})
            print(json.dumps(markets[-1],ensure_ascii=False),flush=True)
        except Exception as e:
            errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
    weak=[r for r in allrows if r['isWeakRepairCarrier']]
    by_age={b:agg([r for r in weak if r['ageBin']==b]) for b in ['<5s','5-15s','15-30s','30-60s','>=60s']}
    by_off={b:agg([r for r in weak if r['offsetBin']==b]) for b in ['<=0t','0-2t','2-5t','5-10t','>=10t','NA']}
    stall=[r for r in weak if r['stall15NoProgress']]; zombie=[r for r in weak if r['auditZombie5t']]; overs=[r for r in weak if r['remainingExceedsLatestGap']]
    out={'version':'R4_MAKER_ADAPTIVE_EXECUTION_V1_SHADOW_ANATOMY','researchOnly':True,'actionAuthority':False,'makerPerChildMaxShares':10.0,'takerArtificialShareCap':None,'markets':markets,'errors':errors,'aggregate':{'markets':len(markets),'errors':len(errors),'allLiveRows':len(allrows),'weakRepairRows':len(weak),'weakRepairOrders':len(set(str(r.get('orderId')) for r in weak)),'weakRepair':agg(weak),'stall15NoProgress':agg(stall),'auditZombie5t_descriptiveOnly':agg(zombie),'remainingExceedsLatestGap':agg(overs),'oversizeRows':len(overs),'oversizeOrders':len(set(str(r.get('orderId')) for r in overs)),'sumRemainingGapExcessShares':float(sum(float(r.get('remainingGapExcessShares') or 0) for r in overs)),'byAge':by_age,'byQuoteOffset':by_off},'boundary':'5s/eventual fill labels are post-episode scoring only. age/offset/progress/gap are strict-past. auditZombie5t is descriptive only, not a promoted threshold.'}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
