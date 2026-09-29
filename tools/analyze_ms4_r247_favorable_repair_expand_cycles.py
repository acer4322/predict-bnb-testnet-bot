from __future__ import annotations
import argparse,json,statistics
from pathlib import Path
from collections import defaultdict,Counter
EPS=1e-9

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    d=json.loads(Path(a.input).read_text(encoding='utf-8'))
    rows=[r for r in d.get('rows',[]) if str(r.get('cell','')).startswith('MS4_R247')]
    all_segments=[]; per=[]
    for r in rows:
        mid=int(r['marketId']); repl_keys={str(e.get('key')) for e in r.get('r247Events',[]) if e.get('event')=='R247_FAVORABLE_REPLENISHMENT_SUBMIT'}
        lots=defaultdict(list); segs=[]; born=0; born_qty=0.0
        events=[e for e in r.get('splitEvents',[]) if e.get('event')=='ROLE_FILL_SPLIT']
        events=sorted(enumerate(events),key=lambda z:(int(z[1].get('t') or 0),z[0]))
        for _,e in events:
            gen=int(e.get('generationAtSubmit') or -1); role=str(e.get('role')); side=str(e.get('side')); p=float(e.get('price') or 0.0)
            rq=float(e.get('repairAllocated') or 0.0); inc=float(e.get('fillInc') or 0.0); key=str(e.get('key'))
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and rq>EPS:
                surplus='DOWN' if side=='UP' else 'UP'; lots[gen].append({'t':int(e.get('t') or 0),'side':surplus,'repairPrice':p,'remaining':rq,'qty':rq,'sourceKey':key,'sourceRole':role}); born+=1; born_qty+=rq
            if role!='SATELLITE_EXPAND' or inc<=EPS:continue
            rem=inc; used=[]
            for x in sorted(lots[gen],key=lambda z:(float(z['repairPrice']),int(z['t']))):
                if rem<=EPS:break
                if x['side']!=side or x['remaining']<=EPS or float(x['repairPrice'])+p>1.0+EPS:continue
                q=min(rem,float(x['remaining'])); x['remaining']-=q; rem-=q
                ps=float(x['repairPrice'])+p; used.append({'repairPrice':x['repairPrice'],'qty':q,'pairSum':ps,'sourceKey':x['sourceKey'],'sourceRole':x['sourceRole']})
            mq=sum(x['qty'] for x in used); gain=sum((1.0-x['pairSum'])*x['qty'] for x in used)
            seg={'marketId':mid,'t':int(e.get('t') or 0),'generation':gen,'key':key,'kind':'REPLENISHMENT' if key in repl_keys else 'ORDINARY',
                 'side':side,'expandPrice':p,'expandQty':inc,'matchedFavorableQty':mq,'unmatchedExpandQty':rem,'matchedGain':gain,'repairLots':used,
                 'weightedPairSum':(sum(x['pairSum']*x['qty'] for x in used)/mq if mq>EPS else None)}
            segs.append(seg); all_segments.append(seg)
        leftovers=[x for arr in lots.values() for x in arr if x['remaining']>EPS]
        per.append({'marketId':mid,'repairLotsBorn':born,'repairQtyBorn':born_qty,'expandFillEvents':len(segs),
                    'favorableMatchedExpandEvents':sum(s['matchedFavorableQty']>EPS for s in segs),
                    'fullyFavorableCoveredExpandEvents':sum(s['matchedFavorableQty']+EPS>=s['expandQty'] for s in segs),
                    'favorableMatchedQty':sum(s['matchedFavorableQty'] for s in segs),'matchedGain':sum(s['matchedGain'] for s in segs),
                    'ordinaryFavorableEvents':sum(s['kind']=='ORDINARY' and s['matchedFavorableQty']>EPS for s in segs),
                    'replenishmentFavorableEvents':sum(s['kind']=='REPLENISHMENT' and s['matchedFavorableQty']>EPS for s in segs),
                    'leftoverRepairLots':len(leftovers),'leftoverRepairQty':sum(x['remaining'] for x in leftovers)})
    matched=[s for s in all_segments if s['matchedFavorableQty']>EPS]; full=[s for s in matched if s['matchedFavorableQty']+EPS>=s['expandQty']]
    wq=sum(s['matchedFavorableQty'] for s in matched)
    agg={'markets':len(rows),'repairLotsBorn':sum(x['repairLotsBorn'] for x in per),'repairQtyBorn':sum(x['repairQtyBorn'] for x in per),
         'expandFillEvents':len(all_segments),'favorableMatchedExpandEvents':len(matched),'fullyFavorableCoveredExpandEvents':len(full),
         'ordinaryFavorableEvents':sum(s['kind']=='ORDINARY' for s in matched),'replenishmentFavorableEvents':sum(s['kind']=='REPLENISHMENT' for s in matched),
         'favorableMatchedQty':wq,'matchedMarginalFloorGain':sum(s['matchedGain'] for s in matched),
         'weightedPairSum':(sum(float(s['weightedPairSum'])*s['matchedFavorableQty'] for s in matched if s['weightedPairSum'] is not None)/wq if wq>EPS else None),
         'medianSegmentPairSum':statistics.median([s['weightedPairSum'] for s in matched]) if matched else None,
         'leftoverRepairLots':sum(x['leftoverRepairLots'] for x in per),'leftoverRepairQty':sum(x['leftoverRepairQty'] for x in per)}
    out={'version':'MS4_R247_FAVORABLE_REPAIR_EXPAND_CYCLE_AUDIT_V1','researchOnly':True,'behaviorMutation':False,'aggregate':agg,'perMarket':per,'segments':all_segments,
         'boundary':['offline reconstruction from R2.47 confirmed ROLE_FILL_SPLIT only','same-generation actual Repair allocations only','SATELLITE_EXPAND matched FIFO by cheapest repairPrice with repairPrice+expandPrice<=1','winner not used in matching','diagnostic only']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':agg,'matchedMarkets':[x for x in per if x['favorableMatchedExpandEvents']>0]},ensure_ascii=False))
if __name__=='__main__':main()
