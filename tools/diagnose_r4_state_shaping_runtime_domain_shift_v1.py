from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';ext=pd.read_csv(P/'r4_state_shaping_authorization_v11_external40_rows.csv');ext=ext[(ext.seconds_left>180)&(ext.seconds_left<=300)].copy();rr=[]
for fp in sorted(P.glob('r4_state_shaping_runtime_same30_chunk*.json')):
 try:d=json.loads(fp.read_text())
 except Exception:continue
 if not isinstance(d,dict) or 'markets' not in d:continue
 rr.extend(z for r in d['markets'] for z in r.get('records',[]) if z['phase']=='FORMATION_180_300')
seen={(x['marketId'],x['atMs'],x['attemptSide']):x for x in rr};dedup=list(seen.values());made=[x for x in dedup if x.get('made')];obj=joblib.load(P/'r4_state_shaping_authorization_v11_portable_shadow.joblib');F=obj['features'];rdf=pd.DataFrame([{**x['portable'],'p':x['pStateShaping']} for x in made]);base=float(rdf.p.mean()) if len(rdf) else None;rows=[]
for f in F:
 tm=float(ext[f].median());rm=float(rdf[f].median()) if len(rdf) else None;q10=float(ext[f].quantile(.1));q90=float(ext[f].quantile(.9));xx=rdf[F].copy();xx[f]=tm;mp=float(obj['model'].predict_proba(xx)[:,1].mean()) if len(xx) else None;rows.append({'feature':f,'targetMedian':tm,'runtimeMadeMedian':rm,'targetP10':q10,'targetP90':q90,'runtimeMedianOutsideTarget10to90':bool(rm is not None and (rm<q10 or rm>q90)),'meanPIfTargetMedian':mp,'deltaMeanP':None if mp is None or base is None else mp-base})
rows=sorted(rows,key=lambda x:abs(x['deltaMeanP'] or 0),reverse=True);out={'version':'R4_STATE_SHAPING_RUNTIME_DOMAIN_SHIFT_V1','researchOnly':True,'actionAuthority':False,'targetExternalFormationRows':len(ext),'runtimeDedupFormationAttempts':len(dedup),'runtimeMadeFormationParents':len(made),'targetExternalPositiveRate':float(ext.future_different_objective_5s.mean()),'targetExternalMeanP':float(ext.p_state_shaping_authorization.mean()),'runtimeMadeMeanP':base,'runtimeMadeTriggerRateAt05':float(np.mean(rdf.p>=.5)) if len(rdf) else None,'featureCounterfactuals':rows,'interpretation':'One-feature-at-a-time diagnostic only. No threshold/model tuning. Runtime integration should occur after successful Pair-Balance parent admission, not on failed/raw order attempts.'};(P/'r4_state_shaping_runtime_domain_shift_v1.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
