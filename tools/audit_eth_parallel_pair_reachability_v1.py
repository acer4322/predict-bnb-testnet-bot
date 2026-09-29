from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_dagger60_smoke_v1 as v1
from tools.run_eth_repair_functional_exam_v1 import role_from_inv
EPS=1e-9
MIDS=[1816356,1816646,1816800,1817999,1818835,1818992,1821664,1823488]
REP={1816356:'DOWN',1816646:'DOWN',1816800:'UP',1817999:'UP',1818835:'DOWN',1818992:'DOWN',1821664:'UP',1823488:'UP'}

class PairSim(v1.Sim):
 def __init__(self,tape,repair_side,dom_qty,repair_qty=12.,ttl_ms=12000):
  super().__init__(tape,None,'FORCE_UP');self.rep=repair_side;self.dom='DOWN' if repair_side=='UP' else 'UP';self.domq=float(dom_qty);self.repq=float(repair_qty);self.ttl=int(ttl_ms);self.first_valid=None;self.keys={};self.events=[];self.partialRepair=[];self.repairFirstFillTruthRole=None
 def cancel_expired(self,t):
  for o in self.orders.values():
   s=self.snap(o)
   if v1.live(s.get('status')) and t-o['placed']>=self.ttl:
    cur=self.bt.orders(0).get(o['n'])
    if cur is not None and bool(cur.cancellable):
     try:self.bt.cancel(0,o['n'],False)
     except Exception:pass
 def process(self,t):
  for key,o in self.orders.items():
   s=self.snap(o);cum=float(s.get('cumExecQty') or 0.);inc=max(0.,cum-o['cum']);st=s.get('status')
   if inc>EPS:
    pre=dict(self.inv);truth_role=role_from_inv(pre,o['side']);p=v1.fill_price(o['side'],s,o['price']);self.record_fill(t,o['side'],inc,p);self.fills+=1;o['cum']=cum
    rec={'t':int(t),'key':key,'side':o['side'],'inc':float(inc),'cum':float(cum),'qty':float(o['qty']),'status':st,'truthRoleBeforeFill':truth_role,'preInv':pre.copy(),'postInv':dict(self.inv),'leaves':float(s.get('leavesQty') or max(0.,o['qty']-cum))};self.events.append(rec)
    if key==self.keys.get('repair'):
     if self.repairFirstFillTruthRole is None:self.repairFirstFillTruthRole=truth_role
     if st=='PARTIALLY_FILLED' or (cum>EPS and cum<float(o['qty'])-EPS):self.partialRepair.append(rec)
   o['status']=st
 def unresolved(self,key):
  o=self.orders.get(key)
  if not o:return 0.
  s=self.snap(o);st=s.get('status')
  if st in {'FILLED','CANCELED','CANCELLED','REJECTED','EXPIRED'}:return 0.
  return max(0.,float(o['qty'])-float(o.get('cum') or 0.))
 def run(self):
  ups=sorted(self.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(self.meta['firstReceivedMs']);v1.ex.advance_to(self.bt,first);end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs']);submitted=False;both_unresolved_at_partial=False
  for u in ups:
   t=int(u[1]);v1.ex.advance_to(self.bt,t);self.process(t);self.cancel_expired(t);v1.apply(self.book,u);qv=v1.quotes(self.book)
   if not qv:continue
   if self.first_valid is None:self.first_valid=t
   if not submitted and t-self.first_valid>=2000 and (end-t)/1000>180:
    pd=float(qv[self.dom]['bid']);qd=max(self.domq,1/pd);self.submit(t,self.dom,pd,qd);self.keys['dom']=f'{self.dom}_{self.n-1}'
    pr=float(qv[self.rep]['bid']);qr=max(self.repq,1/pr);self.submit(t,self.rep,pr,qr);self.keys['repair']=f'{self.rep}_{self.n-1}';submitted=True
   if self.partialRepair:
    both_unresolved_at_partial=(self.unresolved(self.keys['dom'])>EPS and self.unresolved(self.keys['repair'])>EPS);break
  return {'marketId':int(self.payload['marketId']),'repairSide':self.rep,'domSide':self.dom,'domQty':self.domq,'repairQty':self.repq,'partialRepairSeen':bool(self.partialRepair),'repairFirstFillTruthRole':self.repairFirstFillTruthRole,'bothUnresolvedAtRepairPartial':both_unresolved_at_partial,'partialRepair':self.partialRepair[:2],'events':self.events[:6]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output');ap.add_argument('--dom-qtys',default='5,8,12');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_pair_reach_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);qs=[float(x) for x in a.dom_qtys.split(',') if x.strip()];rows=[]
  for mid in MIDS:
   for q in qs:
    sim=PairSim(tmp/'tapes'/f'{mid}.json.xz',REP[mid],q)
    try:r=sim.run()
    finally:sim.close()
    rows.append(r);print(json.dumps({'mid':mid,'domQty':q,'partial':r['partialRepairSeen'],'truthRole':r['repairFirstFillTruthRole'],'bothUnresolved':r['bothUnresolvedAtRepairPartial']}),flush=True)
  hits=[r for r in rows if r['partialRepairSeen'] and r['repairFirstFillTruthRole']=='REPAIR'];concurrent=[r for r in hits if r['bothUnresolvedAtRepairPartial']]
  out={'version':'ETH_PARALLEL_PAIR_REACHABILITY_V1','researchOnly':True,'rows':rows,'repairPartialTruthRepairHits':len(hits),'concurrentUnresolvedHits':len(concurrent),'hitMarkets':sorted(set(r['marketId'] for r in hits)),'concurrentMarkets':sorted(set(r['marketId'] for r in concurrent)),'boundary':['actual HftBacktest passive bids only','dominant and repair orders submitted same tick, dominant first','no dream/synthetic fills','reachability only; not policy performance']}
  op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'repairTruthHits':len(hits),'concurrentHits':len(concurrent),'hitMarkets':out['hitMarkets'],'concurrentMarkets':out['concurrentMarkets']}),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
