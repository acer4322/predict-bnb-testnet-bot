from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
base=v3b.base; EPS=1e-9

class Shadow(v3b.FifoAggregateResponsibilityLadderV3B):
 def __init__(self,tape):
  super().__init__(tape); self.firstDecision=None
 def _snapshot_decision(self,t,side,role,a):
  qv=a.get('qv') or {}; u=float(self.inv['UP']); d=float(self.inv['DOWN']); cost=float(self.cost)
  dom='UP' if u>d+EPS else ('DOWN' if d>u+EPS else 'TIE')
  imb=float(qv.get('imb') or 0.0); depth='UP' if imb>=0 else 'DOWN'
  upb=float(qv['UP']['bid']); upa=float(qv['UP']['ask']); mid=.5*(upb+upa)
  price='UP' if mid>.5+EPS else ('DOWN' if mid<.5-EPS else 'NEUTRAL')
  exside=str(a['expandSide']); dq=list(self.resp_queues[exside]); agg=sum(float(x['remainingQty']) for x in dq)
  oldest=dq[0] if dq else None
  recent={}
  for hz in (1000,3000):
   xs=[x for x in self.fill_side_sequence if int(t)-int(x['t'])<=hz]
   recent[str(hz)]={'events':len(xs),'upQty':sum(float(x['incQty']) for x in xs if x['side']=='UP'),'downQty':sum(float(x['incQty']) for x in xs if x['side']=='DOWN'),'lastSide':xs[-1]['side'] if xs else None}
  return {'t':int(t),'repairSide':str(side),'role':str(role),'expandResponsibilitySide':exside,'bookImbalance':imb,'depthSide':depth,'upMid':mid,'priceSide':price,
          'dominantInventorySide':dom,'upQty':u,'downQty':d,'inventoryGap':abs(u-d),'cost':cost,'bestBranchSurplus':max(u,d)-cost,'floor':min(u,d)-cost,
          'depthAlignedDominant':bool(dom!='TIE' and depth==dom),'depthAlignedRepair':bool(depth==side),'priceAlignedDominant':bool(dom!='TIE' and price==dom),'priceAlignedRepair':bool(price==side),
          'aggregateDebt':agg,'lotCount':len(dq),'oldestRemaining':float(oldest['remainingQty']) if oldest else None,'oldestAgeMs':int(t)-int(oldest['bornAt']) if oldest else None,
          'oldestShare':float(oldest['remainingQty'])/agg if oldest and agg>EPS else None,'inheritedPrice':float(a.get('inheritedPrice') or 0.0),'passivePrice':float(a.get('passivePrice') or 0.0),
          'passivePairSumOldest':float(a.get('passivePairSumOldest') or 0.0),'recent':recent}
 def _submit_role(self,t,side,role,p,q,proj,source):
  a=dict(self.q_arm) if self.q_arm is not None else None
  before=len(self.q_events); ok=super()._submit_role(t,side,role,p,q,proj,source)
  if ok and self.firstDecision is None and a is not None and 'passivePrice' in a and abs(float(p)-float(a['passivePrice']))<=EPS:
   self.firstDecision=self._snapshot_decision(t,side,role,a)
  return ok
 def run_shadow(self):
  r=super().run_qty('__UNSCORED__'); r['firstEnhancedRepairDecision']=self.firstDecision; return r

def sig_place(sim,t):
 return [(int(x[0]),str(x[1]),round(float(x[2]),10),round(float(x[3]),10)) for x in list(sim.placeHist) if int(x[0])<int(t)]
def sig_fill(sim,t):
 return [(int(x['t']),str(x['side']),round(float(x['incQty']),10),round(float(x['price']),10),str(x['role'])) for x in sim.fill_side_sequence if int(x['t'])<int(t)]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 rows=[]
 with tempfile.TemporaryDirectory(prefix='asym_first_repair_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for i,mid in enumerate(mids,1):
   tape=root/'tapes'/f'{mid}.json.xz'; winner=str(co[mid]['winner']).upper()
   M=base.MinimalPairRoleSim(tape,4,False)
   try:rm=M.run_minimal('__UNSCORED__')
   finally:M.close()
   S=Shadow(tape)
   try:rv=S.run_shadow(); dec=rv.get('firstEnhancedRepairDecision'); t=dec['t'] if dec else None
   finally:S.close()
   mp=float(rm['upQty' if winner=='UP' else 'downQty'])-float(rm['buyNotional']); vp=float(rv['upQty' if winner=='UP' else 'downQty'])-float(rv['buyNotional'])
   prefixPlace=True if t is None else sig_place(M,t)==sig_place(S,t); prefixFill=True if t is None else sig_fill(M,t)==sig_fill(S,t)
   if mp>2 and vp<mp: group='UPSIDE_EROSION'
   elif mp<0 and vp>mp: group='DOWNSIDE_RESCUE'
   elif mp<0 and vp<=mp: group='ADVERSE_UNRESOLVED'
   elif mp>2: group='BIG_WIN_PRESERVED'
   else: group='OTHER'
   led=rv['quantityLedgerSummary']; invViol=sum(int(v) for k,v in led.items() if 'violation' in k.lower() and isinstance(v,(int,float)))
   row={'marketId':mid,'winnerPostHocOnly':winner,'minimalPnl':mp,'v3bPnl':vp,'deltaPnl':vp-mp,'group':group,'firstDecision':dec,'prefixPlaceParityStrictBefore':prefixPlace,'prefixFillParityStrictBefore':prefixFill,'ledgerSummary':led,'v3bFills':rv['fillEvents'],'v3bAlternations':rv['fillSideAlternations']}; rows.append(row)
   print(json.dumps({'progress':i,'marketId':mid,'group':group,'minimalPnl':mp,'v3bPnl':vp,'prefixPlace':prefixPlace,'prefixFill':prefixFill,'decision':dec},ensure_ascii=False),flush=True)
 out={'version':'ASYMMETRIC_FIRST_ENHANCED_REPAIR_DECISION_SHADOW_STAGEA16_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,
      'gates':{'allPrefixPlaceParityStrictBefore':all(r['prefixPlaceParityStrictBefore'] for r in rows),'allPrefixFillParityStrictBefore':all(r['prefixFillParityStrictBefore'] for r in rows)},
      'boundary':['behavior-inert shadow','features strict-past only','winner/minimal/v3b terminal PnL posthoc labels only','no runtime threshold','no NEW24-B','no 8781']}
 op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False))
if __name__=='__main__':main()
