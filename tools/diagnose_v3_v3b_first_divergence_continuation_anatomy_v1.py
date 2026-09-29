from __future__ import annotations
import argparse,collections,json,math,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

v3=v3b.v3;base=v3b.base;Q=v3b.qshadow;EPS=v3b.EPS;TICK=v3b.TICK
WINDOWS=(1000,3000,5000,10000)

def opp(s):return 'DOWN' if s=='UP' else 'UP'

def live_slots(sim,t):
 out=[]
 for sid,key,o,role in sim._live_role_rows():
  out.append({'slotId':int(sid),'key':key,'role':role,'side':str(o.get('side')),'price':float(o.get('price') or 0),'qty':float(o.get('qty') or 0),'cum':float(o.get('cum') or 0),'remaining':max(0.0,float(o.get('qty') or 0)-float(o.get('cum') or 0)),'ageMs':max(0,int(t)-int(o.get('placed') or t)),'cancelRequested':bool(o.get('cancelRequested'))})
 return sorted(out,key=lambda x:x['slotId'])

def debt_state(sim,t):
 out={s:float(sum(float(x['remainingQty']) for x in sim.resp_queues[s])) for s in ('UP','DOWN')}
 side='UP' if out['UP']>EPS else ('DOWN' if out['DOWN']>EPS else None)
 lots=list(sim.resp_queues[side]) if side else []
 total=out['UP']+out['DOWN'];old=lots[0] if lots else None
 wpx=sum(float(x['remainingQty'])*float(x['price']) for x in lots)/total if total>EPS else None
 return {'outstandingUP':out['UP'],'outstandingDOWN':out['DOWN'],'outstandingTotal':total,'expandDebtSide':side,'repairSide':opp(side) if side else None,'lotCount':len(lots),'oldestRemaining':float(old['remainingQty']) if old else 0.0,'oldestAgeMs':max(0,int(t)-int(old['bornAt'])) if old else 0,'oldestPrice':float(old['price']) if old else None,'weightedDebtPrice':wpx}

def recent(sim,t,ms):
 z=[x for x in sim.fill_accounting if int(x['t'])<int(t) and int(x['t'])>=int(t)-int(ms)]
 byrole=collections.Counter();byside=collections.Counter();qtyrole=collections.defaultdict(float);qtyside=collections.defaultdict(float)
 for x in z:
  r=str(x.get('role') or 'UNKNOWN');s=str(x.get('side'));q=float(x.get('confirmedQty') or 0);byrole[r]+=1;byside[s]+=1;qtyrole[r]+=q;qtyside[s]+=q
 return {'fillClocks':len({int(x['t']) for x in z}),'fillEvents':len(z),'byRole':dict(byrole),'bySide':dict(byside),'qtyByRole':dict(qtyrole),'qtyBySide':dict(qtyside)}

def qview(qv):
 return {s:{'bid':float(qv[s]['bid']),'ask':float(qv[s]['ask']),'spreadTicks':float(qv[s]['ask']-qv[s]['bid'])/TICK} for s in ('UP','DOWN')}

def state_sig(s):
 return (round(s['invUP'],9),round(s['invDOWN'],9),round(s['cost'],9),tuple((x['role'],x['side'],round(x['price'],6),round(x['remaining'],6),x['cancelRequested']) for x in s['liveSlots']),round(s['debt']['outstandingTotal'],9),s['debt']['lotCount'])

class TraceMixin:
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.diag_receipts=[]
 def _open_one_option(self,t,qv,end):
  state,held,weak=self._state();side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv);sig=self._direction(qv)
  rec={'t':int(t),'timeRemainingMs':int(end)-int(t),'portfolioState':state,'held':held,'weak':weak,'directionSignal':sig,'ordinaryRoleSide':side,'ordinaryRole':role,'invUP':float(self.inv['UP']),'invDOWN':float(self.inv['DOWN']),'cost':float(self.cost),'floor':float(self._physical_floor()),'best':float(max(self.inv.values())-self.cost),'freeSlots':int(self.max_slots-len(self.slot_key)),'liveSlots':live_slots(self,t),'debt':debt_state(self,t),'book':qview(qv),'recent1s':recent(self,t,1000),'recent3s':recent(self,t,3000),'recent5s':recent(self,t,5000)}
  pb=len(self.placeHist);sb=len(self.slot_history);qb=len(self.q_events)
  out=super()._open_one_option(t,qv,end)
  rec['newPlacements']=[{'t':int(x[0]),'side':str(x[1]),'qty':float(x[2]),'price':float(x[3])} for x in list(self.placeHist)[pb:]]
  rec['newSlotEvents']=[x for x in self.slot_history[sb:] if int(x.get('t',-1))==int(t)]
  rec['newQEvents']=[x for x in self.q_events[qb:] if int(x.get('t',-1))==int(t)]
  rec['stateSignature']=state_sig(rec);self.diag_receipts.append(rec)
  return out

class TraceV3(TraceMixin,v3.QuantityResponsibilityLadderV3):pass
class TraceV3B(TraceMixin,v3b.FifoAggregateResponsibilityLadderV3B):pass

def placement_sig(r):return tuple((x['side'],round(x['price'],8),round(x['qty'],8)) for x in r.get('newPlacements',[]))
def find_first_div(a,b):
 A={int(x['t']):x for x in a};B={int(x['t']):x for x in b}
 for t in sorted(set(A)&set(B)):
  x,y=A[t],B[t]
  if placement_sig(x)!=placement_sig(y):return t,'PLACEMENT',x,y
  # catch pre-open state divergence caused by earlier retention/cancel/fill differences
  if x.get('stateSignature')!=y.get('stateSignature'):return t,'PREOPEN_STATE',x,y
 return None,None,None,None

def next_snapshot(rows,t):
 z=[x for x in rows if int(x['t'])>=int(t)]
 return z[0] if z else (rows[-1] if rows else None)
def window_outcome(sim,trace,t0,w):
 end=int(t0)+int(w);fs=[x for x in sim.fill_accounting if int(t0)<int(x['t'])<=end];pays=[x for x in sim.resp_payment_rows if int(t0)<int(x['t'])<=end];birth=[x for x in sim.resp_all if int(t0)<int(x['bornAt'])<=end]
 side_seq=[str(x['side']) for x in fs];alts=sum(1 for i in range(1,len(side_seq)) if side_seq[i]!=side_seq[i-1]);snap=next_snapshot(trace,end)
 return {'fillEvents':len(fs),'filledQty':sum(float(x['confirmedQty']) for x in fs),'fillAlternationsWithinWindow':alts,'repairPaymentQty':sum(float(x['qty']) for x in pays),'repairPaymentClocks':len({int(x['t']) for x in pays}),'expandBirthQty':sum(float(x['initialQty']) for x in birth),'expandBirths':len(birth),'roleFillEvents':dict(collections.Counter(str(x.get('role') or 'UNKNOWN') for x in fs)),'sideFillEvents':dict(collections.Counter(str(x['side']) for x in fs)),'floorAtOrAfterWindow':None if snap is None else float(snap['floor']),'invUPAtOrAfterWindow':None if snap is None else float(snap['invUP']),'invDOWNAtOrAfterWindow':None if snap is None else float(snap['invDOWN']),'freeSlotsAtOrAfterWindow':None if snap is None else int(snap['freeSlots'])}

def first_crosslot_event(sim):
 for x in sim.q_events:
  if x.get('event')!='QTY_FIFO_MANAGED_PASSIVE_SUBMIT':continue
  old=float(x.get('oldestRemaining') or 0);agg=float(x.get('aggregateRemaining') or 0);qty=float(x.get('qty') or 0)
  if old+EPS<qty<=agg+EPS:return x
 return None

def slim_pre(r):
 if r is None:return None
 return {k:r[k] for k in ['t','timeRemainingMs','portfolioState','held','weak','directionSignal','ordinaryRoleSide','ordinaryRole','invUP','invDOWN','cost','floor','best','freeSlots','liveSlots','debt','book','recent1s','recent3s','recent5s','newPlacements','newSlotEvents','newQEvents']}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='firstdiv_cont_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for mid in mids:
   tape=root/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper();A=TraceV3(tape)
   try:ra=A.run_qty('__UNSCORED__');atr=list(A.diag_receipts);awin={}
   finally:A.close()
   B=TraceV3B(tape)
   try:rb=B.run_qty('__UNSCORED__');btr=list(B.diag_receipts);cross=first_crosslot_event(B);tdiv,kind,apre,bpre=find_first_div(atr,btr)
   finally:B.close()
   if tdiv is not None:
    awin={str(w):window_outcome(A,atr,tdiv,w) for w in WINDOWS};bwin={str(w):window_outcome(B,btr,tdiv,w) for w in WINDOWS}
   else:awin={};bwin={}
   pnlA=float(ra['upQty' if winner=='UP' else 'downQty'])-float(ra['buyNotional']);pnlB=float(rb['upQty' if winner=='UP' else 'downQty'])-float(rb['buyNotional'])
   row={'marketId':mid,'winnerPostHocOnly':winner,'firstDivergenceT':tdiv,'firstDivergenceKind':kind,'firstCrossLotEvent':cross,'preV3':slim_pre(apre),'preV3B':slim_pre(bpre),'preStateParityAtDivergence':None if apre is None or bpre is None else apre['stateSignature']==bpre['stateSignature'],'windowsV3':awin,'windowsV3B':bwin,'terminal':{'V3':{'pnl':pnlA,'floor':float(ra['floor']),'fills':int(ra['fillEvents']),'alts':int(ra['fillSideAlternations']),'submits':int(ra['submits'])},'V3B':{'pnl':pnlB,'floor':float(rb['floor']),'fills':int(rb['fillEvents']),'alts':int(rb['fillSideAlternations']),'submits':int(rb['submits'])},'delta':{'pnl':pnlB-pnlA,'floor':float(rb['floor'])-float(ra['floor']),'fills':int(rb['fillEvents'])-int(ra['fillEvents']),'alts':int(rb['fillSideAlternations'])-int(ra['fillSideAlternations']),'submits':int(rb['submits'])-int(ra['submits'])}},'ledgerInvariantV3':ra['quantityLedgerSummary']['invariantViolations'],'ledgerInvariantV3B':rb['quantityLedgerSummary']['invariantViolations']};rows.append(row)
   print(json.dumps({'marketId':mid,'divT':tdiv,'kind':kind,'cross':cross,'preParity':row['preStateParityAtDivergence'],'delta':row['terminal']['delta']},ensure_ascii=False),flush=True)
 out={'version':'V3_V3B_FIRST_DIVERGENCE_CONTINUATION_ANATOMY_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'markets':mids,'rows':rows,'guards':['paired V3/V3B realistic-HFT','first action/pre-state divergence only','strict-past features before action','future windows evaluation-only','winner/PnL posthoc only','no threshold/model fitting','Repair-vs-Continue estimand; no HOLD candidate','activity density always reported','no NEW24-B/no 8781/no dream fill']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':len(rows),'divergences':sum(r['firstDivergenceT'] is not None for r in rows),'preParity':sum(r['preStateParityAtDivergence'] is True for r in rows)},ensure_ascii=False))
if __name__=='__main__':main()
