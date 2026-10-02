from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R213_FULL24_CHARACTERIZATION_AND_FAILURE_ATTRIBUTION_V1_20260906.json'
JOBS=['eth-ms4-r213-full24-s1-20260906-v1','eth-ms4-r213-full24-s2-20260906-v1','eth-ms4-r213-full24-s3-20260906-v1','eth-ms4-r213-full24-s4-20260906-v1']

def main():
    failures={int(x['marketId']):x for x in json.loads(SRC.read_text(encoding='utf-8'))['markets'] if x['pnl']<=0}
    rows=[]
    for j in JOBS:
        d=json.loads((ROOT/'data/research/lan_worker_returns'/j/'result.json').read_text(encoding='utf-8'))
        for r in d['rows']:
            if r['cell']!='MS4_R213_ACTIVE_ECONOMIC_REMAINDER':continue
            mid=int(r['marketId']);
            if mid not in failures:continue
            orders={}
            # slotHistory contains submit events and failure evidence. Track source key first-seen submit/price.
            for e in r.get('slotHistory',[]):
                k=e.get('key')
                if k and e.get('price') is not None:
                    orders.setdefault(str(k),{'t':int(e.get('t') or 0),'price':float(e.get('price')),'role':str(e.get('role') or e.get('event') or '')})
            dr=r.get('failureEvidenceActiveDrainEvents',[])
            src_terminal={str(x.get('sourceKey')):x for x in dr if x.get('event')=='PASSIVE_REPAIR_GENUINE_ZERO_FILL_TERMINAL'}
            for e in dr:
                if e.get('event')!='FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT':continue
                src=str(e.get('sourceKey')); z=src_terminal.get(src,{})
                srcp=float(z.get('sourcePrice') or 0); ap=float(e.get('activePrice') or 0); st=orders.get(src,{}).get('t')
                oldavg=None
                # reconstruct approximate opposite unmatched avg from active pair role result if available is not event-specific; use source->active delta directly.
                rows.append({'marketId':mid,'t':int(e.get('t') or 0),'sourceKey':src,'sourceRole':e.get('sourceRole'),'sourcePrice':srcp,'activePrice':ap,'priceChase':ap-srcp,'sourceSubmitT':st,'waitMs':(int(e.get('t') or 0)-st) if st else None,'repairProgressClock':int(e.get('repairProgressClock') or 0),'debt':float(e.get('debt') or 0),'reservedBefore':float(e.get('reservedBefore') or 0),'marketPnl':float(r['pnlDiagnosticOnly']),'primaryFailureCause':failures[mid]['primaryFailureCause']})
    out={'version':'MS4_ACTIVE_PREMIUM_TIMING_DIAGNOSTIC_V1','date':'2026-09-06','rows':rows,'aggregate':{}}
    if rows:
        vals=[x['priceChase'] for x in rows]; waits=[x['waitMs'] for x in rows if x['waitMs'] is not None]
        out['aggregate']={'activeEventsInLossMarkets':len(rows),'meanPriceChase':sum(vals)/len(vals),'medianPriceChase':sorted(vals)[len(vals)//2],'positiveChaseShare':sum(x>0 for x in vals)/len(vals),'meanWaitMs':sum(waits)/len(waits) if waits else None,'medianWaitMs':sorted(waits)[len(waits)//2] if waits else None}
    op=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_ACTIVE_PREMIUM_TIMING_DIAGNOSTIC_V1_20260906.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
