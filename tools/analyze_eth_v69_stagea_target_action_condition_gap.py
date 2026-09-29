from __future__ import annotations
import argparse,glob,json
from pathlib import Path
import numpy as np
ROLES=['PASSIVE_REPAIR','ACTIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_EXPAND']
STAGEA=[1823603,1823769,1823894,1823897,1824037,1824747,1824852,1825353,1825959,1826030,1826386,1827223,1827418,1827903,1828268,1828768]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pattern',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();files=sorted(glob.glob(a.pattern));rows=[];sources=[];markets=set();safety={'truthMismatch':0.0,'overOwned':0.0,'repairDrift':0.0,'responsibilityOverfill':0.0}
 for p in files:
  d=json.load(open(p,encoding='utf-8'));sources.append({'path':p,'aggregate':d.get('aggregate'),'functionalPass':d.get('functionalPass')})
  for x in d.get('conditionRows',[]):rows.append(x);markets.add(int(x['marketId']))
  for k in safety:safety[k]+=float((d.get('aggregate') or {}).get(k) or 0.0)
 byrole={};allmissing={};lags=[]
 for role in ROLES:
  z=[x for x in rows if x.get('covered') and x['targetRole']==role];allowed=[x for x in z if bool((x.get('conditions') or {}).get('NEW_EXPOSURE_TIME_ALLOWED',True))];missing={};combos={}
  for x in allowed:
   if not x.get('sameRoleReadiness'):
    for m in x.get('missingConditions',[]):missing[m]=missing.get(m,0)+1
    c='+'.join(sorted(x.get('missingConditions',[]))) or 'NONE';combos[c]=combos.get(c,0)+1
  byrole[role]={'events':len(z),'timeAllowed':len(allowed),'semanticReady':sum(bool(x.get('semanticReady')) for x in allowed),'sameRoleReadiness':sum(bool(x.get('sameRoleReadiness')) for x in allowed),'exactTargetSideAlignment':sum(bool(x.get('exactTargetSideAlignment')) for x in allowed),'exactTargetActionReadiness':sum(bool(x.get('exactTargetActionReadiness')) for x in allowed),'endogenousSameRoleWithin1s':sum(x.get('nearestOurSameRoleDeltaMs') is not None and x['nearestOurSameRoleDeltaMs']<=1000 for x in allowed),'endogenousSameRoleWithin3s':sum(x.get('nearestOurSameRoleDeltaMs') is not None and x['nearestOurSameRoleDeltaMs']<=3000 for x in allowed),'missingConditionCounts':dict(sorted(missing.items(),key=lambda q:(-q[1],q[0]))),'topMissingCombinations':dict(sorted(combos.items(),key=lambda q:(-q[1],q[0]))[:12])}
  for k,v in missing.items():allmissing[k]=allmissing.get(k,0)+v
 for x in rows:
  if x.get('covered'):lags.append(int(x['snapshotLagMs']))
 missing_markets=[m for m in STAGEA if m not in markets];extra=sorted(markets-set(STAGEA));covered=sum(bool(x.get('covered')) for x in rows);ready=sum(bool(x.get('sameRoleReadiness')) for x in rows if x.get('covered') and bool((x.get('conditions') or {}).get('NEW_EXPOSURE_TIME_ALLOWED',True)));safe=all(abs(v)<=1e-9 for v in safety.values())
 gates={'completeStageA':not missing_markets and not extra,'strictPastCoverage':covered/max(1,len(rows))>=.9,'allRolesRepresented':all(byrole[r]['events']>0 for r in ROLES),'conditionVariation':ready>0 and ready<covered and len(allmissing)>=4,'allAccountingSafe':safe}
 out={'version':'TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP','researchOnly':True,'actionAuthority':False,'trainingDataOnly':True,'coverage':{'files':len(files),'markets':sorted(markets),'missingStageA':missing_markets,'extraMarkets':extra,'targetEvents':len(rows),'covered':covered,'coverageRate':covered/max(1,len(rows)),'medianSnapshotLagMs':None if not lags else float(np.median(lags)),'maxSnapshotLagMs':None if not lags else max(lags)},'roles':byrole,'allMissingConditionCounts':dict(sorted(allmissing.items(),key=lambda q:(-q[1],q[0]))),'safety':safety,'gates':gates,'functionalPass':all(gates.values()),'conditionRows':rows,'sources':sources,'interpretationBoundary':['Target action clock supplies diagnostic curriculum indices only.','Same-role readiness uses OUR inventory/thesis side; exact Target-side readiness is separate.','Rows are condition-gap supervision, not positive action labels.','A later action label still requires OUR realistic-HFT counterfactual materialization and Repair coverage.']};Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'coverage':out['coverage'],'roles':byrole,'safety':safety},ensure_ascii=False))
if __name__=='__main__':main()
