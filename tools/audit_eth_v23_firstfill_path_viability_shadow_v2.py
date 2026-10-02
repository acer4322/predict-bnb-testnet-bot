from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,os,math,statistics
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_repair_functional_exam_v23_queue_progress_lease as v23
EPS=1e-9; GRID=.01

def qtile(xs,q):
 ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not ys:return None
 z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
 return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
 ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':qtile(ys,.25),'p75':qtile(ys,.75),'p90':qtile(ys,.9)}

class FirstFillPathShadow(v23.QueueProgressLeaseSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.pathCycles=[];self._pathByToken={}
 def _target_best_bid(self,side):
  if side=='UP':return float(max(self.book.get('bids',{}))) if self.book.get('bids') else None
  if side=='DOWN':return 1.0-float(min(self.book.get('asks',{}))) if self.book.get('asks') else None
  return None
 def _target_best_ask(self,side):
  if side=='UP':return float(min(self.book.get('asks',{}))) if self.book.get('asks') else None
  if side=='DOWN':return 1.0-float(max(self.book.get('bids',{}))) if self.book.get('bids') else None
  return None
 def _token(self,rb):
  return f"{rb.get('firstKey')}:{rb.get('firstFillAt')}"
 def _frontier_state(self,rb,t):
  opp='DOWN' if rb.get('firstSide')=='UP' else 'UP';ceiling=1.0-float(rb.get('firstPrice') or 0.0)-0.01
  bb=self._target_best_bid(opp);ba=self._target_best_ask(opp)
  behind=None if bb is None else max(0.0,(float(bb)-ceiling)/GRID)
  raw=None if ba is None else min(ceiling,float(ba)-0.01)
  passive=None if raw is None else math.floor((raw+1e-10)*100.0)/100.0
  passiveBehind=None if bb is None or passive is None else max(0.0,(float(bb)-passive)/GRID)
  return {'t':int(t),'oppSide':opp,'economicCeiling':float(ceiling),'bestBid':bb,'bestAsk':ba,'ceilingBehindBestTicks':behind,'legalPassivePrice':passive,'legalPassiveBehindBestTicks':passiveBehind}
 def process(self,t):
  pre=self.reserveBuilder;pre_first=pre.get('firstFillAt') if pre else None
  super().process(t)
  # pre is a live object reference and may have been mutated/reset by the parent call.
  if pre is not None and pre_first is None and pre.get('firstFillAt') is not None:
   tok=self._token(pre);s=self._frontier_state(pre,t)
   rec={'token':tok,'firstKey':pre.get('firstKey'),'firstFillAt':int(pre['firstFillAt']),'firstSide':pre.get('firstSide'),'firstPrice':float(pre.get('firstPrice') or 0.0),'firstFilledQty':float(pre.get('firstFilledQty') or 0.0),'pairDeadline':int(pre.get('pairDeadline') or 0),'completed':False,'repairFillPrice':None,'repairFillAt':None,
        'firstFillEconomicCeiling':s['economicCeiling'],'firstFillBestBid':s['bestBid'],'firstFillBestAsk':s['bestAsk'],'firstFillCeilingBehindBestTicks':s['ceilingBehindBestTicks'],'firstFillLegalPassivePrice':s['legalPassivePrice'],'firstFillLegalPassiveBehindBestTicks':s['legalPassiveBehindBestTicks'],
        'minCeilingBehindBestTicks':s['ceilingBehindBestTicks'],'maxCeilingBehindBestTicks':s['ceilingBehindBestTicks'],'minLegalPassiveBehindBestTicks':s['legalPassiveBehindBestTicks'],'maxLegalPassiveBehindBestTicks':s['legalPassiveBehindBestTicks'],'within1TickSeen':bool(s['legalPassiveBehindBestTicks'] is not None and s['legalPassiveBehindBestTicks']<=1.0+EPS),'firstWithin1TickAt':int(t) if s['legalPassiveBehindBestTicks'] is not None and s['legalPassiveBehindBestTicks']<=1.0+EPS else None,'samples':1}
   self._pathByToken[tok]=rec;self.pathCycles.append(rec)
  rb=self.reserveBuilder
  if rb is not None and rb.get('firstFillAt') is not None:
   tok=self._token(rb);rec=self._pathByToken.get(tok)
   if rec is not None:
    s=self._frontier_state(rb,t);rec['samples']+=1
    for k0,kmin,kmax in [('ceilingBehindBestTicks','minCeilingBehindBestTicks','maxCeilingBehindBestTicks'),('legalPassiveBehindBestTicks','minLegalPassiveBehindBestTicks','maxLegalPassiveBehindBestTicks')]:
     x=s[k0]
     if x is not None:
      rec[kmin]=float(x) if rec[kmin] is None else min(float(rec[kmin]),float(x));rec[kmax]=float(x) if rec[kmax] is None else max(float(rec[kmax]),float(x))
    if s['legalPassiveBehindBestTicks'] is not None and s['legalPassiveBehindBestTicks']<=1.0+EPS:
     rec['within1TickSeen']=True
     if rec['firstWithin1TickAt'] is None:rec['firstWithin1TickAt']=int(t)
  # If parent processing completed/reset a builder, the pre reference still tells us how it ended.
  if pre is not None and pre.get('firstFillAt') is not None:
   tok=self._token(pre);rec=self._pathByToken.get(tok)
   if rec is not None:
    rec['completed']=bool(pre.get('completed'));rec['repairFillPrice']=float(pre['repairFillPrice']) if pre.get('repairFillPrice') is not None else None;rec['repairFillAt']=int(pre['repairFillAt']) if pre.get('repairFillAt') is not None else None
 def finalize_paths(self):
  for r in self.pathCycles:
   r['firstToRepairFillMs']=None if r.get('repairFillAt') is None else int(r['repairFillAt'])-int(r['firstFillAt'])
   r['firstToWithin1TickMs']=None if r.get('firstWithin1TickAt') is None else int(r['firstWithin1TickAt'])-int(r['firstFillAt'])
  return self.pathCycles

def block(rr):
 return {'cycles':len(rr),'markets':len({r['marketId'] for r in rr}),'completionRate':sum(bool(r['completed']) for r in rr)/len(rr) if rr else None,
         'firstFillCeilingBehindBestTicks':stats([r['firstFillCeilingBehindBestTicks'] for r in rr]),'firstFillLegalPassiveBehindBestTicks':stats([r['firstFillLegalPassiveBehindBestTicks'] for r in rr]),
         'minLegalPassiveBehindBestTicks':stats([r['minLegalPassiveBehindBestTicks'] for r in rr]),'maxLegalPassiveBehindBestTicks':stats([r['maxLegalPassiveBehindBestTicks'] for r in rr]),
         'within1TickSeenRate':sum(bool(r['within1TickSeen']) for r in rr)/len(rr) if rr else None,'firstToWithin1TickMs':stats([r['firstToWithin1TickMs'] for r in rr]),'firstToRepairFillMs':stats([r['firstToRepairFillMs'] for r in rr])}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v23_pathshadow_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,life,cap,tim,econ,price,sur=v23.load_runtime(a);rows=[];markets=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=FirstFillPathShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur)
   try:r=sim.run_exam_v23(models,cr['winner']);zz=sim.finalize_paths()
   finally:sim.close()
   for x in zz:x['marketId']=mid
   rows.extend(zz);markets.append({'marketId':mid,'firstFills':r['reserveFirstLegActualFill'],'cycles':r['reserveCycleCompletion'],'pathCycles':len(zz)});print(json.dumps({'marketId':mid,'first':r['reserveFirstLegActualFill'],'cycles':r['reserveCycleCompletion'],'paths':len(zz),'completedPaths':sum(bool(x['completed']) for x in zz)},ensure_ascii=False),flush=True)
  comp=[r for r in rows if r['completed']];fail=[r for r in rows if not r['completed']]
  out={'version':'ETH_V23_FIRSTFILL_PATH_VIABILITY_SHADOW_V2','researchOnly':True,'behaviorChange':False,'selectedMarketIds':[m['marketId'] for m in markets],'marketRows':markets,'summary':{'all':block(rows),'completed':block(comp),'failed':block(fail)},'rows':rows,'boundary':['V23 behavior unchanged; first-fill and live-frontier states are shadow-recorded only.','Economic ceiling remains 1-firstPrice-0.01; legal passive price mirrors V19 package price geometry descriptively.','Public best bid/ask are receipt-clock book state, not private queue rank.','No thresholds are promoted from this audit; no PnL tuning; consumed markets only.']}
  op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
