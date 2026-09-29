from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_78_persistent_intent_thesis_cycle_capital as r278
v2=r278.v2;EPS=1e-9

class PostRearmFillFavorableRepairAnchorSim(r278.PersistentIntentThesisCycleCapitalSim):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.r282Stats={};self.r282Events=[];self.pendingAnchors=[];self.anchorKeys=set();self.anchorSourceAttempted=set()
 def _inc(self,k,n=1):self.r282Stats[k]=int(self.r282Stats.get(k,0))+int(n)
 def process(self,t):
  s0=len(self.splitEvents);super().process(t)
  for ev in self.splitEvents[s0:]:
   if ev.get('event')!='ROLE_FILL_SPLIT':continue
   k=str(ev.get('key'));m=self.riskTrancheMeta.get(k,{})
   if m.get('kind')!='R278_PERSISTENT_INTENT_THESIS_REARM' or float(ev.get('fillInc') or 0)<=EPS:continue
   if k in self.anchorSourceAttempted:continue
   item={'sourceRiskKey':k,'generation':int(ev.get('generationAtSubmit') or self.scopeGeneration),'riskSide':str(ev.get('side')),
         'riskPrice':float(ev.get('price') or 0.0),'riskFillAt':int(t),'riskFillQty':float(ev.get('fillInc') or 0.0)}
   self.pendingAnchors.append(item);self._inc('ANCHOR_INTENT_BORN')
   self.r282Events.append({'t':int(t),'event':'R282_POST_REARM_FILL_REPAIR_INTENT_BORN',**item})
 def _try_anchor(self,t):
  if not self.pendingAnchors:return False
  item=self.pendingAnchors.pop(0);src=item['sourceRiskKey'];self.anchorSourceAttempted.add(src)
  # One structural attempt only. If current responsibility has left, do not wait for it.
  if self.scopeSide is None or int(self.scopeGeneration)!=int(item['generation']):
   self._inc('BLOCK_SCOPE_LEFT');return False
  if self._has_stale_scope_reservation():
   self._inc('BLOCK_STALE_SCOPE');return False
  if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
   self._inc('BLOCK_CAPACITY');return False
  repair='DOWN' if item['riskSide']=='UP' else 'UP';used=self._used_prices(repair);chosen=None
  for raw in self._live_price_levels(repair):
   p=float(v2.kprice(raw))
   if p in used or p<=EPS:continue
   q=1.0/p
   if not math.isfinite(q) or q<=EPS or q>12+EPS:continue
   if float(item['riskPrice'])+p>1.0+EPS:continue
   sp=self._repair_split(repair,p,q)
   if sp is None:continue
   if float(sp.get('repairQty') or 0.0)<=EPS:continue
   if float(sp.get('overflowQty') or 0.0)>EPS:continue
   chosen=(p,q,sp);break
  if chosen is None:
   self._inc('NO_FULLY_COVERED_FAVORABLE_REPAIR');
   self.r282Events.append({'t':int(t),'event':'R282_FAVORABLE_REPAIR_FALLBACK_ORDINARY','sourceRiskKey':src,'reason':'NO_FULLY_COVERED_FAVORABLE_REPAIR'})
   return False
  p,q,sp=chosen;before=self.n
  if not self._submit_role_v8(t,repair,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
   self._inc('SUBMIT_BLOCKED');return False
  key=f'{repair}_{before}';self.anchorKeys.add(key);self._inc('ANCHOR_SUBMIT')
  ev={'t':int(t),'event':'R282_FAVORABLE_REPAIR_ANCHOR_SUBMIT','sourceRiskKey':src,'key':key,'generation':int(self.scopeGeneration),
      'riskSide':item['riskSide'],'riskPrice':float(item['riskPrice']),'repairSide':repair,'repairPrice':float(p),'qty':float(q),
      'pairSum':float(item['riskPrice'])+float(p),'debtAtSubmit':float(self._scope_debt_qty()),'reservedAfter':float(self._reserved_repair_quota(repair))}
  self.r282Events.append(ev);self.slot_history.append(ev);return True
 def _open_one_option(self,t,qv,end):
  # A realized risk fill may receive one immediate favorable Repair attempt. If not feasible,
  # frozen R278/R257 behavior runs on the same receipt without waiting.
  if self.pendingAnchors and self._try_anchor(t):return
  super()._open_one_option(t,qv,end)
 def run_r282(self,w):
  r=super().run_r278(w)
  # Recover anchor fills from native split telemetry.
  fills=[]
  for ev in self.splitEvents:
   if ev.get('event')=='ROLE_FILL_SPLIT' and str(ev.get('key')) in self.anchorKeys and float(ev.get('fillInc') or 0)>EPS:
    src=next((x for x in self.r282Events if x.get('event')=='R282_FAVORABLE_REPAIR_ANCHOR_SUBMIT' and x.get('key')==ev.get('key')),None)
    ps=(float(src['riskPrice'])+float(ev.get('price') or 0.0)) if src else None
    fills.append({'t':int(ev.get('t') or 0),'key':ev.get('key'),'fillQty':float(ev.get('fillInc') or 0.0),'price':float(ev.get('price') or 0.0),'pairSum':ps,
                  'repairAllocated':float(ev.get('repairAllocated') or 0.0)})
  self.r282Stats['ANCHOR_FILL']=len(fills);self.r282Stats['ANCHOR_FILL_QTY_MILLI']=int(round(sum(x['fillQty'] for x in fills)*1000))
  r.update({'r282Version':'MS4_R2_82_POST_REARM_FILL_FAVORABLE_REPAIR_ANCHOR_V1','r282Stats':dict(self.r282Stats),'r282Events':self.r282Events[:3000],
            'r282AnchorKeys':list(self.anchorKeys),'r282AnchorFills':fills,'r282CorrectnessPass':bool(r.get('r278CorrectnessPass'))})
  return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r282_'))
 try:
  with zipfile.ZipFile(a.bundle) as z:
   co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
   for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
  rows=[];cmp=[]
  for m in mids:
   w=co[m]['winner'];t=tmp/f'{m}.json.xz'
   b=r278.PersistentIntentThesisCycleCapitalSim(t,1,4)
   try:br=b.run_r278(w)
   finally:b.close()
   s=PostRearmFillFavorableRepairAnchorSim(t,1,4)
   try:r=s.run_r282(w)
   finally:s.close()
   rows += [{'marketId':m,'cell':'R278_CONTROL','winnerPostHocOnly':w,**br},{'marketId':m,'cell':'R282_POST_REARM_FILL_FAVORABLE_REPAIR_ANCHOR','winnerPostHocOnly':w,**r}]
   d={'marketId':m,'intentThesis':r.get('r278IntentThesisSide'),'anchorSubmits':int(r.get('r282Stats',{}).get('ANCHOR_SUBMIT',0)),
      'anchorFills':int(r.get('r282Stats',{}).get('ANCHOR_FILL',0)),'anchorFillQty':float(r.get('r282Stats',{}).get('ANCHOR_FILL_QTY_MILLI',0))/1000.0,
      'pnlDelta':float(r['pnlDiagnosticOnly'])-float(br['pnlDiagnosticOnly']),'bestDelta':float(r['best'])-float(br['best']),'floorDelta':float(r['floor'])-float(br['floor']),
      'gapDelta':(float(r['best'])-float(r['floor']))-(float(br['best'])-float(br['floor'])),'fillDelta':int(r['fillEvents'])-int(br['fillEvents']),
      'candidatePnl':float(r['pnlDiagnosticOnly']),'candidateBest':float(r['best']),'candidateFloor':float(r['floor']),'bestGt2':float(r['best'])>2.0,'floorGtMinus1':float(r['floor'])>-1.0,
      'correct':bool(r.get('r282CorrectnessPass')),'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0))}
   cmp.append(d);print(json.dumps(d,ensure_ascii=False),flush=True)
  out={'version':'MS4_R2_82_POST_REARM_FILL_FAVORABLE_REPAIR_ANCHOR_RESULT_V1','researchOnly':True,'markets':mids,'rows':rows,'comparison':cmp,
       'gates':{'correctnessPass':all(x['correct'] for x in cmp),'anchorSubmitExercised':any(x['anchorSubmits']>0 for x in cmp),'anchorFillExercised':any(x['anchorFills']>0 for x in cmp)},
       'boundary':['exact R278 control','risk entry unchanged and never waits for Repair','special Repair born only after confirmed R278 risk fill','one favorable Repair attempt per source rearm key','pure Repair only/no overflow','if infeasible immediately falls back to frozen ordinary Repair','no shared claim yet','max4','<=180s','no Target/winner/future runtime input','realistic HFT','consumed causal test']}
  Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False))
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
