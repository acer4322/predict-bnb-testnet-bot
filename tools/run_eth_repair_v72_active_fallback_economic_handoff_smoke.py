from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
EPS=1e-9
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v70g=sib('eth_v70g_for_v72handoff','run_eth_repair_v70g_generation_scoped_relay_hft_smoke.py');v65=v70g.v65;v38=v70g.v38;v1=v70g.v70f.v70d.v1

class V72EconomicFallback(v70g.V70GGenerationScopedRelay):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v72Blocks=0;self.v72Allows=0;self.v72NoEvidenceInherited=0;self.v72Events=[];self.v72CreditReserved={};self.v72CreditReuse=0.0
 def _repair_credit_context(self,t,side,ask):
  ev=sorted(getattr(self,'v53Fills',[]) or [],key=lambda x:(int(x.get('t') or 0),str(x.get('key'))))
  ex=[x for x in ev if str(x.get('role')) in ('PASSIVE_EXPAND','ACTIVE_EXPAND') and str(x.get('side'))==side and int(x.get('t') or 0)<int(t)]
  if not ex:return None
  last=max(ex,key=lambda x:int(x.get('t') or 0));pay='UP' if side=='DOWN' else 'DOWN'
  lots=[]
  for r in ev:
   if str(r.get('role')) not in ('PASSIVE_REPAIR','ACTIVE_REPAIR') or str(r.get('side'))!=pay:continue
   rt=int(r.get('t') or 0)
   if not (int(last.get('t') or 0)<rt<int(t)):continue
   key=str(r.get('key'));q=float(r.get('qty') or 0.0);used=float(self.v72CreditReserved.get(key) or 0.0);rem=max(0.0,q-used)
   if rem>EPS:lots.append({'key':key,'qty':q,'remaining':rem,'price':float(r.get('price') or 0.0),'t':rt})
  total=sum(x['remaining'] for x in lots)
  if total<=EPS:return {'lastExpand':last,'lots':[],'qty':0.0,'weightedRepairPrice':None,'pairSum':None,'ask':float(ask)}
  rp=sum(x['remaining']*x['price'] for x in lots)/total
  return {'lastExpand':last,'lots':lots,'qty':total,'weightedRepairPrice':rp,'pairSum':rp+float(ask),'ask':float(ask)}
 def _reserve_credit(self,lots,qty):
  need=float(qty);used=[]
  for x in lots:
   if need<=EPS:break
   take=min(float(x['remaining']),need);self.v72CreditReserved[x['key']]=float(self.v72CreditReserved.get(x['key']) or 0.0)+take;used.append({'key':x['key'],'qty':take,'price':x['price']});need-=take
  if need>EPS:self.v72CreditReuse+=need
  return used
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  if int(self.capEnd)-int(t)<=180000:return super()._submit_active_expand(t,source_key,e,o,remaining)
  side=e.get('side') or o.get('side');qv=v1.quotes(self.book)
  if side not in ('UP','DOWN') or not qv or qv.get(side,{}).get('ask') is None:return super()._submit_active_expand(t,source_key,e,o,remaining)
  ask=float(qv[side]['ask']);ctx=self._repair_credit_context(t,side,ask)
  if ctx is None or ctx['weightedRepairPrice'] is None:
   self.v72NoEvidenceInherited+=1;self.v72Events.append({'t':int(t),'event':'NO_REPAIR_CREDIT_INHERIT_V70G','sourceKey':source_key,'side':side,'ask':ask});return super()._submit_active_expand(t,source_key,e,o,remaining)
  legal=1.0/ask if ask>EPS else math.inf;eligible=min(float(remaining),float(ctx['qty']))
  row={'t':int(t),'sourceKey':source_key,'side':side,'ask':ask,'weightedRepairPrice':ctx['weightedRepairPrice'],'pairSum':ctx['pairSum'],'repairCreditQty':ctx['qty'],'remainingAuthorizedQty':float(remaining),'venueMinQty':legal,'eligibleQtyBeforeVenueMin':eligible,'lastExpandT':int(ctx['lastExpand']['t']),'lastExpandKey':str(ctx['lastExpand']['key'])}
  if float(ctx['pairSum'])>1.0+EPS:
   self.v72Blocks+=1;row.update({'event':'ACTIVE_FALLBACK_ECONOMIC_BLOCK','reason':'PAIR_SUM_GT_1'});self.v72Events.append(row);return False
  if not math.isfinite(legal) or eligible+EPS<legal:
   self.v72Blocks+=1;row.update({'event':'ACTIVE_FALLBACK_ECONOMIC_BLOCK','reason':'CREDIT_BELOW_VENUE_MIN'});self.v72Events.append(row);return False
  qty_cap=min(float(remaining),float(ctx['qty']))
  before=set(self.v64Active)
  ok=super()._submit_active_expand(t,source_key,e,o,qty_cap)
  if ok:
   new=[k for k in self.v64Active if k not in before];submitted=float(self.v64Active[new[0]]['qty']) if new else min(legal,qty_cap);used=self._reserve_credit(ctx['lots'],submitted);self.v72Allows+=1;row.update({'event':'ACTIVE_FALLBACK_ECONOMIC_ALLOW','submittedQty':submitted,'creditReserved':used,'childKey':new[0] if new else None})
  else:row.update({'event':'ACTIVE_FALLBACK_INHERITED_SUBMIT_FAILED'})
  self.v72Events.append(row);return ok
 def run_exam_v72(self,models,winner):
  r=super().run_exam_v70g(models,winner);r.update({'v72EconomicBlocks':self.v72Blocks,'v72EconomicAllows':self.v72Allows,'v72NoEvidenceInherited':self.v72NoEvidenceInherited,'v72CreditReserved':self.v72CreditReserved,'v72CreditReuse':self.v72CreditReuse,'v72Events':self.v72Events[:100]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
 if mids!=[1827903]:raise ValueError(f'fixed prereg market must be [1827903], got {mids}')
 tmp=Path(tempfile.mkdtemp(prefix='eth_v72handoff_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in mids:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
   b=v70g.V70GGenerationScopedRelay(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v70g(models,cr['winner'])
   finally:b.close()
   c=V72EconomicFallback(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v72(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baselineV70G':br,'candidateV72':rr});print(json.dumps({'marketId':mid,'blocks':rr['v72EconomicBlocks'],'allows':rr['v72EconomicAllows'],'v64Fill':[br['v64FillQty'],rr['v64FillQty']],'v44Submits':[br['v44Submits'],rr['v44Submits']],'v44Fill':[br['v44ActualFillQty'],rr['v44ActualFillQty']],'rounds':[br['v64Rounds'],rr['v64Rounds']],'floor':[br['floor'],rr['floor']],'events':rr['v72Events']},ensure_ascii=False),flush=True)
  br=rows[0]['baselineV70G'];rr=rows[0]['candidateV72'];late_submits=sum(1 for x in rr.get('v64Events',[]) if x.get('event')=='ACTIVE_EXPAND_FALLBACK_SUBMIT' and int(rr.get('windowEndMs') or 0)-int(x.get('t') or 0)<=180000) if rr.get('windowEndMs') else 0
  gates={'oneMarketCompletes':len(rows)==1,'passiveEconomicAuthorizationPreserved':int(rr.get('v44Submits') or 0)==int(br.get('v44Submits') or 0) and float(rr.get('v44ActualFillQty') or 0)>=float(br.get('v44ActualFillQty') or 0)-EPS,'damagingActiveFallbackBlocked':int(rr.get('v72EconomicBlocks') or 0)>=1,'zeroDamagingActiveExpandFill':float(rr.get('v64FillQty') or 0)<=EPS,'zeroTruthMismatch':float(rr.get('authorizedSubmitWithTruthRoleMismatch') or 0)==0,'zeroOverOwned':float(rr.get('overOwnedSubmitViolations') or 0)==0,'zeroResponsibilityOverfill':float(rr.get('v51ResponsibilityOverfill') or 0)<=EPS,'zeroRepairDrift':float(rr.get('repairToExpandAtFirstFill') or 0)==0,'noCreditReuse':float(rr.get('v72CreditReuse') or 0)<=EPS,'cutoffPreserved':late_submits==0}
  decision='KEEP_V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_REPLICATE_3_MARKETS' if all(gates.values()) else 'REJECT_OR_FIX_V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF'
  out={'version':'ETH_REPAIR_V72_ACTIVE_FALLBACK_ECONOMIC_HANDOFF_SMOKE','date':'2026-09-03','researchOnly':True,'behaviorChange':True,'actionAuthority':'ONE_MARKET_FUNCTIONAL_SMOKE_ONLY','fixedMarket':1827903,'gates':gates,'decision':decision,'diagnostic':{'baselineRawRounds':br.get('v64Rounds'),'candidateRawRounds':rr.get('v64Rounds'),'baselineFloor':br.get('floor'),'candidateFloor':rr.get('floor'),'baselinePnlDiagnosticOnly':br.get('pnlDiagnosticOnly'),'candidatePnlDiagnosticOnly':rr.get('pnlDiagnosticOnly'),'rawRoundDecreaseAllowedByPreregister':True},'rows':rows,'boundary':['only V64 active fallback execution price revalidated','V44/V65 passive/model authority unchanged','pair sum <=1 structural not tuned','repair-credit quantity cap','raw token rounds not a KEEP gate','realistic HFT/Predict Tape only','no winner/PnL gate','no H100','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'diagnostic':out['diagnostic'],'v72Events':rr['v72Events']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
