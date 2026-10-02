from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.audit_lane_g_post_first_event_parallel_candidate_fate_v1 import PostFirstEventCandidateFateAudit,EXPECTED,_copy_obligations
from tools.run_lane_g_shared_parent_parallel_residual_exact_fork_v1 import EPS,near

CASES={
 1946468:{'candidate':'DOWN_13','side':'DOWN','generation':1,'ownerGeneration':2,'repair':0.41593714964910133,'overflow':1.5071397734278216,'notional':0.7837126821824673,'positive':True},
 1946792:{'candidate':'UP_10','side':'UP','generation':1,'overflow':0.0,'positive':False},
}

class SatelliteOverflowR239OwnershipHandoff(PostFirstEventCandidateFateAudit):
 def __init__(self,tape,mid):
  super().__init__(tape,mid);self.handoffEvents=[];self.handoffFired=False;self.handoffReceipt=None
 def process(self,t):
  super().process(t)
  if self.handoffFired or not self.postEndpoint:return
  ep=self.postEndpoint
  oq=float(ep.get('candidateOverflowAllocated') or 0.0)
  if oq<=EPS:return
  # Existing R239 primitive: confirmed physical overflow only, source-key exact.
  before=len(getattr(self,'r239events',[]) or [])
  ev={'event':'ROLE_FILL_SPLIT','key':self.candidateKey,'role':'SATELLITE_REPAIR','side':self.spec['repairSide'],'price':float(self.spec['price']),'generationAtSubmit':int(self.spec['generation']),'overflowRealized':oq}
  self._register_overflow(int(ep['t']),ev)
  new=list((getattr(self,'r239events',[]) or [])[before:])
  self.handoffEvents.extend(new);self.handoffFired=True
  self.handoffReceipt={'t':int(ep['t']),'sourceEvent':ev,'r239Events':new,'nativeOwnersAfter':{'activeObligationId':getattr(self,'activeObligationId',None),'obligations':_copy_obligations(self)}}
 def run_owner(self,winner):
  raw=self.run_audit(winner);spec=CASES[self.mid];ep=self.postEndpoint or {};owner=[e for e in self.handoffEvents if e.get('event') in {'R239_OVERFLOW_OBLIGATION_BORN','R239_OVERFLOW_OBLIGATION_AUGMENTED'} and str(e.get('sourceKey'))==str(self.candidateKey)]
  base_geometry=self.candidateKey==spec['candidate'] and self.submitDeltaAtFork==1
  if spec['positive']:
   ob=next((x for x in _copy_obligations(self) if self.handoffReceipt and int(x.get('bornT') or -1)==int(self.handoffReceipt['t']) and self.candidateKey in (x.get('sourceKeys') or [])),None)
   correct=bool(base_geometry and self.handoffFired and len(owner)==1 and near(float(ep.get('candidateRepairAllocated') or 0.0),spec['repair'],2e-7) and near(float(ep.get('candidateOverflowAllocated') or 0.0),spec['overflow'],2e-7) and near(float(ep.get('candidateOverflowNotional') or 0.0),spec['notional'],2e-7) and owner[0].get('side')==spec['side'] and int(owner[0].get('generation'))==spec['ownerGeneration'] and near(float(owner[0].get('overflowQty') or owner[0].get('addQty') or 0.0),spec['overflow'],2e-7) and ob is not None and near(float(ob.get('outstanding') or 0.0),spec['overflow'],2e-7) and not self.errors and bool(raw.get('r264CorrectnessPass')) and float(raw.get('unauthorizedOverflowQty',0.0))<=EPS and float(raw.get('repairQuotaExcessMax',0.0))<=EPS)
  else:
   correct=bool(base_geometry and not self.handoffFired and len(owner)==0 and near(float(ep.get('candidateOverflowAllocated') or 0.0),0.0,2e-7) and not self.errors and bool(raw.get('r264CorrectnessPass')) and float(raw.get('unauthorizedOverflowQty',0.0))<=EPS and float(raw.get('repairQuotaExcessMax',0.0))<=EPS)
  return {'marketId':self.mid,'candidateKey':self.candidateKey,'trigger':self.trigger,'firstStructuralEvent':self.firstEvent,'postEndpoint':self.postEndpoint,'handoffFired':self.handoffFired,'handoffReceipt':self.handoffReceipt,'handoffEvents':self.handoffEvents,'nativeObligationsFinal':_copy_obligations(self),'errors':self.errors,'underlyingCorrect':bool(raw.get('r264CorrectnessPass')),'nativeUnauthorizedOverflowQty':float(raw.get('unauthorizedOverflowQty',0.0)),'nativeRepairQuotaExcessMax':float(raw.get('repairQuotaExcessMax',0.0)),'correct':correct}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946468,1946792');ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 tmp=Path(tempfile.mkdtemp(prefix='lane_g_satellite_r239_owner_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[]
  for m in mids:
   sim=SatelliteOverflowR239OwnershipHandoff(tmp/f'{m}.json.xz',m)
   try:r=sim.run_owner(co[m]['winner'])
   finally:sim.close()
   rows.append(r);print(json.dumps({'marketId':m,'handoffFired':r['handoffFired'],'handoffEvents':r['handoffEvents'],'candidateOverflow':(r.get('postEndpoint') or {}).get('candidateOverflowAllocated'),'correct':r['correct'],'errors':r['errors']},ensure_ascii=False),flush=True)
  pos=[r for r in rows if CASES[r['marketId']]['positive']];neg=[r for r in rows if not CASES[r['marketId']]['positive']]
  out={'version':'LANE_G_SATELLITE_OVERFLOW_R239_OWNERSHIP_HANDOFF_EXACT_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'gates':{'positiveOwnershipBorn':all(r['handoffFired'] and r['correct'] for r in pos),'negativeNoFalseOwner':all((not r['handoffFired']) and r['correct'] for r in neg),'correctnessPass':all(r['correct'] for r in rows)},'boundary':['single delta is R239 registration of already-confirmed SATELLITE_REPAIR shared-parent overflow','no physical order geometry/capacity/authority change before structural ownership horizon','same candidate realistic-HFT fills as parent audit','sourceKey exact ownership','no fixed-time horizon','consumed only','fresh untouched','no dream fill','no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
