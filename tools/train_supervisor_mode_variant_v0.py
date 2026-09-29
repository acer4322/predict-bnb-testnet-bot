from __future__ import annotations
import json, sys
from pathlib import Path
import joblib
from train_supervisor_mode_v0 import ROOT, OUT, CURRENT, build, fit, metrics


def main():
    variant=(sys.argv[1] if len(sys.argv)>1 else 'currentOnly').strip()
    if variant not in {'currentOnly','currentPlusMemory'}: raise SystemExit('variant must be currentOnly or currentPlusMemory')
    OUT.mkdir(parents=True,exist_ok=True)
    d,mem=build(); feats=CURRENT if variant=='currentOnly' else CURRENT+mem
    markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id'])
    ids=markets.market_id.astype(int).tolist(); a=int(len(ids)*.70); b=int(len(ids)*.85)
    split={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}
    parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in split.items()}
    print(json.dumps({'progress':'BUILT','variant':variant,'rows':len(d),'trainRows':len(parts['train']),'features':len(feats)}),flush=True)
    model=fit(parts['train'],feats)
    art=OUT/f'supervisor_mode_v0_{variant}.joblib'
    joblib.dump({'version':'SUPERVISOR_MODE_V0_HGB_RESEARCH','variant':variant,'model':model,'features':feats,'classes':list(model.classes_),'trainingMarkets':sorted(split['train']),'researchOnly':True,'runtimePromotion':False},art)
    print(json.dumps({'progress':'SAVED','variant':variant,'artifact':str(art)}),flush=True)
    res={k:metrics(model,parts[k],feats) for k in ('validation','test')}
    rep={'reportVersion':'SUPERVISOR_MODE_V0_VARIANT','variant':variant,'researchOnly':True,'sourceRows':len(d),'sourceMarkets':int(d.market_id.nunique()),'featureCount':len(feats),'splitMarkets':{k:len(v) for k,v in split.items()},'results':res,'artifact':str(art),'guards':['No winner/PnL.','No 2026-08-16 special market.','Strict-past memory only when present.','No runtime promotion.']}
    rp=OUT/f'supervisor_mode_v0_{variant}_report.json'; rp.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
