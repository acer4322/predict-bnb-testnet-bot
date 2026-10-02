from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityContext,SpreadAwareMakerPriorityPolicyV1
EPS=1e-9;MID=1946475;pe=base.pe
LEASE_BASE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}
SPREAD_BASE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.9282608695652179,'floor':-0.9282608695652179,'activeRepairFillQty':0.0}

class V16GatedMultiSlotRollingHFT(lease.ReanchorHandoffLeaseHFT):
    def __init__(self,*a,**kw):
        self.spreadPolicy=SpreadAwareMakerPriorityPolicyV1();self.v16Rows=[];self.v16Blocks=0;self.v16Allows=0;self.economicHold=None;self.holdEvents=[]
        super().__init__(*a,**kw)
    def _v16_eval(self,t,side,qty,target,pid):
        row={'t':int(t),'parentId':int(pid),'side':side,'targetPrice':float(target),'qty':float(qty)}
        x,v=self._feature(int(t),side,float(qty),float(target));phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);env_ok=pair<=env+EPS
        row.update({'phase':phase,'candidatePairSum':pair,'q80Envelope':env,'priceEnvelopeAllow':env_ok,'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'netPairReserveRatio':float(v['net_pair_reserve_ratio'])})
        rec_ok=True
        if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
            sc,th=self.economic.score('expensive_repair_recovers_30s',x);rec_ok=float(sc)>=float(th);row.update({'expensiveRecoveryScore':float(sc),'expensiveRecoveryThreshold':float(th),'expensiveRecoveryAllow':rec_ok})
        else:row['expensiveRecoveryAllow']=True
        row['v16Allow']=bool(env_ok and rec_ok);return row
    def _submit_replacement(self,t,meta,rows,best):
        qv=v1.quotes(self.book);side=str(meta.get('side') or '').upper();target=float(best)
        if qv and side in qv:
            bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);dec=self.spreadPolicy.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None));target=float(dec.price)
        olde=getattr(self,'carrierLedger',{}).get(meta['key'],{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0.0)-float(olde.get('actualFilled') or 0.0));qty=min(oldrem,avail)
        if qty<=EPS:
            return super()._submit_replacement(int(t),meta,rows,target)
        try:er=self._v16_eval(int(t),side,qty,target,pid)
        except Exception as e:
            er={'t':int(t),'parentId':pid,'side':side,'targetPrice':target,'qty':qty,'v16Allow':False,'auditError':repr(e)}
        if qv and side in qv:er.update({'bestBid':float(qv[side]['bid']),'bestAsk':float(qv[side]['ask'])})
        self.v16Rows.append(er)
        if not bool(er.get('v16Allow')):
            self.v16Blocks+=1;self.economicHold={'parentId':pid,'debt':debt,'blockedAt':int(t),'targetPrice':target,'reason':'V16_ECONOMIC_ADMISSION_BLOCK'};self.pendingRollingCancel=None
            ev={'t':int(t),'event':'MULTISLOT_PASSIVE_REANCHOR_ECONOMIC_ABANDON','parentId':pid,'side':side,'targetPrice':target,'authoritativeDebt':debt,'reason':'V16_ECONOMIC_ADMISSION_BLOCK','priceEnvelopeAllow':er.get('priceEnvelopeAllow'),'expensiveRecoveryAllow':er.get('expensiveRecoveryAllow')};self.repeatedRollingEvents.append(ev);self.holdEvents.append(ev);return False
        self.v16Allows+=1
        return super()._submit_replacement(int(t),meta,rows,target)
    def _maybe_reanchor(self,t):
        if self.economicHold is not None and self.pendingRollingCancel is None:
            self._scan_managed_fills(int(t));rows=self._managed_live(int(t));pid=int(self.economicHold['parentId']);debt=float(self._parent_debt_now(pid))
            if abs(debt-float(self.economicHold['debt']))>EPS:
                self.holdEvents.append({'t':int(t),'event':'MULTISLOT_ECONOMIC_HOLD_RELEASE','parentId':pid,'reason':'PARENT_DEBT_CHANGED','oldDebt':self.economicHold['debt'],'newDebt':debt});self.economicHold=None
            elif not rows:
                self.holdEvents.append({'t':int(t),'event':'MULTISLOT_ECONOMIC_HOLD_RELEASE','parentId':pid,'reason':'NO_MANAGED_PASSIVE_LIVE'});self.economicHold=None
            else:
                qv=v1.quotes(self.book);same=[r for r in rows if int(r.get('parentId') or -1)==pid]
                if qv and same:
                    side=same[0]['side'];best=float(qv[side]['bid']);front=max(r['price'] for r in same)
                    if best<=front+EPS:
                        self.holdEvents.append({'t':int(t),'event':'MULTISLOT_ECONOMIC_HOLD_RELEASE','parentId':pid,'reason':'PASSIVE_FRONTIER_RECONNECTED','bestBid':best,'frontPrice':front});self.economicHold=None
                    else:return
                else:return
        return super()._maybe_reanchor(int(t))
    def run_v16(self,models,winner):
        r=self.run_lease(models,winner);r.update({'rollingV16Rows':self.v16Rows[:300],'rollingV16Blocks':self.v16Blocks,'rollingV16Allows':self.v16Allows,'multiSlotEconomicHoldEvents':self.holdEvents[:300]});return r

def slim(r):return base.slim(r)
def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='v16_gated_multislot_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(V16GatedMultiSlotRollingHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_v16(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(v or 0)<=EPS for v in ss.values());occ_bound=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=slim(r)
        gates={'v16EconomicBlockExercised':int(r.get('rollingV16Blocks') or 0)>0,'noV16Bypass':all(not bool(x.get('v16Allow')) for x in (r.get('rollingV16Rows') or [])) if (r.get('rollingV16Rows') or []) else False,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ_bound,'noNewRepairParent':m['repairParentBirths']==1}
        if not safe or not cons or not bound or not occ_bound or not gates['noNewRepairParent']:decision='V16_GATED_MULTISLOT_ACCOUNTING_FAIL'
        elif not gates['v16EconomicBlockExercised']:decision='V16_GATED_MULTISLOT_NOT_EXERCISED'
        elif m['pnlDiagnosticOnly']>=LEASE_BASE['pnlDiagnosticOnly']-1e-7:decision='V16_GATED_MULTISLOT_ECONOMICS_RESTORED'
        else:decision='V16_GATED_MULTISLOT_ECONOMICS_INCOMPLETE'
        out={'version':'ETH_V16_GATED_MULTISLOT_ROLLING_1946475_V1','date':'2026-09-05','researchOnly':True,'marketId':MID,'winnerPostHocOnly':cr['winner'],'decision':decision,'candidate':m,'deltaVsLease':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-LEASE_BASE['pnlDiagnosticOnly'],'floor':m['floor']-LEASE_BASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-LEASE_BASE['activeRepairFillQty']},'deltaVsUngatedSpread':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-SPREAD_BASE['pnlDiagnosticOnly'],'floor':m['floor']-SPREAD_BASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-SPREAD_BASE['activeRepairFillQty']},'gates':gates,'v16Rows':r.get('rollingV16Rows',[]),'v16Blocks':r.get('rollingV16Blocks'),'v16Allows':r.get('rollingV16Allows'),'economicHoldEvents':r.get('multiSlotEconomicHoldEvents',[]),'events':r.get('repeatedRollingEvents',[])[:600],'partitionFills':r.get('partitionFills') or [],'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['single market 1946475','same-parent two-slot Repair plumbing frozen','spread-aware candidate geometry retained','all rolling replacements must re-pass frozen V16 price-envelope/value admission','V16 block abandons the management reanchor and releases Active handoff lease','economic hold prevents immediate repeated cancel until debt/frontier/live-slot state changes','no threshold/qty/model retraining','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'deltaVsLease':out['deltaVsLease'],'deltaVsUngatedSpread':out['deltaVsUngatedSpread'],'gates':gates,'v16Blocks':out['v16Blocks'],'v16Allows':out['v16Allows'],'v16Rows':out['v16Rows'][:12],'holdEvents':out['economicHoldEvents'][:12],'partitionFills':out['partitionFills'],'safety':ss},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
