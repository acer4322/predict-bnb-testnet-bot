from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path: sys.path.insert(0,str(STAGING))
import run_eth_dagger60_smoke_v1 as v1
import run_eth_dagger60_pending_responsibility_conservation_v1 as pr

class LocalReservedBootSim(pr.ConservedBootSim):
 def __init__(self,tape,mode,models):
  super().__init__(tape,mode,models);self.localPending={'UP':{},'DOWN':{}};self.localPendingBlocked=0
 def local_reserved(self,side):
  return sum(float(v.get('remaining',0.0)) for v in self.localPending[side].values())
 def reserved_authoritative(self,side):
  # Venue-visible reservation plus locally submitted-but-not-yet-visible responsibility.
  # Do not double-count an order once venue state is visible: local entry is reconciled in process().
  return self.reserved(side)+self.local_reserved(side)
 def process(self,t):
  super().process(t)
  # Reconcile local pending responsibility against venue snapshots. Once an order is observable,
  # venue leavesQty becomes authoritative and local pre-ack reservation is released.
  for side in ('UP','DOWN'):
   for key in list(self.localPending[side]):
    o=self.orders.get(key)
    if o is None:
     self.localPending[side].pop(key,None);continue
    s=self.snap(o);status=s.get('status')
    if status is not None:
     self.localPending[side].pop(key,None)
 def submit(self,t,side,p,q):
  # Default remains one same-side locally/venue-reserved carrier. A narrowly scoped research kernel may opt in
  # to same-side parallel submission only after its higher-level shared-responsibility capacity check has passed.
  allow_parallel_same_responsibility=bool(getattr(self,'_allowSameSideParallelReservation',False))
  if self.reserved_authoritative(side)>v1.EPS and not allow_parallel_same_responsibility:
   self.duplicateBlocked+=1;self.localPendingBlocked+=1;return False
  # Bypass parent's duplicate check because authoritative/local responsibility is reconciled here;
  # opt-in parallel users remain responsible for aggregate capacity conservation in the persistent carrier ledger.
  n=self.n;self.n+=1
  v1.ex.submit_native(self.bt,n,side,float(p),float(q))
  key=f'{side}_{n}'
  self.orders[key]={'n':n,'side':side,'price':float(p),'qty':float(q),'cum':0.,'placed':t,'status':'NEW'}
  self.placeHist.append((t,side,q,p));self.submits+=1
  self.localPending[side][key]={'remaining':float(q),'submitted':t}
  return True

def train_models(tmp,cohort,traj):
 return pr.train_models(tmp,cohort,traj)

def agg(rs):
 buy=sum(r['buyNotional'] for r in rs);p=sum(r['pnl'] for r in rs);active=[r for r in rs if r['buyNotional']>v1.EPS]
 return {'markets':len(rs),'activeMarkets':len(active),'pnl':p,'buyNotional':buy,'roi':p/buy if buy else None,'winRate':sum(r['pnl']>0 for r in rs)/len(rs) if rs else None,'activeWinRate':sum(r['pnl']>0 for r in active)/len(active) if active else None,'meanBuy':statistics.mean(r['buyNotional'] for r in rs),'meanPairCoverage':statistics.mean(r['pairCoverage'] for r in rs),'positiveFloorRate':sum(r['floor']>=0 for r in rs)/len(rs),'meanAbsNet':statistics.mean(r['absNet'] for r in rs),'meanSubmits':statistics.mean(r['submits'] for r in rs),'meanFills':statistics.mean(r['fills'] for r in rs),'meanDuplicateBlocked':statistics.mean(r.get('duplicateBlocked',0) for r in rs),'meanLocalPendingBlocked':statistics.mean(r.get('localPendingBlocked',0) for r in rs),'maxWin':max(r['pnl'] for r in rs),'maxLoss':min(r['pnl'] for r in rs),'leaveOneBestOutPnl':p-max(r['pnl'] for r in rs),'worstCounterfactual':min(min(r['pnl'],r['oppositePnl']) for r in rs),'maxCounterfactualAdverseOnWinner':min((r['oppositePnl'] for r in rs if r['pnl']>0),default=None)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_dagger_local_pending_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=train_models(tmp,cohort,traj);test=[r for r in cohort if r['split']=='TEST20'];rows=[]
  for i,cr in enumerate(test,1):
   sim=LocalReservedBootSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models)
   try:r=sim.run_student(models,cr['winner'])
   finally:sim.close()
   r.update({'marketId':int(cr['marketId']),'winner':cr['winner'],'targetPnl':cr['targetPnl'],'targetBuy':cr['targetBuy'],'duplicateBlocked':sim.duplicateBlocked,'localPendingBlocked':sim.localPendingBlocked});opp='UP' if cr['winner']=='DOWN' else 'DOWN';r['oppositePnl']=r[opp.lower()]-r['buyNotional'];rows.append(r)
   if i%5==0:print(json.dumps({'testProgress':i,'pnlSoFar':sum(x['pnl'] for x in rows)}),flush=True)
  summary=agg(rows);out={'version':'ETH_DAGGER60_LOCAL_PENDING_RESERVATION_V1','boundary':['exact bootstrap-v3 two-round DAgger retrain','TEST20 BOOK_IMBALANCE seed','no Target objective/trajectory/outcome at runtime','only strategy change relative to pending-responsibility V1: submitted same-side qty is reserved locally immediately before venue acknowledgement and reconciled when venue state becomes observable','<=180s no new exposure; Maker-only realistic HFT'],'round1Offline':off1,'round2Offline':off2,'summary':summary,'rows':rows};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
