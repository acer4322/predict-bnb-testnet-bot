from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl

EPS=1e-9

def _active_order_path_state(c,a,now,net):
    try:
        bf=base.mod.outcome_book(a.book,None) or {}
    except Exception:
        bf={}
    repair_side='DOWN' if net>EPS else 'UP' if net<-EPS else None
    dominant_side='UP' if net>EPS else 'DOWN' if net<-EPS else None
    orders=[]
    for key,o in list(c.orders.items()):
        snap=a.snap(key)
        bid=bf.get('up_bid') if o.side=='UP' else bf.get('down_bid')
        ask=bf.get('up_ask') if o.side=='UP' else bf.get('down_ask')
        cum=float(snap.get('cumExecQty') or 0.0)
        leaves=snap.get('leavesQty')
        if leaves is None: leaves=max(0.0,float(o.shares))
        qty=float(snap.get('qty') or (cum+float(leaves)))
        qoff=((float(bid)-float(o.price))/base.mod.GRID) if bid is not None else float('nan')
        init=float(o.initial_depth)
        dep=float(o.cum_depletion)
        orders.append({
            'side':o.side,'price':float(o.price),'orderAgeMs':float(now-int(o.placed_at_ms)),
            'hftStatus':str(snap.get('status') or 'NONE'),'originalQty':qty,'cumExecQty':cum,'remainingQty':float(leaves),
            'remainingRatio':float(leaves)/qty if qty>EPS else float('nan'),'partialFillRatio':cum/qty if qty>EPS else 0.0,
            'quoteOffsetTicks':float(qoff),'currentBid':bid,'currentAsk':ask,
            'currentSpreadTicks':((float(ask)-float(bid))/base.mod.GRID) if bid is not None and ask is not None else float('nan'),
            'initialDepth':init,'publicCumDepletion':dep,'publicDepletionRatio':dep/init if init>EPS else float('nan'),
            'publicAnyDepletion':bool(o.any_depletion),'occupiedBefore':bool(o.occupied_before),
        })
    def vals(side,key):
        return [float(x[key]) for x in orders if x['side']==side and isinstance(x.get(key),(int,float)) and math.isfinite(float(x[key]))]
    import math
    def sm(v,fn,default=0.0): return float(fn(v)) if v else float(default)
    summary={
        'activeOrderCount':len(orders),'repairSide':repair_side,'dominantSide':dominant_side,
        'repairSideActiveCount':sum(x['side']==repair_side for x in orders) if repair_side else 0,
        'dominantSideActiveCount':sum(x['side']==dominant_side for x in orders) if dominant_side else 0,
        'totalRemainingQty':sum(float(x['remainingQty']) for x in orders),
        'repairSideRemainingQty':sum(float(x['remainingQty']) for x in orders if x['side']==repair_side) if repair_side else 0.0,
        'dominantSideRemainingQty':sum(float(x['remainingQty']) for x in orders if x['side']==dominant_side) if dominant_side else 0.0,
        'meanOrderAgeMs':sm(vals('UP','orderAgeMs')+vals('DOWN','orderAgeMs'),lambda z:sum(z)/len(z)),
        'maxOrderAgeMs':sm(vals('UP','orderAgeMs')+vals('DOWN','orderAgeMs'),max),
        'repairMeanOrderAgeMs':sm(vals(repair_side,'orderAgeMs') if repair_side else [],lambda z:sum(z)/len(z)),
        'dominantMeanOrderAgeMs':sm(vals(dominant_side,'orderAgeMs') if dominant_side else [],lambda z:sum(z)/len(z)),
        'repairBestQuoteOffsetTicks':sm(vals(repair_side,'quoteOffsetTicks') if repair_side else [],min,float('nan')),
        'dominantBestQuoteOffsetTicks':sm(vals(dominant_side,'quoteOffsetTicks') if dominant_side else [],min,float('nan')),
        'repairMeanDepletionRatio':sm(vals(repair_side,'publicDepletionRatio') if repair_side else [],lambda z:sum(z)/len(z)),
        'dominantMeanDepletionRatio':sm(vals(dominant_side,'publicDepletionRatio') if dominant_side else [],lambda z:sum(z)/len(z)),
        'repairPartialCount':sum(x['side']==repair_side and x['partialFillRatio']>0 for x in orders) if repair_side else 0,
        'dominantPartialCount':sum(x['side']==dominant_side and x['partialFillRatio']>0 for x in orders) if dominant_side else 0,
    }
    return {'summary':summary,'orders':orders}

def first_candidate(decisions,age_ms=5000):
    risk_since=None
    for d in decisions:
        p=d.get('portfolio') or {}; t=int(d.get('decisionMs') or 0)
        absn=float(p.get('maker_abs_net') or 0.0); pc=float(p.get('maker_paired_coverage') or 0.0)
        risky=(d.get('episode') is None and absn>=18-EPS and pc<0.80)
        if not risky:
            risk_since=None; continue
        if risk_since is None: risk_since=t
        if t-risk_since>=age_ms:
            return {'decisionMs':t,'riskSinceMs':risk_since,'riskAgeMs':t-risk_since,'makerAbsNet':absn,'makerPairedCoverage':pc}
    return None

def run_exact(mid,force_at):
    orig_new=base.new_controller; applied=[]
    def injected_new(a):
        c=orig_new(a)
        inv=c.inventory; orig_features=inv.features
        def features(now):
            # In UnifiedControllerPaperV2._step this call occurs immediately after
            # HFT _fill_orders(snapshot, now) and before residual-wake/Repair logic.
            # Inject once at the exact baseline candidate timestamp, then return
            # the unmodified deterministic portfolio feature vector.
            if not applied and int(now)==int(force_at) and c.episode is None:
                net=float(inv.maker_up-inv.maker_down); gross=float(inv.maker_up+inv.maker_down)
                pc=2*min(inv.maker_up,inv.maker_down)/gross if gross>EPS else 0.0; ab=abs(net)
                if ab>1.0:
                    side='UP' if net>0 else 'DOWN'
                    c.episode={'kind':'RESIDUAL','side':side,'risk_start_ms':int(now),'risk_pre_abs':ab,'start_ms':int(now),'pre_abs':ab,'start_abs':ab,'expansion':0.0,'pre_pc':pc,'unresolved':False}
                    c.readiness=True
                    path_state=_active_order_path_state(c,a,int(now),net); applied.append({'atMs':int(now),'side':side,'makerNet':net,'makerAbs':ab,'makerPairedCoverage':pc,'requestedAtMs':int(force_at),'exactTimestamp':True,'seam':'POST_HFT_FILL_PRE_DECISION_INVENTORY_FEATURES','activeOrderPathState':path_state})
            return orig_features(now)
        inv.features=features
        return c
    base.new_controller=injected_new
    try: rep=r3ctl.run_market(int(mid),True)
    finally: base.new_controller=orig_new
    rep['forcedRepairWakeV1']=applied
    return rep

def compact(r):
    s=r['studentRollout'];p=s['finalPortfolio']
    return {'makerFillEvents':int(s['makerFillEvents']),'makerFilledShares':float(s['makerFilledShares']),'takerFills':int(s['takerFills']),'takerFilledShares':float(s.get('takerFilledShares') or 0.0),'finalFloor':float(p.get('worst_case_floor') or 0.0),'finalAbsNet':float(p.get('combined_abs_net') or 0.0),'finalCoverage':float(p.get('combined_paired_coverage') or 0.0),'makerCostUsdt':float(s.get('makerCostUsdt') or 0.0),'takerCostUsdt':float(s.get('takerCostUsdt') or 0.0),'takerFeesUsdt':float(s.get('takerFeesUsdt') or 0.0),'forced':r.get('forcedRepairWakeV1') or []}

def classify(b,c):
    df=c['finalFloor']-b['finalFloor']; da=c['finalAbsNet']-b['finalAbsNet']
    if df>=-EPS and da<=EPS and (df>EPS or da<-EPS): return 'PARETO_BENEFICIAL'
    if df<=EPS and da>=-EPS and (df<-EPS or da>EPS): return 'PARETO_HARMFUL'
    if abs(df)<=EPS and abs(da)<=EPS:return 'NO_EFFECT'
    return 'TRADEOFF'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    b=r3ctl.run_market(a.market_id,True);cand=first_candidate(b.get('decisionRows') or []);bc=compact(b)
    rep={'version':'R4_R3_REPAIR_COUNTERFACTUAL_TEACHER_V1_VALIDATION','researchOnly':True,'actionAuthority':False,'marketId':a.market_id,'candidate':cand,'baseline':bc,'counterfactual':None,'branchClass':'NO_CANDIDATE','exactBranchApplied':False,'baselineReplayDeterministic':None,'contract':'r4_r3_repair_counterfactual_teacher_v1_contract.json'}
    if cand:
        c=run_exact(a.market_id,cand['decisionMs']);cc=compact(c); b2=r3ctl.run_market(a.market_id,True);b2c=compact(b2)
        rep.update({'counterfactual':cc,'exactBranchApplied':bool(cc['forced'] and int(cc['forced'][0]['atMs'])==int(cand['decisionMs'])),'baselineReplayDeterministic':bc==b2c,'branchClass':classify(bc,cc),'delta':{'finalFloor':cc['finalFloor']-bc['finalFloor'],'finalAbsNet':cc['finalAbsNet']-bc['finalAbsNet'],'finalCoverage':cc['finalCoverage']-bc['finalCoverage'],'makerFilledShares':cc['makerFilledShares']-bc['makerFilledShares'],'takerFilledShares':cc['takerFilledShares']-bc['takerFilledShares'],'makerCostUsdt':cc['makerCostUsdt']-bc['makerCostUsdt'],'takerCostUsdt':cc['takerCostUsdt']-bc['takerCostUsdt'],'takerFeesUsdt':cc['takerFeesUsdt']-bc['takerFeesUsdt']}})
    Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
