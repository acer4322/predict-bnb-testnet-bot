from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib,numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityContext,SpreadAwareMakerPriorityPolicyV1
EPS=1e-9;MID=1946475;pe=base.pe

class AuditHFT(lease.ReanchorHandoffLeaseHFT):
    def __init__(self,*a,**kw):
        self.spreadPolicy=SpreadAwareMakerPriorityPolicyV1();self.econRows=[]
        super().__init__(*a,**kw)
    def _submit_replacement(self,t,meta,rows,best):
        qv=v1.quotes(self.book);side=str(meta.get('side') or '').upper();target=float(best)
        if qv and side in qv:
            bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);dec=self.spreadPolicy.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None));target=float(dec.price)
        olde=getattr(self,'carrierLedger',{}).get(meta['key'],{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0.0)-float(olde.get('actualFilled') or 0.0));qty=min(oldrem,avail)
        row={'t':int(t),'parentId':pid,'side':side,'targetPrice':target,'qty':qty,'bestBid':float(qv[side]['bid']) if qv and side in qv else None,'bestAsk':float(qv[side]['ask']) if qv and side in qv else None}
        try:
            x,v=self._feature(int(t),side,qty,target);phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);row.update({'phase':phase,'candidatePairSum':pair,'q80Envelope':env,'priceEnvelopeAllow':pair<=env+EPS,'pairEdge':float(v['pair_edge']),'matchFraction':float(v['match_fraction']),'netPairReserveRatio':float(v['net_pair_reserve_ratio'])})
            if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
                sc,th=self.economic.score('expensive_repair_recovers_30s',x);row.update({'expensiveRecoveryScore':float(sc),'expensiveRecoveryThreshold':float(th),'expensiveRecoveryAllow':float(sc)>=float(th)})
            else:row['expensiveRecoveryAllow']=True
            row['v16Allow']=bool(row['priceEnvelopeAllow'] and row['expensiveRecoveryAllow'])
        except Exception as e:row.update({'auditError':repr(e),'v16Allow':None})
        self.econRows.append(row)
        return super()._submit_replacement(int(t),meta,rows,target)
    def run_audit(self,models,winner):
        r=self.run_lease(models,winner);r['rollingV16EconomicRows']=self.econRows;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='rolling_v16_audit_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        s=pe.make(AuditHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_audit(models,cr['winner'])
        finally:s.close()
        out={'version':'ETH_MULTISLOT_ROLLING_V16_ECONOMIC_ADMISSION_AUDIT_1946475_V1','date':'2026-09-05','marketId':MID,'behaviorChange':False,'winnerPostHocOnly':cr['winner'],'candidate':base.slim(r),'rows':r.get('rollingV16EconomicRows',[]),'boundary':['shadow only','same spread-aware rolling target geometry','existing V16 price envelope and expensive-repair recovery models only','no Target runtime input','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
