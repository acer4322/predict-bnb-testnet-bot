from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
 from tools import run_eth_repair_functional_exam_v4_persistent_carrier_ledger as v4
except ImportError:
 p4=Path(__file__).resolve().with_name('run_eth_repair_functional_exam_v4_persistent_carrier_ledger.py');sp=importlib.util.spec_from_file_location('v4_seedrepair',p4);v4=importlib.util.module_from_spec(sp);sp.loader.exec_module(v4)
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_dagger60_smoke_v1 as v1
EPS=1e-9
MIDS=[1816356,1816646,1816800,1817999,1818835,1818992,1821664,1823488]
REP={1816356:'DOWN',1816646:'DOWN',1816800:'UP',1817999:'UP',1818835:'DOWN',1818992:'DOWN',1821664:'UP',1823488:'UP'}
TERMINAL={'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}

def run_one(sim,repair_side,seed_qty=12.,repair_max=12.):
 dom='DOWN' if repair_side=='UP' else 'UP';ups=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);v1.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs']);first_valid=None;seed_key=None;repair_key=None;seed_cancel_requested=False;repair_partial=[];events=[]
 for u in ups:
  t=int(u[1]);v1.ex.advance_to(sim.bt,t);pre=dict(sim.truthInv);sim.process(t);sim._refresh_carrier_ledger(t)
  # capture fill deltas from ledger cum changes by comparing truth inventory delta
  for side in ('UP','DOWN'):
   d=float(sim.truthInv[side])-float(pre[side])
   if d>EPS: events.append({'t':t,'side':side,'incTruth':d,'truthInv':dict(sim.truthInv)})
  v1.apply(sim.book,u);qv=v1.quotes(sim.book)
  if not qv:continue
  if first_valid is None:first_valid=t
  if seed_key is None and t-first_valid>=2000 and (end-t)/1000>180:
   p=float(qv[dom]['bid']);q=max(float(seed_qty),1/p);sim._pendingAuthorizedRole='EXPAND';sim._pendingAuthorizedObjectiveId=100;sim.submit(t,dom,p,q);seed_key=f'{dom}_{sim.n-1}'
  if seed_key and not seed_cancel_requested and float(sim.truthInv[dom])>EPS:
   sim._cancel_key(t,seed_key);seed_cancel_requested=True
  if seed_key and repair_key is None:
   e=sim.carrierLedger.get(seed_key);seed_terminal=bool(e and (e.get('terminalConfirmed') or sim._ledger_remaining(e)<=EPS))
   if seed_terminal and float(sim.truthInv[dom])>float(sim.truthInv[repair_side])+EPS and (end-t)/1000>180:
    gap=float(sim.truthInv[dom])-float(sim.truthInv[repair_side]);p=float(qv[repair_side]['bid']);legal=1/p if p>EPS else 1e9;q=min(float(repair_max),gap)
    if q>=legal-EPS:
     sim._pendingAuthorizedRole='REPAIR';sim._pendingAuthorizedObjectiveId=200;sim.submit(t,repair_side,p,q);repair_key=f'{repair_side}_{sim.n-1}'
  if repair_key:
   o=sim.orders.get(repair_key);s=sim.snap(o);cum=float(s.get('cumExecQty') or 0.);st=s.get('status')
   if cum>EPS and cum<float(o['qty'])-EPS:
    repair_partial.append({'t':t,'cum':cum,'qty':float(o['qty']),'leaves':float(s.get('leavesQty') or o['qty']-cum),'status':st,'truthInv':dict(sim.truthInv)});break
 return {'marketId':int(sim.payload['marketId']),'repairSide':repair_side,'domSide':dom,'seedKey':seed_key,'seedTruthFilled':float(sim.truthInv[dom]),'seedCanceledAfterFill':seed_cancel_requested,'repairKey':repair_key,'repairSubmitted':repair_key is not None,'repairPartialSeen':bool(repair_partial),'repairPartial':repair_partial[:2],'overOwnedViolations':sim.overOwnedSubmitViolations,'events':events[:12]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v4_seed_repair_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));rows=[]
  for mid in MIDS:
   sim=v4.PersistentCarrierSim(tmp/'tapes'/f'{mid}.json.xz','FORCE_UP',models,life,0,0)
   try:r=run_one(sim,REP[mid])
   finally:sim.close()
   rows.append(r);print(json.dumps({'mid':mid,'seedFill':r['seedTruthFilled'],'repairSubmitted':r['repairSubmitted'],'partial':r['repairPartialSeen'],'overOwned':r['overOwnedViolations']}),flush=True)
  hits=[r for r in rows if r['repairPartialSeen']];out={'version':'ETH_V4_SEED_THEN_REPAIR_PARTIAL_REACHABILITY_V1','researchOnly':True,'rows':rows,'partialRepairHits':len(hits),'hitMarkets':[r['marketId'] for r in hits],'allHitOverOwnedZero':all(r['overOwnedViolations']==0 for r in hits),'boundary':['V4 persistent carrier execution lifecycle','actual HftBacktest passive fills only','dominant seed must materially fill then remaining seed carrier is canceled/terminal before Repair submit','Repair qty capped by authoritative truth gap and legal minimum','no dream/synthetic fill']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'partialRepairHits':len(hits),'hitMarkets':out['hitMarkets'],'allHitOverOwnedZero':out['allHitOverOwnedZero']}),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
