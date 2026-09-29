from __future__ import annotations
import json
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PH=P/'r4_p0b_phase_routed_shadow_controller_v1.json'
RM=P/'r4_p0b_responsibility_manager_shadow_state_machine_v1.json'
TG=P/'r4_p0b_target_objective_topology_rows_v2.csv'
OUT=P/'r4_management_testbed_v0_target_gap_audit.json'

def phase_band(s):
 s=float(s)
 if s>180:return 'FORMATION_180_300'
 if s>60:return 'MANAGEMENT_60_180'
 return 'PROTECTION_0_60'

def main():
 ph=json.loads(PH.read_text(encoding='utf-8')); tr=ph['trace']
 ours={}
 for band in ['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']:
  z=[r for r in tr if r.get('phase')==band]
  dec=Counter(str(r.get('shadow_decision')) for r in z)
  eligible=[r for r in z if int(r.get('build_now') or 0)==1]
  ours[band]={
   'rows':len(z),'markets':len({int(r['marketId']) for r in z}),
   'decisionCounts':dict(dec),'decisionRates':{k:v/len(z) for k,v in dec.items()} if z else {},
   'eligibleBuildRows':len(eligible),
   'meanFloor':float(np.mean([float(r.get('floor') or 0) for r in z])) if z else None,
   'meanAbsNet':float(np.mean([float(r.get('absNet') or 0) for r in z])) if z else None,
   'pendingSubmitRate':float(np.mean([bool(r.get('pendingSubmitResponsibilityIds')) for r in z])) if z else None,
   'pendingCancelRate':float(np.mean([bool(r.get('pendingCancelResponsibilityIds')) for r in z])) if z else None,
  }
 # Target objective topology: derive strict chronological objective-family switch/open rate at event timestamps.
 t=pd.read_csv(TG).sort_values(['market_id','t','parent_id']).copy(); t['band']=t.seconds_left.map(phase_band)
 target={}
 for band,g in t.groupby('band'):
  switches=[]; coexist=[]; fam=Counter(g.objective_family.astype(str))
  for mid,h in g.groupby('market_id'):
   prev=set()
   for tm,q in h.groupby('t',sort=True):
    cur=set(q.objective_family.astype(str))
    if prev: switches.extend([int(f not in prev) for f in cur])
    prev=cur
  coexist=((g.current_pair_balance.astype(int)>0)&(g.current_state_shaping.astype(int)>0)).mean()
  target[band]={
   'rows':int(len(g)),'markets':int(g.market_id.nunique()),
   'objectiveFamilyCounts':dict(fam),
   'objectiveFamilyRates':{k:v/len(g) for k,v in fam.items()},
   'strictFamilySwitchOpenRate':float(np.mean(switches)) if switches else None,
   'pairBalanceAndStateShapingCoexistRate':float(coexist),
   'meanCurrentCommitment':float(g.current_commitment.mean()),
   'meanAbsGap':float(g.abs_gap.mean()),'meanRiskDeficit':float(g.risk_deficit.mean()),
   'meanFloor':float(g.floor.mean()),'meanAbsNet':float(g.absNet.mean())
  }
 rm=json.loads(RM.read_text(encoding='utf-8'))
 known={
  'phaseRoutedShadowStatus':ph.get('status'),
  'responsibilityManagerStateMachineStatus':rm.get('status'),
  'responsibilityManagerIntegration':rm.get('integration'),
  'stage3Untouched16':'PROMOTION_FAILED_BA_0.4167_AUC_0.4167',
  'stage2ObjectiveLifecycle':'PROMOTION_PASSED_CONSUMED',
  'targetSameMarketOverlap':0,
  'targetComparisonMode':'aggregate matched lifecycle/time-band comparison; not same-market causal comparison'
 }
 findings=[]
 m=ours.get('MANAGEMENT_60_180',{}); tm=target.get('MANAGEMENT_60_180',{})
 if m:
  fallback=m.get('decisionRates',{}).get('OBSERVE',0)+m.get('decisionRates',{}).get('FALLBACK_R3',0)
  findings.append({'id':'MGT_SHADOW_ACTION_MASS','value':m.get('decisionRates',{}),'interpretation':'Current integrated plumbing produces a concrete distribution of CONTINUE/HANDOFF/OBSERVE decisions, so whole-trajectory testing is now feasible.'})
 if tm:
  findings.append({'id':'TARGET_PARALLEL_OBJECTIVE_COEXISTENCE','value':tm.get('pairBalanceAndStateShapingCoexistRate'),'interpretation':'Target frequently/occasionally carries objective families concurrently; this is the structural comparison signal the testbed should preserve rather than forcing one flat role.'})
 findings.append({'id':'STAGE3_PRIMARY_GAP','value':'candidate-specific execution feasibility/completion capacity','interpretation':'Untouched validation showed local economics alone cannot distinguish beneficial additive responsibility from no-effect/worsening paths.'})
 findings.append({'id':'JOURNAL_EDGE_GAP','value':{'status':rm.get('status'),'journalPrefixStateExact':rm.get('integration',{}).get('journalPrefixStateExact')},'interpretation':'The existing responsibility manager is nearly plumbing-exact but still has OPEN_UNOWNED/active-intent edge cases; keep shadow-only until repaired.'})
 out={'version':'R4_MANAGEMENT_TESTBED_V0_TARGET_GAP_AUDIT','researchOnly':True,'actionAuthority':False,'ours':ours,'targetReference':target,'knownState':known,'findings':findings,
      'interpretation':'Management Testbed V0 is suitable now for integrated research because the plumbing can run full lifecycle trajectories. It is not suitable for autonomous/live authority because Stage3 generalization failed and responsibility-state integration is not perfectly exact.'}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)),'oursManagement':ours.get('MANAGEMENT_60_180'),'targetManagement':target.get('MANAGEMENT_60_180'),'knownState':known,'findings':findings},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
