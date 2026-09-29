from __future__ import annotations
import argparse,json,math
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--states',required=True); ap.add_argument('--forks',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    sd=json.loads(Path(a.states).read_text(encoding='utf-8')); fd=json.loads(Path(a.forks).read_text(encoding='utf-8'))
    state_by={(int(s['marketId']),int(s['t'])):s for s in sd['states']}
    rows=[]
    for fr in fd['rows']:
        key=(int(fr['marketId']),int(fr['t'])); s=state_by.get(key)
        if s is None: raise KeyError(f'missing enriched state {key}')
        weak=str(s['weakSide']); expand=str(s['expandSide']); book=s['book']
        for branch in ('NEXT_REPAIR','NEXT_REEXPAND'):
            forced=(fr['interventions'][branch] or {}).get('forced') or {}
            if not forced.get('ok'): raise RuntimeError(f'unexercised {key} {branch}')
            role_expand=branch=='NEXT_REEXPAND'; side=str(forced['side']); p=float(forced['price']); q=float(forced['qty'])
            same_bid=float(book['expandBid'] if role_expand else book['weakBid']); same_ask=float(book['expandAsk'] if role_expand else book['weakAsk'])
            dr=fr['branchResolution'][branch]['deltaFromPrefix']
            mid=(float(dr['favoredPayoff'])+float(dr['weakPayoff']))/2.0
            skew=(float(dr['favoredPayoff'])-float(dr['weakPayoff']))/2.0
            rem=float(s['remainingDebtQty']); invgap=abs(float(s['inventory']['UP'])-float(s['inventory']['DOWN']))
            aligned_imb=float(book['imbalance'])*(1.0 if expand=='UP' else -1.0)
            feat={
                'actionIsReexpand':1.0 if role_expand else 0.0,'actionSideUp':1.0 if side=='UP' else 0.0,
                'actionPrice':p,'actionQty':q,'fullFillMidpoint':q*(0.5-p),'fullFillSkew':(q/2.0 if role_expand else -q/2.0),
                'repairProgressFrac':float(s['repairProgressFrac']),'remainingDebtQty':rem,'inventoryGapQty':invgap,
                'floor':float(s['floor']),'best':float(s['best']),'payoffGap':float(s['best'])-float(s['floor']),
                'freeSlots':float(s['freeSlots']),'liveSlots':float(s['liveSlots']),'qLadderLive':1.0 if s['qLadderLive'] else 0.0,
                'actionQtyOverDebt':q/max(rem,1e-9),
                'liveRepairSlots':float(s.get('liveRepairSlots',0)),'liveExpandSlots':float(s.get('liveExpandSlots',0)),
                'boundaryWasFill':1.0 if str(s.get('sourceBoundaryReason'))=='FILL' else 0.0,
                'bookImbalanceAligned':aligned_imb,'bookSpread':float(book['spread']),
                'weakBid':float(book['weakBid']),'weakAsk':float(book['weakAsk']),'expandBid':float(book['expandBid']),'expandAsk':float(book['expandAsk']),
                'weakQuoteSpread':float(book['weakAsk'])-float(book['weakBid']),'expandQuoteSpread':float(book['expandAsk'])-float(book['expandBid']),
                'actionPriceMinusSameBid':p-same_bid,'actionPriceMinusSameAsk':p-same_ask,
            }
            rows.append({'marketId':key[0],'t':key[1],'branch':branch,'features':feat,
                         'targets':{'localDeltaMidpoint':mid,'localDeltaSkew':skew,'localDeltaFloor':float(dr['floor']),'localDeltaBest':float(dr['best']),
                                    'resolutionKind':str(fr['branchResolution'][branch]['kind'])}})
    rows.sort(key=lambda x:(x['marketId'],x['t'],x['branch']))
    out={'version':'MANAGEMENT_MAINLINE_LIFECYCLE_LOCAL_VECTOR_CORPUS_V1_20260907','researchOnly':True,'runtimeAuthority':False,
         'stateCount':len(set((r['marketId'],r['t']) for r in rows)),'rowCount':len(rows),'rows':rows,
         'boundary':['consumed H100 only','strict-past enriched post-lifecycle state','two legal actions per state','targets stop at each branch first structural resolution','no decision-lag feature','no fixed window','no winner/terminal/Target/future feature']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'states':out['stateCount'],'rows':out['rowCount']},ensure_ascii=False))
if __name__=='__main__': main()
