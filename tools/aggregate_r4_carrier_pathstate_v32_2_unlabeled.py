from pathlib import Path
import json,glob
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_pathstate_v32_2_features_unlabeled.json'
rows=[];errors=[];sources=[]
for p in sorted((ROOT/'data/research/lan_worker_returns').glob('r4-carrier-v32-2-*/result.json')):
    try:d=json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:errors.append({'source':str(p),'error':f'{type(e).__name__}:{e}'});continue
    sources.append(str(p.relative_to(ROOT)))
    errors.extend([{'source':str(p.relative_to(ROOT)),**x} for x in (d.get('errors') or [])])
    for r in d.get('rows') or []:
        for x in r.get('seamInstrumentation') or []:
            y=dict(x); y['sourceJob']=p.parent.name; rows.append(y)
rows=sorted(rows,key=lambda x:(int(x.get('marketId') or 0),int(x.get('t') or 0),str(x.get('logical'))))
rep={'version':'R4_CARRIER_PATHSTATE_V32_2_FEATURES_UNLABELED','researchOnly':True,'unlabeled':True,'winnerUsed':False,'settlementUsed':False,'futureTargetActionUsed':False,'featureContract':'V32.1 path-derivative/within-carrier normalization first; this phase materializes lifecycle-ledger path only, queue-ahead derivatives pending V32.3','marketCount':len(set(int(x['marketId']) for x in rows)),'seamCount':len(rows),'rows':rows,'errors':errors,'sources':sources}
out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(out),'marketCount':rep['marketCount'],'seamCount':rep['seamCount'],'errors':len(errors)},ensure_ascii=False))
