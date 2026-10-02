from pathlib import Path
import json, math, numpy as np
ROOT=Path(__file__).resolve().parents[1]; P=ROOT/'data/research/r4_v0/p0_provenance_v1'
d=json.loads((P/'r4_p0b_phase_routed_shadow_controller_v1.json').read_text(encoding='utf-8'))
rows=[r for r in d['trace'] if r.get('phase')=='MANAGEMENT_60_180' and int(r.get('build_now') or 0)==1]
# Diagnostic annotation only: use the stable qualitative completion-load findings, not a fitted action threshold.
# High load = requested responsibility >= remaining gap proxy; owner coverage = an existing weak responsibility is already active.
# requested_qty is not present in this old integrated trace, so infer the legacy fixed responsibility quantum=18 only for a sensitivity annotation.
# This cannot become runtime authority; purpose is to test whether whole-market failure classes concentrate by load/occupancy state.
out=[]
for r in rows:
 gap=max(abs(float(r.get('absNet') or 0)),18.0); load18=18.0/gap
 owners=len(r.get('weakResponsibilityIds') or []); pending=len(r.get('pendingSubmitResponsibilityIds') or [])
 teacher={'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}.get(r.get('future_management_label_5s'),'UNKNOWN')
 dec=r.get('shadow_decision'); fill=int(float(r.get('futureWeakMakerFill5s') or 0)>0); econ=int(float(r.get('floorImproved5s') or 0)>0 or float(r.get('absNetReduced5s') or 0)>0)
 out.append({'marketId':r['marketId'],'t':r['t'],'decision':dec,'teacher':teacher,'load18_gap':load18,'weakOwners':owners,'pendingSubmit':pending,'fill5s':fill,'econ5s':econ,'error':f'{dec}_VS_{teacher}' if teacher!='UNKNOWN' and dec!=teacher else 'MATCH' if teacher!='UNKNOWN' else 'UNKNOWN'})
# group anatomy, no threshold optimization
def agg(xs):
 if not xs:return {'n':0}
 return {'n':len(xs),'markets':len(set(x['marketId'] for x in xs)),'meanLoad18Gap':float(np.mean([x['load18_gap'] for x in xs])),'ownerPresentRate':float(np.mean([x['weakOwners']>0 for x in xs])),'pendingSubmitRate':float(np.mean([x['pendingSubmit']>0 for x in xs])),'fillRate5s':float(np.mean([x['fill5s'] for x in xs])),'econProgressRate5s':float(np.mean([x['econ5s'] for x in xs]))}
classes=sorted(set(x['error'] for x in out)); rep={'version':'R4_MANAGEMENT_TESTBED_V0_1_WHOLEMARKET_COMPLETION_ANNOTATIONS_V1','researchOnly':True,'actionAuthority':False,'strictPastAnnotation':True,'warning':'Old integrated trace lacks requested_qty; load18_gap uses legacy 18-share quantum as sensitivity proxy only. Do not fit or promote from this artifact.','rows':len(out),'markets':len(set(x['marketId'] for x in out)),'byError':{c:agg([x for x in out if x['error']==c]) for c in classes},'continueObserveContrast':{'continueVsObserve':agg([x for x in out if x['error']=='CONTINUE_VS_OBSERVE']),'continueMatch':agg([x for x in out if x['decision']=='CONTINUE' and x['teacher']=='CONTINUE'])},'interpretation':'Whole-market bridge diagnostic only. A fresh integrated runner must log requested_qty directly before Completion Capacity can be evaluated faithfully.'}
(P/'r4_management_testbed_v01_wholemarket_completion_annotations_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))