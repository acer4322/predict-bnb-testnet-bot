from __future__ import annotations
import argparse,json,heapq,math,os,random,statistics
from collections import Counter,defaultdict
from pathlib import Path

SCENARIOS=['CLEAN_FULL','FILL_OBS_LAG','ACK_LAG','REPAIR_PARTIAL','NO_FILL_CANCEL_REPRICE','REJECT_RETRY','CANCEL_LATE_FILL','COMPOUND_DELAY_PARTIAL','DOMINANT_LATE_FILL_GAP_GROWS','PRICE_QUEUE_DRIFT']
EPS=1e-9

class MicroWorld:
 def __init__(self,seed:int,scenario:str,mode:str='CURRENT_REPAIR'):
  self.r=random.Random(seed);self.scenario=scenario;self.mode=mode;self.q=[];self.seq=0;self.t=0
  self.seed_side='UP' if self.r.random()<.5 else 'DOWN';self.repair_side='DOWN' if self.seed_side=='UP' else 'UP';self.seed_qty=self.r.uniform(3.0,12.0);self.inv={'UP':0.0,'DOWN':0.0};self.visible_inv={'UP':0.0,'DOWN':0.0};self.hidden_obs_qty=0.0;self.obs_lag_events=0;self.ack_lag_orders=0
  self.submits=[];self.fills=[];self.parent=False;self.parent_id=0;self.parent_births=0;self.parent_completions=0;self.parent_lost_while_gap=0
  self.carrier=None;self.repair_attempts=0;self.repair_accepts=0;self.repair_rejects=0;self.cancel_requests=0;self.reprices=0;self.partial_events=0;self.late_fill_events=0;self.dominant_late_fills=0
  self.over_owned=0;self.duplicate_lane=0;self.max_gap=0.;self.initial_gap=None;self.first_seed_fill=None;self.first_repair_submit=None;self.first_parent_birth=None;self.partial_parent_continuity=True;self.late_fill_absorbed=True;self.was_cancelled_attempts=0
  self._attempt_no=0;self._dominant_late_scheduled=False
  self.schedule(0,'SEED_SUBMIT',{})
  if mode=='CHEAT_SIMULTANEOUS_PAIR':self.schedule(0,'CHEAT_OPPOSITE_SUBMIT',{})
 def schedule(self,t,k,p):self.seq+=1;heapq.heappush(self.q,(int(t),self.seq,k,p))
 def gap(self):return abs(self.inv['UP']-self.inv['DOWN'])
 def weak(self):return 'UP' if self.inv['UP']<self.inv['DOWN']-EPS else 'DOWN' if self.inv['DOWN']<self.inv['UP']-EPS else None
 def submit(self,side,role,qty,accepted=True):
  self.submits.append({'t':self.t,'side':side,'role':role,'qty':float(qty),'accepted':bool(accepted)})
  if role=='REPAIR' and accepted and self.first_repair_submit is None:self.first_repair_submit=self.t
 def material_fill(self,side,qty,role,late=False):
  qty=max(0.,float(qty));pre=self.gap();self.inv[side]+=qty;self.fills.append({'t':self.t,'side':side,'role':role,'qty':qty,'late':late});post=self.gap();self.max_gap=max(self.max_gap,post)
  if self.scenario in {'FILL_OBS_LAG','COMPOUND_DELAY_PARTIAL'}:
   lag=self.r.randint(1000,3000);self.hidden_obs_qty+=qty;self.obs_lag_events+=1;self.schedule(self.t+lag,'OBS_APPLY',{'side':side,'qty':qty})
  else:self.visible_inv[side]+=qty
  if self.first_seed_fill is None and role=='SEED':self.first_seed_fill=self.t;self.initial_gap=post
  if role=='REPAIR' and self.carrier:
   self.carrier['filled']=min(self.carrier['qty'],self.carrier['filled']+qty)
   if late:self.late_fill_events+=1
  if role=='SEED' and self.first_seed_fill is not None and self.parent and self.t>self.first_seed_fill:self.dominant_late_fills+=1
  if self.mode=='CURRENT_REPAIR' and post>EPS and not self.parent:
   self.parent=True;self.parent_id+=1;self.parent_births+=1
   if self.first_parent_birth is None:self.first_parent_birth=self.t
   self.schedule(self.t+10000,'REPAIR_DUE',{'parent_id':self.parent_id})
  if role=='REPAIR' and self.partial_events>0 and self.parent is False and post>EPS:self.partial_parent_continuity=False
  self.maybe_complete_parent()
  return pre,post
 def maybe_complete_parent(self):
  if self.parent and self.gap()<=1e-7 and self.carrier is None:
   self.parent=False;self.parent_completions+=1
 def schedule_repair_fill_plan(self):
  c=self.carrier;attempt=self._attempt_no;qty=c['qty']
  if self.scenario in {'CLEAN_FULL','FILL_OBS_LAG','ACK_LAG'}:
   self.schedule(self.t+self.r.randint(500,1800),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='REPAIR_PARTIAL':
   f=qty*self.r.uniform(.2,.75);self.schedule(self.t+self.r.randint(500,1400),'REPAIR_FILL',{'qty':f,'terminal':False});self.schedule(self.t+self.r.randint(2200,4200),'REPAIR_FILL',{'qty':qty-f,'terminal':True})
  elif self.scenario=='NO_FILL_CANCEL_REPRICE':
   if attempt<=2:self.schedule(self.t+5000,'TTL_CANCEL',{})
   else:self.schedule(self.t+self.r.randint(600,1600),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='REJECT_RETRY':
   self.schedule(self.t+self.r.randint(600,1600),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='CANCEL_LATE_FILL':
   if attempt==1:self.schedule(self.t+5000,'TTL_CANCEL',{})
   else:self.schedule(self.t+self.r.randint(700,1800),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='COMPOUND_DELAY_PARTIAL':
   if attempt==1:
    f=qty*self.r.uniform(.15,.55);self.schedule(self.t+1800+self.r.randint(0,1200),'REPAIR_FILL',{'qty':f,'terminal':False});self.schedule(self.t+5000,'TTL_CANCEL',{})
   else:self.schedule(self.t+self.r.randint(1000,2400),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='DOMINANT_LATE_FILL_GAP_GROWS':
   if attempt==1:
    f=qty*self.r.uniform(.25,.65);self.schedule(self.t+900,'REPAIR_FILL',{'qty':f,'terminal':True})
    if not self._dominant_late_scheduled:
     self._dominant_late_scheduled=True;self.schedule(self.t+1700,'DOMINANT_LATE_FILL',{'qty':self.r.uniform(1.0,4.5)})
   else:self.schedule(self.t+self.r.randint(700,1800),'REPAIR_FILL',{'qty':qty,'terminal':True})
  elif self.scenario=='PRICE_QUEUE_DRIFT':
   if attempt<=2:self.schedule(self.t+5000,'TTL_CANCEL',{})
   else:self.schedule(self.t+self.r.randint(1200,3000),'REPAIR_FILL',{'qty':qty,'terminal':True})
 def on_repair_due(self,p):
  if self.mode!='CURRENT_REPAIR' or not self.parent or p.get('parent_id')!=self.parent_id or self.gap()<=EPS:return
  if self.carrier is not None:
   self.duplicate_lane+=1;return
  self.repair_attempts+=1;self._attempt_no+=1
  if self.scenario=='REJECT_RETRY' and self._attempt_no<=self.r.randint(1,3):
   self.repair_rejects+=1;self.submit(self.weak() or self.repair_side,'REPAIR',min(self.gap(),12.),False);self.schedule(self.t+500,'REPAIR_DUE',{'parent_id':self.parent_id});return
  side=self.weak();qty=min(self.gap(),12.)
  if side is None or qty<=EPS:return
  owned=0. if self.carrier is None else max(0.,self.carrier['qty']-self.carrier['filled'])
  if owned+qty>self.gap()+1e-7:self.over_owned+=1
  self.carrier={'side':side,'qty':qty,'filled':0.,'submitted':self.t,'cancelRequested':False,'terminal':False,'ackVisible':self.scenario not in {'ACK_LAG','COMPOUND_DELAY_PARTIAL'}};self.repair_accepts+=1;self.submit(side,'REPAIR',qty,True)
  if not self.carrier['ackVisible']:
   self.ack_lag_orders+=1;self.schedule(self.t+3000,'ACK_VISIBLE',{})
  self.schedule_repair_fill_plan()
 def terminal_carrier(self):
  if self.carrier is None:return
  rem=max(0.,self.carrier['qty']-self.carrier['filled']);self.carrier=None;self.maybe_complete_parent()
  if self.parent and self.gap()>EPS:
   self.reprices+=1;self.schedule(self.t+250,'REPAIR_DUE',{'parent_id':self.parent_id})
 def run(self,horizon=120000):
  while self.q:
   t,_,k,p=heapq.heappop(self.q)
   if t>horizon:break
   self.t=t
   if self.parent is False and self.first_seed_fill is not None and self.gap()>EPS and self.mode=='CURRENT_REPAIR':self.parent_lost_while_gap+=1
   if k=='SEED_SUBMIT':
    self.submit(self.seed_side,'SEED',self.seed_qty,True);self.schedule(self.r.randint(1000,4500),'SEED_FILL',{'qty':self.seed_qty})
   elif k=='CHEAT_OPPOSITE_SUBMIT':
    self.submit(self.repair_side,'CHEAT_PAIR',self.seed_qty,True);self.schedule(self.r.randint(1000,4500),'CHEAT_FILL',{'qty':self.seed_qty})
   elif k=='SEED_FILL':self.material_fill(self.seed_side,p['qty'],'SEED')
   elif k=='CHEAT_FILL':self.material_fill(self.repair_side,p['qty'],'CHEAT_PAIR')
   elif k=='REPAIR_DUE':self.on_repair_due(p)
   elif k=='REPAIR_FILL':
    if self.carrier is None:continue
    rem=max(0.,self.carrier['qty']-self.carrier['filled']);q=min(rem,float(p['qty']));
    if q<rem-EPS:self.partial_events+=1
    self.material_fill(self.carrier['side'],q,'REPAIR',late=bool(self.carrier.get('cancelRequested')))
    if p.get('terminal') or self.carrier is not None and self.carrier['filled']>=self.carrier['qty']-EPS:self.terminal_carrier()
    elif self.parent is False and self.gap()>EPS:self.partial_parent_continuity=False
   elif k=='TTL_CANCEL':
    if self.carrier is None:continue
    self.cancel_requests+=1;self.was_cancelled_attempts+=1;self.carrier['cancelRequested']=True
    if self.scenario in {'CANCEL_LATE_FILL','COMPOUND_DELAY_PARTIAL'}:
     rem=max(0.,self.carrier['qty']-self.carrier['filled'])
     if rem>EPS:self.schedule(self.t+self.r.randint(250,900),'LATE_FILL',{'qty':rem*self.r.uniform(.15,.65)})
     self.schedule(self.t+self.r.randint(1000,1800),'CANCEL_TERMINAL',{})
    else:self.schedule(self.t+self.r.randint(300,1200),'CANCEL_TERMINAL',{})
   elif k=='LATE_FILL':
    if self.carrier is None:continue
    rem=max(0.,self.carrier['qty']-self.carrier['filled']);q=min(rem,float(p['qty']));self.material_fill(self.carrier['side'],q,'REPAIR',late=True)
   elif k=='CANCEL_TERMINAL':self.terminal_carrier()
   elif k=='DOMINANT_LATE_FILL':
    self.material_fill(self.seed_side,p['qty'],'SEED',late=True)
   elif k=='OBS_APPLY':
    self.visible_inv[p['side']]+=float(p['qty']);self.hidden_obs_qty=max(0.0,self.hidden_obs_qty-float(p['qty']))
   elif k=='ACK_VISIBLE':
    if self.carrier is not None:self.carrier['ackVisible']=True
  if self.parent and self.gap()<=1e-7 and self.carrier is None:self.maybe_complete_parent()
  return self.result()
 def result(self):
  ff=self.first_seed_fill;rs=self.first_repair_submit;seed=self.seed_side
  opp_before=sum(1 for x in self.submits if ff is not None and x['t']<ff and x['side']!=seed)
  opp_at_or_before=sum(1 for x in self.submits if ff is not None and x['t']<=ff and x['side']!=seed)
  lag=None if ff is None or rs is None else rs-ff;initial=float(self.initial_gap or 0.);final=self.gap();reduction=(initial-final)/initial if initial>EPS else 0.
  return {'scenario':self.scenario,'mode':self.mode,'seedSide':seed,'seedQty':self.seed_qty,'firstSeedActualFillMs':ff,'firstParentBirthMs':self.first_parent_birth,'firstRepairSubmitMs':rs,'repairLagMs':lag,'oppositeSubmitBeforeFirstActualFill':opp_before,'oppositeSubmitAtOrBeforeFirstActualFill':opp_at_or_before,'sameTimestampDouble':bool(lag==0 if lag is not None else False),'sameSecondDouble':bool(lag is not None and 0<=lag<1000),'repairParentBirths':self.parent_births,'repairParentCompletions':self.parent_completions,'repairAttempts':self.repair_attempts,'repairAcceptedSubmits':self.repair_accepts,'repairRejectedAttempts':self.repair_rejects,'cancelRequests':self.cancel_requests,'reprices':self.reprices,'partialFillEvents':self.partial_events,'lateFillEvents':self.late_fill_events,'dominantLateFills':self.dominant_late_fills,'observationLagEvents':self.obs_lag_events,'ackLagOrders':self.ack_lag_orders,'hiddenObservationQtyAtEnd':self.hidden_obs_qty,'overOwnedViolations':self.over_owned,'duplicateLaneViolations':self.duplicate_lane,'parentLostWhileGap':self.parent_lost_while_gap,'partialParentContinuity':self.partial_parent_continuity,'lateFillAbsorbed':self.late_fill_absorbed,'initialGap':initial,'maxGap':self.max_gap,'finalGap':final,'gapReductionFrac':reduction,'repairActionOccurred':self.repair_accepts>0,'completed':final<=1e-7,'submits':self.submits[:20],'fills':self.fills[:20]}

def summarize(rows):
 n=len(rows);by=defaultdict(list)
 for r in rows:by[r['scenario']].append(r)
 def sm(k):return sum(int(bool(r.get(k))) for r in rows)
 out={'episodes':n,'oppositeBeforeFillViolations':sum(r['oppositeSubmitBeforeFirstActualFill'] for r in rows),'oppositeAtOrBeforeFillViolations':sum(r['oppositeSubmitAtOrBeforeFirstActualFill'] for r in rows),'sameTimestampDoubleViolations':sm('sameTimestampDouble'),'sameSecondDoubleViolations':sm('sameSecondDouble'),'repairActionEpisodes':sm('repairActionOccurred'),'repairActionRate':sm('repairActionOccurred')/n if n else None,'completionEpisodes':sm('completed'),'completionRate':sm('completed')/n if n else None,'parentBirths':sum(r['repairParentBirths'] for r in rows),'parentCompletions':sum(r['repairParentCompletions'] for r in rows),'overOwnedViolations':sum(r['overOwnedViolations'] for r in rows),'duplicateLaneViolations':sum(r['duplicateLaneViolations'] for r in rows),'parentLostWhileGap':sum(r['parentLostWhileGap'] for r in rows),'partialContinuityFailures':sum(not r['partialParentContinuity'] for r in rows),'meanGapReductionFrac':statistics.mean(r['gapReductionFrac'] for r in rows) if rows else None,'medianRepairLagMs':statistics.median([r['repairLagMs'] for r in rows if r['repairLagMs'] is not None]) if any(r['repairLagMs'] is not None for r in rows) else None,'scenario':{}}
 for k,z in by.items():out['scenario'][k]={'episodes':len(z),'repairActionRate':sum(r['repairActionOccurred'] for r in z)/len(z),'completionRate':sum(r['completed'] for r in z)/len(z),'meanGapReductionFrac':statistics.mean(r['gapReductionFrac'] for r in z),'partialEvents':sum(r['partialFillEvents'] for r in z),'cancelRequests':sum(r['cancelRequests'] for r in z),'reprices':sum(r['reprices'] for r in z),'rejectedAttempts':sum(r['repairRejectedAttempts'] for r in z),'lateFillEvents':sum(r['lateFillEvents'] for r in z),'dominantLateFills':sum(r['dominantLateFills'] for r in z),'observationLagEvents':sum(r['observationLagEvents'] for r in z),'ackLagOrders':sum(r['ackLagOrders'] for r in z)}
 return out

def gates_current(s):
 return {'zeroOppositeBeforeFirstFill':s['oppositeBeforeFillViolations']==0,'zeroOppositeAtOrBeforeFirstFill':s['oppositeAtOrBeforeFillViolations']==0,'zeroSameTimestampDouble':s['sameTimestampDoubleViolations']==0,'zeroSameSecondDoubleForWait10':s['sameSecondDoubleViolations']==0,'repairActionRateGe099':s['repairActionRate']>=.99,'completionRateGe097':s['completionRate']>=.97,'gapReductionMeanGe095':s['meanGapReductionFrac']>=.95,'zeroOverOwned':s['overOwnedViolations']==0,'zeroDuplicateLane':s['duplicateLaneViolations']==0,'zeroParentLostWhileGap':s['parentLostWhileGap']==0,'partialContinuityNoFailure':s['partialContinuityFailures']==0,'partialScenarioExercised':s['scenario'].get('REPAIR_PARTIAL',{}).get('partialEvents',0)>0 and s['scenario'].get('COMPOUND_DELAY_PARTIAL',{}).get('partialEvents',0)>0,'cancelRepriceExercised':s['scenario'].get('NO_FILL_CANCEL_REPRICE',{}).get('cancelRequests',0)>0 and s['scenario'].get('PRICE_QUEUE_DRIFT',{}).get('reprices',0)>0,'rejectRetryExercised':s['scenario'].get('REJECT_RETRY',{}).get('rejectedAttempts',0)>0,'lateFillExercised':s['scenario'].get('CANCEL_LATE_FILL',{}).get('lateFillEvents',0)>0,'gapGrowthExercised':s['scenario'].get('DOMINANT_LATE_FILL_GAP_GROWS',{}).get('dominantLateFills',0)>0,'fillObservationLagExercised':s['scenario'].get('FILL_OBS_LAG',{}).get('observationLagEvents',0)>0 and s['scenario'].get('COMPOUND_DELAY_PARTIAL',{}).get('observationLagEvents',0)>0,'ackLagExercised':s['scenario'].get('ACK_LAG',{}).get('ackLagOrders',0)>0 and s['scenario'].get('COMPOUND_DELAY_PARTIAL',{}).get('ackLagOrders',0)>0}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--episodes',type=int,default=100);ap.add_argument('--seed',type=int,default=20260901);ap.add_argument('--include-rows',type=int,default=20);ap.add_argument('--output');a=ap.parse_args()
 rows=[]
 for i in range(a.episodes):rows.append(MicroWorld(a.seed+i,SCENARIOS[i%len(SCENARIOS)],'CURRENT_REPAIR').run())
 cheat=[MicroWorld(a.seed+100000+i,SCENARIOS[i%len(SCENARIOS)],'CHEAT_SIMULTANEOUS_PAIR').run() for i in range(min(a.episodes,1000))]
 norep=[MicroWorld(a.seed+200000+i,SCENARIOS[i%len(SCENARIOS)],'NO_REPAIR').run() for i in range(min(a.episodes,1000))]
 s=summarize(rows);cs=summarize(cheat);ns=summarize(norep);g=gates_current(s);neg={'cheatDetected':cs['oppositeBeforeFillViolations']>0 and cs['oppositeAtOrBeforeFillViolations']>0,'noRepairDetected':ns['repairActionRate']==0 and ns['meanGapReductionFrac']<=.05};allpass=all(g.values()) and all(neg.values())
 out={'version':'ETH_REPAIR_ANTI_CHEAT_MICRO_WORLD_V1','researchOnly':True,'performanceGraduationEligible':False,'config':{'episodes':a.episodes,'seed':a.seed,'scenarios':SCENARIOS,'anchorWaitMs':10000,'horizonMs':120000},'currentRepair':s,'hardGates':g,'negativeControls':{'gates':neg,'cheatSimultaneousPair':cs,'noRepair':ns},'allPass':allpass,'sampleRows':rows[:a.include_rows],'boundary':['Synthetic micro-world execution events are functional fault-injection only, not dream-fill performance evidence','No winner/PnL used','Current Repair contract is one-sided seed actual-fill -> persistent parent -> anchored WAIT10 -> Repair carrier lifecycle','Negative controls demonstrate anti-cheat sensitivity','Realistic-HftBacktest causal trace is separate required evidence']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'allPass':allpass,'episodes':a.episodes,'current':s,'gates':g,'negativeControls':neg},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
