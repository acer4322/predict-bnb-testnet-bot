from __future__ import annotations
import argparse,json,math
from pathlib import Path
EPS=1e-9

def floor_from(u,d,c): return min(u,d)-c

def portfolio_states(events):
    u=d=c=0.0;out=[]
    for e in sorted(events,key=lambda x:(int(x.get('t') or 0),str(x.get('key')))):
        pre=floor_from(u,d,c);side=str(e.get('side'));q=float(e.get('qty') or 0.0);p=float(e.get('price') or 0.0)
        if side=='UP':u+=q
        elif side=='DOWN':d+=q
        c+=q*p;post=floor_from(u,d,c)
        out.append({**e,'floorBeforeRebuilt':pre,'floorAfterRebuilt':post,'upAfter':u,'downAfter':d,'costAfter':c})
    return out

def fifo_match(repairs,qty):
    need=float(qty);matched=0.0;notional=0.0;rows=[]
    for r in repairs:
        if need<=EPS:break
        q=float(r.get('qty') or 0.0);take=min(q,need)
        if take>EPS:
            p=float(r.get('price') or 0.0);matched+=take;notional+=take*p;need-=take;rows.append({'key':r.get('key'),'t':r.get('t'),'qty':q,'matchedQty':take,'price':p})
    return matched,notional,rows,max(0.0,need)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--v72-3m',default='data/research/lan_worker_returns/eth-v72-active-economic-handoff-3m-20260903-v1/result.json');ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.load(open(a.v72_3m,encoding='utf-8'));branches=[]
    for row in d['rows']:
        mid=int(row['marketId']);b=row['baselineV70G'];c=row['candidateV72'];blocks=[x for x in (c.get('v72Events') or []) if x.get('event')=='ACTIVE_FALLBACK_ECONOMIC_BLOCK']
        if not blocks:continue
        ev=portfolio_states(b.get('v53FillEvents') or [])
        v64=b.get('v64Events') or []
        for blk in blocks:
            src=str(blk['sourceKey']);fm=next((x for x in v64 if x.get('event')=='ACTIVE_EXPAND_FALLBACK_FILL' and str(x.get('sourceKey'))==src),None)
            if fm is None:branches.append({'marketId':mid,'sourceKey':src,'error':'NO_BASELINE_ACTIVE_FILL'});continue
            key=str(fm['key']);fill=next(x for x in ev if str(x.get('key'))==key);ft=int(fill['t']);side=str(fill['side']);pay='UP' if side=='DOWN' else 'DOWN';q=float(fill['qty']);pe=float(fill['price'])
            later_expand=[x for x in ev if int(x['t'])>ft and str(x.get('role')) in ('PASSIVE_EXPAND','ACTIVE_EXPAND')]
            endt=min([int(x['t']) for x in later_expand],default=10**30)
            repairs=[x for x in ev if ft<int(x['t'])<endt and str(x.get('role')) in ('PASSIVE_REPAIR','ACTIVE_REPAIR') and str(x.get('side'))==pay]
            matched,rn,mrows,unmatched=fifo_match(repairs,q);rp=rn/matched if matched>EPS else None;fps=(pe+rp) if rp is not None else None
            totalrq=sum(float(x.get('qty') or 0.0) for x in repairs);excess=max(0.0,totalrq-matched)
            last_state=(repairs[-1] if repairs else fill);settle_floor=float(last_state['floorAfterRebuilt']);pre_floor=float(fill['floorBeforeRebuilt']);post_fill=float(fill['floorAfterRebuilt'])
            branch={'marketId':mid,'sourceKey':src,'activeKey':key,'activeFillT':ft,'activeSide':side,'activeQty':q,'activePrice':pe,'backwardWeightedRepairPrice':blk.get('weightedRepairPrice'),'backwardPairSum':blk.get('pairSum'),'postRepairWindowEndT':None if endt>=10**30 else endt,'forwardRepairFillCount':len(repairs),'forwardRepairQtyTotal':totalrq,'forwardMatchedQty':matched,'forwardMatchedRepairPrice':rp,'forwardPairSum':fps,'forwardMatchedFloorDelta':matched*(1.0-fps) if fps is not None else None,'unmatchedExpandQty':unmatched,'excessRepairQtyBeyondActiveDebt':excess,'floorBeforeActive':pre_floor,'floorAfterActive':post_fill,'floorAfterSettlementWindow':settle_floor,'floorDeltaActiveThroughSettlement':settle_floor-pre_floor,'backwardMinusForwardPair':float(blk['pairSum'])-fps if fps is not None else None,'classificationFlipToNonDamaging':bool(fps is not None and float(blk['pairSum'])>1+EPS and fps<=1+EPS),'matchedRepairRows':mrows,'allRepairRows':[{'key':x.get('key'),'t':x.get('t'),'qty':x.get('qty'),'price':x.get('price'),'floorAfter':x.get('floorAfterRebuilt')} for x in repairs]}
            branches.append(branch)
    valid=[x for x in branches if 'error' not in x];flips=sum(bool(x['classificationFlipToNonDamaging']) for x in valid);material=sum(x.get('backwardMinusForwardPair') is not None and abs(float(x['backwardMinusForwardPair']))>=0.03 for x in valid)
    reject=flips>0 or material>0
    out={'version':'ETH_REPAIR_V73_ACTIVE_EXPAND_FORWARD_SETTLEMENT_ANATOMY','date':'2026-09-03','researchOnly':True,'actionAuthority':False,'branches':branches,'aggregate':{'branches':len(valid),'classificationFlipsToForwardNonDamaging':flips,'materialPairMismatchGe003':material,'forwardNonDamagingShare':sum(x.get('forwardPairSum') is not None and x['forwardPairSum']<=1+EPS for x in valid)/len(valid) if valid else None,'medianBackwardPairSum':None,'medianForwardPairSum':None},'decision':'REJECT_BACKWARD_PAIR_AS_STANDALONE_ACTION_GATE' if reject else 'KEEP_BACKWARD_PAIR_AS_EXECUTION_GATE_CANDIDATE','interpretationBoundary':['Forward settlement uses future fills and is post-episode causal anatomy only; never runtime authority.','If backward gate misclassifies, next ex-ante candidate should use recoverability/repair-ceiling information available at the handoff clock, not realized future Repair price.'],'boundary':['no HFT rerun','no winner/PnL gate','no tuning','no H100','no 8781']}
    import statistics
    bp=[float(x['backwardPairSum']) for x in valid if x.get('backwardPairSum') is not None];fp=[float(x['forwardPairSum']) for x in valid if x.get('forwardPairSum') is not None]
    out['aggregate']['medianBackwardPairSum']=statistics.median(bp) if bp else None;out['aggregate']['medianForwardPairSum']=statistics.median(fp) if fp else None
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'aggregate':out['aggregate'],'branches':branches},ensure_ascii=False))
if __name__=='__main__':main()
