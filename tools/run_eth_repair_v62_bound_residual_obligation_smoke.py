from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v53=sib('eth_v53_for_v62','run_eth_repair_v53_multicycle_audit.py');v38=v53.v38;v1=v53.v52.v51.v1;EPS=1e-9

class V62BoundResidual(v53.V53MultiCycleAudit):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v62Obs={};self.v62SeenExpand=set();self.v62FillSeen=0;self.v62OpSeen=0;self.v62ActiveKeys=set();self.v62ActiveFillSeen={};self.v62Created=0;self.v62Bound=0;self.v62ActiveSubmits=0;self.v62ActiveFillQty=0.;self.v62PassivePaidQty=0.;self.v62Satisfied=0;self.v62Blocks=0;self.v62Events=[]
 def _new_strict_expands(self):
  ev=self.v53Fills;out=[]
  for i,x in enumerate(ev):
   if x['role']!='PASSIVE_EXPAND' or x['key'] in self.v62SeenExpand:continue
   prior=[z for z in ev[:i] if z['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_EXPAND')]
   if prior and prior[-1]['role']=='PASSIVE_REPAIR':out.append(x)
  return out
 def _bind(self,t,ob):
  if ob['state']!='PENDING':return
  rp=self.repairParent
  if rp is None:return
  pid=int(rp['id']);side=rp.get('side');psv=self._find_passive(pid);pay=self._current_payoffs()
  if side not in ('UP','DOWN') or psv is None or pay['gap']<=EPS:return
  pk,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:return
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;target=min(legal,float(pay['gap']),float(rem))
  if target<=EPS or target+EPS<legal:return
  ob.update({'state':'BOUND','bindT':int(t),'parentId':pid,'side':side,'initialPassiveKey':pk,'targetQty':target,'paidQty':0.0,'lastPassiveKey':pk,'lastDisconnectT':None});self.v62Bound+=1;self.v62Events.append({'t':int(t),'event':'RESIDUAL_BOUND','expandKey':ob['expandKey'],'parentId':pid,'targetQty':target,'passiveKey':pk,'activeAskAtBind':ask})
 def _allocate_new_repairs(self):
  new=self.v53Fills[self.v62FillSeen:];self.v62FillSeen=len(self.v53Fills)
  for z in new:
   if z['role'] not in ('PASSIVE_REPAIR','ACTIVE_REPAIR'):continue
   q=float(z['qty']);pid=z.get('parentId')
   obs=sorted([o for o in self.v62Obs.values() if o['state']=='BOUND' and o.get('parentId')==pid and int(z['t'])>int(o['bindT'])],key=lambda o:o['expandT'])
   rem=q
   for ob in obs:
    need=max(0.,float(ob['targetQty'])-float(ob['paidQty']));x=min(need,rem)
    if x<=EPS:continue
    ob['paidQty']+=x;rem-=x
    if z['role']=='PASSIVE_REPAIR':self.v62PassivePaidQty+=x
    if z['key'] in self.v62ActiveKeys:self.v62ActiveFillQty+=x
    self.v62Events.append({'t':int(z['t']),'event':'RESIDUAL_PAYMENT','expandKey':ob['expandKey'],'role':z['role'],'key':z['key'],'paidQty':x,'cumPaid':ob['paidQty'],'targetQty':ob['targetQty']})
    if ob['paidQty']+EPS>=ob['targetQty']:
     ob['state']='SATISFIED';self.v62Satisfied+=1;self.v62Events.append({'t':int(z['t']),'event':'RESIDUAL_SATISFIED','expandKey':ob['expandKey'],'byRole':z['role']})
    if rem<=EPS:break
 def _try_route_on_disconnect(self,t,opp):
  pid=int(opp.get('parentId'));c=[o for o in self.v62Obs.values() if o['state']=='BOUND' and o.get('parentId')==pid and int(opp.get('t',-1))>int(o['bindT'])]
  if not c:return
  ob=sorted(c,key=lambda o:o['expandT'])[0]
  if bool(opp.get('connected')):return
  if ob.get('lastDisconnectT')==int(opp['t']):return
  ob['lastDisconnectT']=int(opp['t']);rp=self.repairParent
  if rp is None or int(rp.get('id'))!=pid:return
  side=rp.get('side');psv=self._find_passive(pid);pay=self._current_payoffs();need=max(0.,float(ob['targetQty'])-float(ob['paidQty']))
  if side not in ('UP','DOWN') or psv is None or need<=EPS:return
  pk,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:return
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(legal,need,float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:return
  ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost);u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay['gap'])-q)
  if not (floor2>=-EPS or (math.isfinite(passive_legal) and gap2+EPS>=passive_legal)):
   self.v62Blocks+=1;self.v62Events.append({'t':int(t),'event':'V50_STRANDING_BLOCK','expandKey':ob['expandKey'],'parentId':pid,'gap2':gap2,'passiveLegal':passive_legal});return
  # Never overlap another live active child for the same parent.
  a=getattr(self,'activeByParent',{}).get(pid)
  if a:
   ao=self.orders.get(a.get('key'))
   try:
    if ao and v1.live(self.snap(ao).get('status')):return
   except Exception:pass
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  if self._submit_active(t,pid,pk,side,ask,q,oid):
   ak=self.activeByParent[pid]['key'];self.v62ActiveKeys.add(ak);self.v62ActiveSubmits+=1;self.v62Events.append({'t':int(t),'event':'RESIDUAL_ACTIVE_SUBMIT','expandKey':ob['expandKey'],'parentId':pid,'activeKey':ak,'passiveKey':pk,'qty':q,'ask':ask,'projectedFloor':floor2,'projectedGap':gap2,'opportunityT':int(opp['t'])})
 def process(self,t):
  super().process(t);self._refresh_carrier_ledger(t)
  for x in self._new_strict_expands():
   self.v62SeenExpand.add(x['key']);self.v62Obs[x['key']]={'expandKey':x['key'],'expandT':int(x['t']),'state':'PENDING'};self.v62Created+=1;self.v62Events.append({'t':int(t),'event':'RESIDUAL_BIRTH','expandKey':x['key']})
  for ob in self.v62Obs.values():self._bind(t,ob)
  self._allocate_new_repairs()
  newops=self.postArmCarrierOpportunities[self.v62OpSeen:];self.v62OpSeen=len(self.postArmCarrierOpportunities)
  for op in newops:self._try_route_on_disconnect(t,op)
 def run_exam_v62(self,models,winner):
  r=super().run_exam_v53(models,winner);self._refresh_carrier_ledger(int(self.capEnd));self._allocate_new_repairs();ev=sorted(self.v53Fills,key=lambda x:(x['t'],x['key']));cs=self._cycle_stats(ev);counts={}
  for x in ev:counts[x['role']]=counts.get(x['role'],0)+1
  r.update({'v62Created':self.v62Created,'v62Bound':self.v62Bound,'v62ActiveSubmits':self.v62ActiveSubmits,'v62ActiveFillQty':self.v62ActiveFillQty,'v62PassivePaidQty':self.v62PassivePaidQty,'v62Satisfied':self.v62Satisfied,'v62Unresolved':sum(o['state']!='SATISFIED' for o in self.v62Obs.values()),'v62States':{k:{z:o.get(z) for z in ['state','parentId','targetQty','paidQty','bindT']} for k,o in self.v62Obs.items()},'v62Events':self.v62Events[:200],'v62RoleCounts':counts,'v62RepairExpandRepairRounds':cs['repairExpandRepairRounds'],'v62StrictRounds':cs['passiveRepairExpandActiveRepairRounds'],'v62CompressedSequence':cs['compressedSequence'][:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v62_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V62','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V62_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v53.V53MultiCycleAudit(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v53(models,cr['winner'])
   finally:b.close()
   c=V62BoundResidual(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v62(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'created':rr['v62Created'],'bound':rr['v62Bound'],'activeSubmits':rr['v62ActiveSubmits'],'activeFill':rr['v62ActiveFillQty'],'satisfied':rr['v62Satisfied'],'unresolved':rr['v62Unresolved'],'rounds':[br['repairExpandRepairRounds'],rr['v62RepairExpandRepairRounds']],'strict':[br['passiveRepairExpandActiveRepairRounds'],rr['v62StrictRounds']]},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0) for x in rows)
  agg={'markets':len(rows),'created':int(sm('candidate','v62Created')),'bound':int(sm('candidate','v62Bound')),'activeSubmits':int(sm('candidate','v62ActiveSubmits')),'activeFillQty':sm('candidate','v62ActiveFillQty'),'passivePaidQty':sm('candidate','v62PassivePaidQty'),'satisfied':int(sm('candidate','v62Satisfied')),'unresolved':int(sm('candidate','v62Unresolved')),'baselineRounds':int(sm('baseline','repairExpandRepairRounds')),'candidateRounds':int(sm('candidate','v62RepairExpandRepairRounds')),'baselineStrict':int(sm('baseline','passiveRepairExpandActiveRepairRounds')),'candidateStrict':int(sm('candidate','v62StrictRounds')),'marketsStrictImproved':sum(x['candidate']['v62StrictRounds']>x['baseline']['passiveRepairExpandActiveRepairRounds'] for x in rows),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill')}
  gates={'obligationExercised':agg['created']>0 and agg['bound']>0,'v62ActiveActuallyFilled':agg['activeFillQty']>EPS,'strictRoundImproved':agg['marketsStrictImproved']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V62_BOUND_RESIDUAL_OBLIGATION_SMOKE','researchOnly':True,'behaviorChange':True,'actionAuthority':'FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['residual obligation quantum = venue-min legal active slice at Repair-carrier bind','passive/active payments share same obligation oldest-first','active route only at fresh disconnected post-bind lifecycle opportunity','V50 recoverability + V51/V52 shared budget inherited','small-scale only','no PnL/winner trigger','no threshold/qty/delay tuning','no H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
