from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math,statistics
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v53=sib('eth_v53_for_v56','run_eth_repair_v53_multicycle_audit.py')
hzmod=sib('eth_target_hazard_for_v56','train_target_eth_repair_taker_escalation_hazard_v1.py')
core=hzmod.core;FEATURES=list(hzmod.FEATURES);EPS=1e-9
v38=v53.v38;v1=v53.v52.v51.v1

def qstats(xs):
 a=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
 if not a:return {'n':0}
 def q(p):
  z=(len(a)-1)*p;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo;return a[lo]*(1-w)+a[hi]*w
 return {'n':len(a),'mean':statistics.mean(a),'p10':q(.1),'p25':q(.25),'median':q(.5),'p75':q(.75),'p90':q(.9),'max':a[-1]}

class V56HazardParallelShadow(v53.V53MultiCycleAudit):
 def __init__(self,*a,hazard_art=None,**kw):
  super().__init__(*a,**kw)
  self.v56Art=hazard_art;self.v56Model=hazard_art['model'];assert list(hazard_art['features'])==FEATURES
  self.v56Inv=core.Inventory();self.v56Synced=0;self.v56MakerParents={};self.v56ScoredBuckets=set();self.v56Rows=[]
  self.v56EligibleStates=0;self.v56HighStates=0;self.v56FeasibleStates=0;self.v56HighFeasibleStates=0
  self.v56Epochs=set();self.v56HighEpochs=set();self.v56HighFeasibleEpochs=set();self.v56Scores=[];self.v56HighScores=[];self.v56FeasibleScores=[]
 def _maker_abs(self):return abs(float(self.v56Inv.maker_up)-float(self.v56Inv.maker_down))
 def _sync_hazard_fills(self):
  while self.v56Synced<len(self.v53Fills):
   x=self.v53Fills[self.v56Synced];self.v56Synced+=1
   role='TAKER' if str(x['role']).startswith('ACTIVE_') else 'MAKER';side=str(x.get('side') or '');q=float(x.get('qty') or 0.0);p=float(x.get('price') or 0.0);t=int(x['t'])
   if side not in ('UP','DOWN') or q<=EPS:continue
   if role=='MAKER':
    pre=self._maker_abs();key=str(x.get('key'));z=self.v56MakerParents.get(key)
    if z is None:z={'key':key,'firstEventMs':t,'lastEventMs':t,'shares':0.0,'preMakerAbsNet':pre,'postMakerAbsNet':pre,'deltaMakerAbsNet':0.0};self.v56MakerParents[key]=z
   self.v56Inv.apply({'event_ms':t,'role':role,'side':side,'price':p,'shares':q})
   if role=='MAKER':
    z=self.v56MakerParents[str(x.get('key'))];z['lastEventMs']=t;z['shares']+=q;z['postMakerAbsNet']=self._maker_abs();z['deltaMakerAbsNet']=z['postMakerAbsNet']-z['preMakerAbsNet']
 def _feature_vector(self,t):
  f=self.v56Inv.features(int(t));cn=float(f.pop('_combined_net'));dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None;bf=core.outcome_book(self.book,dom)
  if bf is None:return None,None
  m3=[e for e in self.v56Inv.events if e['role']=='MAKER' and int(t)-3000<int(e['event_ms'])<=int(t)]
  extra={'maker_fills_3s':float(len(m3)),'maker_shares_3s':float(sum(float(e['shares']) for e in m3))}
  pp=list(self.v56MakerParents.values())
  for w in (1000,3000,5000,10000):
   rp=[p for p in pp if float(p['deltaMakerAbsNet'])<-EPS and int(p['lastEventMs'])<=int(t) and int(p['lastEventMs'])>int(t)-w]
   extra[f'maker_repair_parents_{w//1000}s']=float(len(rp));extra[f'maker_repair_shares_{w//1000}s']=float(sum(float(p['shares']) for p in rp))
  ex=[p for p in pp if float(p['deltaMakerAbsNet'])>EPS and int(p['firstEventMs'])<=int(t)]
  ex=max(ex,key=lambda p:int(p['firstEventMs'])) if ex else None
  if ex is None:extra['latest_maker_expansion_age_ms']=math.nan;extra['latest_maker_expansion_repaid_frac']=math.nan
  else:
   extra['latest_maker_expansion_age_ms']=float(int(t)-int(ex['firstEventMs']));start=max(float(ex['postMakerAbsNet'])-float(ex['preMakerAbsNet']),EPS);cur=float(f['maker_abs_net']);extra['latest_maker_expansion_repaid_frac']=float((float(ex['postMakerAbsNet'])-cur)/start)
  vals={'seconds_left':(int(self.capEnd)-int(t))/1000.0,**f,**bf,**extra};x=np.asarray([np.nan if vals.get(k) is None else float(vals.get(k)) for k in FEATURES],np.float32)
  return x,vals
 def _parallel_feasible(self,t,gen_rem,rp):
  pid=int(rp.get('id'));side=rp.get('side');psv=self._find_passive(pid)
  if psv is None or side not in ('UP','DOWN'):return {'feasible':False,'reason':'NO_LIVE_PASSIVE'}
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:return {'feasible':False,'reason':'NO_ACTIVE_ASK'}
  ask=float(qv[side]['ask']);active_legal=1.0/ask if ask>EPS else math.inf;pay=self._current_payoffs();gen_rem=max(0.,float(gen_rem));q=min(active_legal,float(pay.get('gap') or 0.),float(rem),gen_rem)
  base={'passiveKey':passive_key,'passiveRemaining':float(rem),'activeAsk':ask,'activeLegalMin':active_legal,'generationRemaining':gen_rem,'payoffGap':float(pay.get('gap') or 0.),'qty':q}
  if q<=EPS or not math.isfinite(active_legal) or q+EPS<active_legal:return {**base,'feasible':False,'reason':'NO_LEGAL_ACTIVE_SLICE'}
  ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost);u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay.get('gap') or 0.)-q)
  allowed=floor2>=-EPS or (math.isfinite(passive_legal) and gap2+EPS>=passive_legal)
  return {**base,'passivePrice':ppx,'passiveLegalMin':passive_legal,'projectedFloor':floor2,'projectedRepairGap':gap2,'feasible':bool(allowed),'reason':'PROJECTED_FLOOR_RECOVERED' if floor2>=-EPS else ('POST_ACTIVE_PASSIVE_HANDOFF_LEGAL' if allowed else 'POST_ACTIVE_PASSIVE_REMAINDER_SUBLEGAL')}
 def _shadow_score(self,t):
  rp=self.repairParent
  if rp is None:return
  pay=self._current_payoffs();pay_gap=max(0.,float(pay.get('gap') or 0.))
  g=self._latest_gen();grem=max(0.,float(g.get('debt') or 0.)-float(g.get('paid') or 0.)) if g is not None else 0.0
  use_gen=g is not None and grem>EPS
  rem=grem if use_gen else pay_gap
  if rem<=EPS or int(self.capEnd)-int(t)<=180000:return
  bucket=int(t)//1000
  if bucket in self.v56ScoredBuckets:return
  self.v56ScoredBuckets.add(bucket);x,vals=self._feature_vector(t)
  if x is None:return
  pid=int(rp.get('id'));gid=int(g['id']) if use_gen else -pid;ctx=self.v52EpochCtx.get(int(g['id'])) if use_gen else None;ep=int(ctx['epoch']) if ctx is not None else 0;ek=(gid,ep);hard=bool((use_gen and (ek in self.v52EpochHardConfirmed or int(g['id']) in self.v49GenHardConfirmed)) or pid in getattr(self,'_armedParents',set()) and any(int(e.get('parentId',-1))==pid and e.get('event') in ('V36_HARD_EVENT_CONFIRMED','GENERATION_EPOCH_HARD_EVENT_CONFIRMED') for e in getattr(self,'activeEvents',[])+getattr(self,'v52Events',[])));feas=self._parallel_feasible(t,rem,rp);score=float(self.v56Model.predict_proba(x.reshape(1,-1))[0,1]);high=score>=0.5
  born=int(g.get('bornAt') or 0) if use_gen else int(rp.get('bornAt') or rp.get('createdAt') or 0);genfills=[z for z in self.v53Fills if int(z['t'])>=born] if born else list(self.v53Fills);passive_paid=sum(float(z.get('qty') or 0.) for z in genfills if z['role']=='PASSIVE_REPAIR');active_paid=sum(float(z.get('qty') or 0.) for z in genfills if z['role']=='ACTIVE_REPAIR')
  row={'t':int(t),'responsibilityKind':'V48_GENERATION' if use_gen else 'REPAIR_PARENT','generationId':gid,'epoch':ep,'parentId':pid,'hazard':score,'highAtFrozen05':high,'existingHardConfirmed':hard,'feasible':bool(feas.get('feasible')),'reason':feas.get('reason'),'generationDebt':float(g.get('debt') or 0.) if use_gen else pay_gap,'generationPaid':float(g.get('paid') or 0.) if use_gen else 0.0,'generationRemaining':rem,'passiveRepairQtyThisGeneration':passive_paid,'activeRepairQtyThisGeneration':active_paid,'lastFillRole':self.v53Fills[-1]['role'] if self.v53Fills else None,'worstCaseFloor':float(vals.get('worst_case_floor')),'combinedAbsNet':float(vals.get('combined_abs_net')),'makerRepairParents3s':float(vals.get('maker_repair_parents_3s')),'latestMakerExpansionAgeMs':None if not math.isfinite(float(vals.get('latest_maker_expansion_age_ms',math.nan))) else float(vals['latest_maker_expansion_age_ms']),**feas}
  self.v56Rows.append(row);self.v56EligibleStates+=1;self.v56Epochs.add(ek);self.v56Scores.append(score)
  if high:self.v56HighStates+=1;self.v56HighEpochs.add(ek);self.v56HighScores.append(score)
  if feas.get('feasible'):self.v56FeasibleStates+=1;self.v56FeasibleScores.append(score)
  if high and feas.get('feasible'):self.v56HighFeasibleStates+=1;self.v56HighFeasibleEpochs.add(ek)
 def process(self,t):
  super().process(t);self._sync_hazard_fills();self._shadow_score(t)
 def run_exam_v56(self,models,winner):
  r=super().run_exam_v53(models,winner)
  hard_times={}
  for e in r.get('v52Events',[]):
   if e.get('event')=='GENERATION_EPOCH_HARD_EVENT_CONFIRMED':hard_times[(int(e['generationId']),int(e['epoch']))]=int(e['t'])
  hf={}
  for z in self.v56Rows:
   if z['highAtFrozen05'] and z['feasible']:
    ek=(int(z['generationId']),int(z['epoch']));hf[ek]=min(int(z['t']),hf.get(ek,10**30))
  earlier=sum(1 for ek,t0 in hf.items() if ek not in hard_times or t0<hard_times[ek]);overlap=sum(1 for ek in hf if ek in hard_times)
  r.update({'v56EligibleStates':self.v56EligibleStates,'v56EligibleEpochs':len(self.v56Epochs),'v56HighStates':self.v56HighStates,'v56HighEpochs':len(self.v56HighEpochs),'v56FeasibleStates':self.v56FeasibleStates,'v56HighFeasibleStates':self.v56HighFeasibleStates,'v56HighFeasibleEpochs':len(self.v56HighFeasibleEpochs),'v56HighFeasibleOverlapExistingHardEpochs':overlap,'v56HighFeasibleEarlierThanExistingHardOrNeverHardEpochs':earlier,'v56ScoreStats':qstats(self.v56Scores),'v56FeasibleScoreStats':qstats(self.v56FeasibleScores),'v56Rows':[z for z in self.v56Rows if z['highAtFrozen05'] or z['feasible']][:600]})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','hazard-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v56_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V56','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V56_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];haz=joblib.load(a.hazard_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V56HazardParallelShadow(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,hazard_art=haz)
   try:r=sim.run_exam_v56(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'winner':cr['winner'],'functional':r});print(json.dumps({'marketId':mid,'eligible':r['v56EligibleStates'],'high':r['v56HighStates'],'highFeasible':r['v56HighFeasibleStates'],'hfEpochs':r['v56HighFeasibleEpochs'],'existingHard':r['v52EpochHardConfirmed'],'earlierOrNeverHard':r['v56HighFeasibleEarlierThanExistingHardOrNeverHardEpochs'],'rounds':r['repairExpandRepairRounds']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['functional'].get(k) or 0.) for x in rows)
  agg={k:int(sm(k)) for k in ['v56EligibleStates','v56HighStates','v56FeasibleStates','v56HighFeasibleStates','v56EligibleEpochs','v56HighEpochs','v56HighFeasibleEpochs','v56HighFeasibleOverlapExistingHardEpochs','v56HighFeasibleEarlierThanExistingHardOrNeverHardEpochs']};agg.update({'markets':len(rows),'marketsWithHighFeasible':sum(x['functional']['v56HighFeasibleEpochs']>0 for x in rows),'marketsWithExistingV52Hard':sum(x['functional']['v52EpochHardConfirmed']>0 for x in rows),'existingV52HardEpochs':int(sm('v52EpochHardConfirmed')),'repairExpandRepairRounds':int(sm('repairExpandRepairRounds')),'strictRounds':int(sm('passiveRepairExpandActiveRepairRounds')),'ourPnlDiagnosticOnly':sm('pnlDiagnosticOnly'),'zeroTruthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'zeroOverOwned':sm('overOwnedSubmitViolations'),'zeroRepairDrift':sm('repairToExpandAtFirstFill'),'responsibilityOverfill':sm('v51ResponsibilityOverfill')})
  scores=[];feas_scores=[]
  for x in rows:
   f=x['functional'];
   for z in f.get('v56Rows',[]):
    if z.get('hazard') is not None:scores.append(z['hazard'])
    if z.get('feasible') and z.get('hazard') is not None:feas_scores.append(z['hazard'])
  out={'version':'ETH_REPAIR_V56_TARGET_BOOK_HAZARD_PARALLEL_RESIDUAL_SHADOW','researchOnly':True,'behaviorChange':False,'actionAuthority':False,'frozenHazardBoundary':0.5,'aggregate':agg,'scoreStatsStoredSubset':qstats(scores),'feasibleScoreStatsStoredSubset':qstats(feas_scores),'rows':rows,'decisionHints':{'coverageMultiplierVsExistingHard':(agg['v56HighFeasibleEpochs']/agg['existingV52HardEpochs']) if agg['existingV52HardEpochs'] else None,'earlierOrNeverHardFraction':(agg['v56HighFeasibleEarlierThanExistingHardOrNeverHardEpochs']/agg['v56HighFeasibleEpochs']) if agg['v56HighFeasibleEpochs'] else None},'boundary':['V52/V53 behavior unchanged','Target 1s hazard frozen; p>=0.5 diagnostic only and not swept','OUR own fills + public book only','V50 recoverability and shared budget evaluated counterfactually','passive carrier never cancelled/resized by V56','no winner/PnL trigger','no dream fill','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg,'decisionHints':out['decisionHints']},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
