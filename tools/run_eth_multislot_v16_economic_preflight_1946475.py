from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_spread_aware_rolling_handoff_1946475 as spread
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityContext
EPS=1e-9;MID=1946475;pe=base.pe
BASE={'fills':3,'rounds':0,'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}
class EconPreflightHFT(spread.SpreadAwareRollingHandoffHFT):
    def __init__(self,*a,**kw):self.economicEvents=[];super().__init__(*a,**kw)
    def _target(self,side):
        qv=v1.quotes(self.book)
        if not qv or side not in qv:return None,None,None
        bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);d=self.spreadPriority.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None));return float(d.price),bid,ask
    def _econ(self,t,side,qty,target):
        row={'t':int(t),'side':side,'targetPrice':float(target),'qty':float(qty)}
        try:
            x,v=self._feature(int(t),side,float(qty),float(target));phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);allow=pair<=env+EPS;row.update({'phase':phase,'candidatePairSum':pair,'q80Envelope':env,'priceEnvelopeAllow':allow,'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'netPairReserveRatio':float(v['net_pair_reserve_ratio'])})
            exp=True
            if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
                sc,th=self.economic.score('expensive_repair_recovers_30s',x);exp=float(sc)>=float(th);row.update({'expensiveRecoveryScore':float(sc),'expensiveRecoveryThreshold':float(th),'expensiveRecoveryAllow':exp})
            else:row['expensiveRecoveryAllow']=True
            row['allow']=bool(allow and exp);row['reason']='V16_REPAIR_ECONOMIC_ALLOW' if row['allow'] else ('V16_PRICE_ENVELOPE_BLOCK' if not allow else 'V16_EXPENSIVE_RECOVERY_BLOCK')
        except Exception as e:row.update({'allow':False,'reason':'V16_ECONOMIC_EVAL_ERROR','error':repr(e)})
        return row
    def _request_cancel(self,t,row,reason):
        side=str(row.get('side') or '').upper();target,bid,ask=self._target(side)
        if target is None:return False
        pid=int(row['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));hyp_avail=avail+float(row.get('remaining') or 0.0);qty=min(float(row.get('remaining') or 0.0),hyp_avail);ev=self._econ(t,side,qty,target);ev.update({'event':'ROLLING_V16_PREFLIGHT','stage':'BEFORE_CANCEL','parentId':pid,'oldKey':row['key'],'oldPrice':row['price'],'bestBid':bid,'bestAsk':ask,'triggerReason':reason});self.economicEvents.append(ev)
        if not ev['allow']:return False
        return super()._request_cancel(int(t),row,reason)
    def _submit_replacement(self,t,meta,rows,best):
        side=str(meta.get('side') or '').upper();target,bid,ask=self._target(side)
        if target is None:self.pendingRollingCancel=None;return False
        olde=getattr(self,'carrierLedger',{}).get(meta['key'],{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0.0)-float(olde.get('actualFilled') or 0.0));qty=min(oldrem,avail);ev=self._econ(t,side,qty,target);ev.update({'event':'ROLLING_V16_PREFLIGHT','stage':'AFTER_CANCEL_BEFORE_REPLACEMENT','parentId':pid,'oldKey':meta['key'],'bestBid':bid,'bestAsk':ask});self.economicEvents.append(ev)
        if not ev['allow']:
            self.pendingRollingCancel=None
            self.repeatedRollingEvents.append({'t':int(t),'event':'ROLLING_ECONOMIC_ABANDON','oldKey':meta['key'],'parentId':pid,'side':side,'targetPrice':target,'reason':ev['reason']})
            return False
        return super()._submit_replacement(int(t),meta,rows,float(best))
    def run_econ(self,models,winner):
        r=self.run_spread(models,winner);r['rollingEconomicEvents']=self.economicEvents;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_v16_preflight_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(EconPreflightHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_econ(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r);ev=r.get('rollingEconomicEvents') or [];blocks=sum(not bool(x.get('allow')) for x in ev);allows=sum(bool(x.get('allow')) for x in ev)
        gates={'economicPreflightExercised':len(ev)>0,'economicBlockExercised':blocks>0,'noEconomicallyUnauthorizedRollingFill':float(r.get('repeatedRollingFillQty') or 0)<=EPS,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occok,'noNewRepairParent':m['repairParentBirths']==1}
        decision='ECONOMIC_PREFLIGHT_ACCOUNTING_FAIL' if not(safe and cons and bound and occok and gates['noNewRepairParent']) else ('ECONOMIC_PREFLIGHT_PASS_BLOCKS_UNWORTHY_ROLLING' if gates['economicBlockExercised'] else 'ECONOMIC_PREFLIGHT_NOT_DISCRIMINATING')
        out={'version':'ETH_MULTISLOT_V16_ECONOMIC_PREFLIGHT_1946475_V1','date':'2026-09-05','marketId':MID,'researchOnly':True,'decision':decision,'candidate':m,'deltaVsFrozenPartition':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'],'floor':m['floor']-BASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-BASE['activeRepairFillQty']},'economicEvents':ev[:300],'economicAllows':allows,'economicBlocks':blocks,'gates':gates,'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['same initial 2-slot Repair parent','spread-aware target geometry retained only as candidate','V16 economic admission checked before management cancel and again before replacement','blocked preflight preserves existing queue option','post-cancel block releases handoff lease','Active router frozen','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'delta':out['deltaVsFrozenPartition'],'allows':allows,'blocks':blocks,'events':ev[:12],'gates':gates,'safety':ss},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
