from pathlib import Path
import glob,json
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_runtime_two_axis_v37_equivalence.json'
V36=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_runtime_equivalent_selective_completion_v36_event_map.json'
rows=[];errors=[]
for d in sorted(glob.glob(str(RET/'r4-runtime2axis-v37-*'))):
 p=Path(d)/'result.json'
 if not p.exists():continue
 z=json.loads(p.read_text(encoding='utf-8'));errors.extend(z.get('errors') or [])
 for r in z.get('rows') or []:
  tr=r.get('runtimeLateDecisionTrace') or []
  rows.append({'marketId':int(r['marketId']),'trace':tr,'eligible':bool(any(bool(x.get('eligible')) for x in tr)),'lateWeakAcquisitions':int((r.get('counts') or {}).get('lateWeakAcquisitions',0)),'runtimeLateAnchorChecks':int((r.get('counts') or {}).get('runtimeLateAnchorChecks',0))})
expected=set(int(x) for x in json.loads(V36.read_text(encoding='utf-8')).get('eligibleMarketIds') or [])
got=set(x['marketId'] for x in rows if x['eligible'])
ids=set(x['marketId'] for x in rows)
missing_rows=sorted(set(json.loads(V36.read_text(encoding='utf-8')).get('events',{}).keys())-set(str(x) for x in ids))
rep={'version':'R4_RUNTIME_TWO_AXIS_V37_EQUIVALENCE','researchOnly':True,'strictPastRuntimeEligibility':True,'eligibleMapRuntimeInput':False,'nRows':len(rows),'errors':errors,'expectedEligible':sorted(expected),'runtimeEligible':sorted(got),'matches':sorted(expected)==sorted(got),'extraRuntimeEligible':sorted(got-expected),'missedExpectedEligible':sorted(expected-got),'anchorChecks':sum(x['runtimeLateAnchorChecks'] for x in rows),'runtimeAcquisitions':sum(x['lateWeakAcquisitions'] for x in rows),'missingRows':missing_rows,'rows':rows}
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(OUT),'nRows':rep['nRows'],'matches':rep['matches'],'expectedEligible':len(expected),'runtimeEligible':len(got),'errors':len(errors),'missingRows':len(missing_rows)},ensure_ascii=False))
