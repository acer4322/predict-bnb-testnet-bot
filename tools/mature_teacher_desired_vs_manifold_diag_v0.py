from __future__ import annotations
import json, statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
REP=OUT/'r2_candidate_v2_ourloss_targetwin21_replay_v1.json'
EPS=1e-9

def bal(u,d):
 s=u+d; return 0.0 if s<=EPS else 2*min(u,d)/s

def main():
 rep=json.loads(REP.read_text(encoding='utf-8')); allrows=[]; per=[]
 for r in rep['rows']:
  intents=sorted(r.get('makerIntents',[]),key=lambda x:int(x['atMs'])); fills=sorted(r.get('makerFills',[]),key=lambda x:int(x['atMs']))
  du=dd=au=ad=0.0; j=0; rows=[]
  for f in fills:
   t=int(f['atMs'])
   while j<len(intents) and int(intents[j]['atMs'])<=t:
    it=intents[j]; q=float(it['shares']); du+=q if it['side']=='UP' else 0; dd+=q if it['side']=='DOWN' else 0; j+=1
   b0=bal(au,ad); side=f['side']; q=float(f['deltaShares'])
   low='UP' if au<ad-EPS else 'DOWN' if ad<au-EPS else None
   deficit_u=du-au; deficit_d=dd-ad; desired_recovery='UP' if deficit_u>deficit_d+EPS else 'DOWN' if deficit_d>deficit_u+EPS else None
   au2=au+(q if side=='UP' else 0); ad2=ad+(q if side=='DOWN' else 0); b1=bal(au2,ad2)
   effect='IMPROVE' if b1>b0+1e-12 else 'WORSEN' if b1<b0-1e-12 else 'FLAT'
   rec={'marketId':r['marketId'],'atMs':t,'side':side,'qty':q,'balanceBefore':b0,'balanceAfter':b1,'effect':effect,'actualLowSide':low,'desiredLargerDeficitSide':desired_recovery,'fillIsActualRecovery':side==low if low else None,'fillIsDesiredRecovery':side==desired_recovery if desired_recovery else None,'actualUp':au,'actualDown':ad,'desiredUp':du,'desiredDown':dd,'deficitUp':deficit_u,'deficitDown':deficit_d,'desiredBalance':bal(du,dd)}
   rows.append(rec); allrows.append(rec); au,ad=au2,ad2
  hi=[x for x in rows if x['balanceBefore']>=0.8]
  per.append({'marketId':r['marketId'],'eventsHigh':len(hi),'worsenHigh':sum(x['effect']=='WORSEN' for x in hi),'desiredRecoveryWorsens':sum(bool(x['effect']=='WORSEN' and x['fillIsDesiredRecovery']) for x in hi),'actualRecoveryFills':sum(bool(x['fillIsActualRecovery']) for x in hi),'meanDesiredBalanceHigh':sum(x['desiredBalance'] for x in hi)/len(hi) if hi else None})
 hi=[x for x in allrows if x['balanceBefore']>=0.8]; wor=[x for x in hi if x['effect']=='WORSEN']
 result={'version':'MATURE_TEACHER_DESIRED_VS_MANIFOLD_DIAG_V0','researchOnly':True,'dreamFillUsed':False,'highBalanceThreshold':0.8,'aggregate':{'events':len(hi),'worsen':len(wor),'worsenRate':len(wor)/len(hi) if hi else None,'fillsActualRecovery':sum(bool(x['fillIsActualRecovery']) for x in hi),'actualRecoveryRate':sum(bool(x['fillIsActualRecovery']) for x in hi)/len(hi) if hi else None,'fillsDesiredRecovery':sum(bool(x['fillIsDesiredRecovery']) for x in hi),'desiredRecoveryRate':sum(bool(x['fillIsDesiredRecovery']) for x in hi)/len(hi) if hi else None,'worsenThatAreDesiredRecovery':sum(bool(x['fillIsDesiredRecovery']) for x in wor),'worsenDesiredRecoveryShare':sum(bool(x['fillIsDesiredRecovery']) for x in wor)/len(wor) if wor else None,'medianDesiredBalance':statistics.median(x['desiredBalance'] for x in hi) if hi else None},'perMarket':per,'rowsHighBalance':hi}
 out=OUT/'mature_teacher_desired_vs_manifold_diag_v0.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'aggregate':result['aggregate']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
