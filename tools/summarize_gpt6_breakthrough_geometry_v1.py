"""Paired, availability-aware postprocessing. No outcome-selected runtime policy."""
import argparse
import collections
import json
from pathlib import Path
import numpy as np

ap=argparse.ArgumentParser()
ap.add_argument('input')
ap.add_argument('output')
a=ap.parse_args()
d=json.loads(Path(a.input).read_text())
rows=d['rows']
contrasts=[('REPAIR_PAIR_ONLY','NEXT_REPAIR'),('REPAIR_DOUBLE','NEXT_REPAIR'),
           ('EXPAND_DOUBLE','NEXT_REEXPAND'),('EXPAND_INSIDE','NEXT_REEXPAND'),('WAIT','NATIVE')]
if 'EXPAND_INSIDE_INTENT' in rows[0]['branches']:
    contrasts=[('EXPAND_INSIDE_INTENT','EXPAND_INSIDE'),('EXPAND_NATIVE_INTENT','NEXT_REEXPAND'),('EXPAND_INSIDE_INTENT','NATIVE')]
rng=np.random.default_rng(20260907)
summary={}
for test,ctrl in contrasts:
    pairs=[r for r in rows if r['branches'][test]['available'] and r['branches'][ctrl]['available']]
    if not pairs:
        continue
    stat={}
    for key in ['favoredPayoff','weakPayoff','floor','best','winnerPnlPosthoc','fills','submits','alternations','buyNotional','gross']:
        av=np.array([r['branches'][ctrl]['terminal'][key] for r in pairs])
        bv=np.array([r['branches'][test]['terminal'][key] for r in pairs])
        dv=bv-av
        boot=np.mean(rng.choice(dv,size=(3000,len(dv)),replace=True),axis=1)
        stat[key]={'controlSum':float(av.sum()),'candidateSum':float(bv.sum()),'deltaSum':float(dv.sum()),
            'meanDeltaCI95':np.quantile(boot,[.025,.975]).tolist(),'improved':int((dv>1e-9).sum()),
            'worsened':int((dv<-1e-9).sum()),'tied':int((abs(dv)<=1e-9).sum()),'worstDelta':float(dv.min()),
            'deltaQ10':float(np.quantile(dv,.1)),'candidateWorst':float(bv.min())}
    phys={}
    for k in ['fillQty5s','repairPayQty5s','anyFill5s','cancelReq5s','terminal5s']:
        av=np.array([(r['branches'][ctrl]['physicalLabel'] or {}).get(k,0) for r in pairs])
        bv=np.array([(r['branches'][test]['physicalLabel'] or {}).get(k,0) for r in pairs])
        phys[k]={'controlSum':float(av.sum()),'candidateSum':float(bv.sum()),'deltaSum':float((bv-av).sum())}
    common=[]
    for r in pairs:
        x,y=r['branches'][ctrl],r['branches'][test]
        matched=all(abs(x['terminal'][k]-y['terminal'][k])<1e-8 for k in ['fills','submits','alternations','buyNotional','gross'])
        if matched:
            common.append({'marketId':r['marketId'],'pnlDelta':y['terminal']['winnerPnlPosthoc']-x['terminal']['winnerPnlPosthoc']})
    summary[test+'_minus_'+ctrl]={'eligible':len(pairs),'totalSeams':len(rows),'terminal':stat,'physical5s':phys,
        'exactActivityExposureMatchedPairs':common,
        'exposureNormalizedFavored':{b:sum(r['branches'][b]['terminal']['favoredPayoff'] for r in pairs)/max(1e-9,sum(r['branches'][b]['terminal']['buyNotional'] for r in pairs)) for b in (ctrl,test)},
        'strata':{cat:{'n':sum(r['stateSpec']['researchStratum']==cat for r in pairs),
            'favoredDelta':sum(r['branches'][test]['terminal']['favoredPayoff']-r['branches'][ctrl]['terminal']['favoredPayoff'] for r in pairs if r['stateSpec']['researchStratum']==cat)} for cat in sorted({r['stateSpec']['researchStratum'] for r in pairs})}}

coherence=[]
for r in rows:
    for b,x in r['branches'].items():
        q=x['physicalLabel']
        if q is None:continue
        for h in (3,5):
            fill=q[f'fillQty{h}s'];repair=q[f'repairPayQty{h}s'];overflow=q[f'overflowQty{h}s']
            if not(-1e-8<=fill<=q['qty']+1e-8 and abs(fill-repair-overflow)<1e-8):coherence.append([r['marketId'],b,h])
out={'source':a.input,'allCorrectnessPass':d['allCorrectnessPass'],'quantityLabelCoherenceErrors':coherence,
     'seams':d['seams'],'runs':d['runs'],'availability':{b:sum(r['branches'][b]['available'] for r in rows) for b in rows[0]['branches']},
     'contrasts':summary,'limitations':d['limitations'],
     'graduationVerdict':'NOT_EVALUATED_AS_POLICY; forced seam contrasts are not a repeated candidate policy or untouched evaluation'}
Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'correctness':out['allCorrectnessPass'],'coherenceErrors':len(coherence),'seams':len(rows),'availability':out['availability'],
                 'effects':{k:{'n':v['eligible'],'favoredDelta':v['terminal']['favoredPayoff']['deltaSum'],
                     'pnlDelta':v['terminal']['winnerPnlPosthoc']['deltaSum'],'pnlSum':v['terminal']['winnerPnlPosthoc']['candidateSum'],
                     'pnlImproved':v['terminal']['winnerPnlPosthoc']['improved'],'fillsDelta':v['terminal']['fills']['deltaSum'],
                     'submitsDelta':v['terminal']['submits']['deltaSum'],'worstPnlDelta':v['terminal']['winnerPnlPosthoc']['worstDelta']} for k,v in summary.items()}}))
