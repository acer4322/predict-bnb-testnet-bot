from __future__ import annotations
import argparse,json,math,shutil,tempfile,threading,time,zipfile,sys
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

class TrancheAuditHFT(spread.SpreadAwareRollingHandoffHFT):
    def __init__(self,*a,**kw):self.trancheAudit=[];super().__init__(*a,**kw)
    def _econ(self,t,side,qty,p):
        out={'qty':float(qty),'price':float(p)}
        try:
            x,v=self._feature(int(t),side,float(qty),float(p));phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);price_ok=pair<=env+EPS;rec=True
            out.update({'phase':phase,'pairSum':pair,'q80Envelope':env,'priceAllow':price_ok,'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'netPairReserveRatio':float(v['net_pair_reserve_ratio']),'projectedFloorDeltaRatio':float(v['projected_floor_delta_ratio'])})
            if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
                sc,th=self.economic.score('expensive_repair_recovers_30s',x);rec=float(sc)>=float(th);out.update({'recoveryScore':float(sc),'recoveryThreshold':float(th),'recoveryAllow':rec})
            else:out['recoveryAllow']=True
            out['v16Allow']=bool(price_ok and rec)
        except Exception as e:out.update({'v16Allow':False,'error':repr(e)})
        return out
    def _submit_replacement(self,t,meta,rows,best):
        side=str(meta.get('side') or '').upper();qv=v1.quotes(self.book)
        if qv and side in qv:
            bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);d=self.spreadPriority.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None));target=float(d.price)
            olde=getattr(self,'carrierLedger',{}).get(str(meta['key']),{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0)-float(olde.get('actualFilled') or 0));full=min(oldrem,avail);legal=(1.0/target) if target>EPS else math.inf
            row={'t':int(t),'parentId':pid,'oldKey':str(meta['key']),'side':side,'bestBid':bid,'bestAsk':ask,'targetPrice':target,'priorityImproved':bool(d.improved),'priorityReason':d.reason,'authoritativeDebt':debt,'availableQty':avail,'oldRemainingQty':oldrem,'legalMinQty':legal,'fullReplacement':self._econ(t,side,full,target),'legalMinTranche':self._econ(t,side,min(legal,avail),target) if math.isfinite(legal) and legal<=avail+EPS else None}
            if row['legalMinTranche'] is not None:
                row['fullRiskSpendPairLoss']=max(0.0,float(row['fullReplacement'].get('pairSum') or 0)-1.0)*full
                row['minRiskSpendPairLoss']=max(0.0,float(row['legalMinTranche'].get('pairSum') or 0)-1.0)*min(legal,avail)
            self.trancheAudit.append(row)
        return super()._submit_replacement(int(t),meta,rows,best)
    def run_audit(self,models,winner):
        r=self.run_spread(models,winner);r['priorityTrancheAudit']=self.trancheAudit;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='priority_tranche_audit_1946475_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'PRIORITY_TRANCHE_AUDIT','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'PRIORITY_TRANCHE_AUDIT_START','marketId':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(TrancheAuditHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_audit(models,cr['winner'])
        finally:s.close()
        out={'version':'ETH_MULTISLOT_PRIORITY_TRANCHE_SIZE_AUDIT_1946475_V1','date':'2026-09-05','researchOnly':True,'behaviorChange':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'candidate':base.slim(r),'rows':r.get('priorityTrancheAudit',[]),'boundary':['shadow only','spread-aware behavior unchanged','compare full inherited replacement qty vs venue-min priority tranche at same strict-past price','V16 existing models only','no fixed cross-market ceiling','no Target runtime input','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'candidate':out['candidate'],'rows':out['rows']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
