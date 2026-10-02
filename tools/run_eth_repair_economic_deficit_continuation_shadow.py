from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,numpy as np
from pathlib import Path
import run_eth_repair_modular_allocation_v2_generic_hft as g

EPS=1e-9
v38=g.v38;v80=g.v80;v1=g.v1

class EconomicDeficitContinuationShadow(g.ModularAllocationLedgerV2):
    def __init__(self,*a,**kw):
        self.continuationShadow=[]
        super().__init__(*a,**kw)
    def _complete_parent_if_structural(self,t,ai):
        before=int(getattr(self,'v80ShareRepairSettlements',0)); pid=int(self.repairParent.get('id')) if self.repairParent is not None and self.repairParent.get('id') is not None else None
        out=super()._complete_parent_if_structural(t,ai)
        after=int(getattr(self,'v80ShareRepairSettlements',0))
        if after>before and getattr(self,'v80EconomicDeficit',None) is not None and self.repairParent is None:
            row={'t':int(t),'event':'ECONOMIC_DEFICIT_CONTINUATION_SHADOW','settledParentId':pid,'floor':float(self._raw_floor()[0]),'deficitAmount':float(self.v80EconomicDeficit.get('amount') or 0.0),'hasThesis':bool(getattr(self,'thesis',None)),'coordDebt':float(getattr(self,'_coordDebt',0.0) or 0.0),'generationAuthorized':bool(getattr(self,'v70gGenerationAuthorized',False)),'generationId':int(getattr(self,'v70gGenerationId',1))}
            qv=v1.quotes(self.book)
            if qv and getattr(self,'teacher',None) is not None:
                try:
                    f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);side=self._signal_side(qv);rec=self._v75_recoverability(t,side,qv);ctx=v80.OwnershipContext(t=int(t),seconds_left=(int(self.capEnd)-int(t))/1000.0,has_thesis=bool(getattr(self,'thesis',None)),p_expand=pE,signal_side=side,recoverable=bool(rec.get('recoverable')));dec=self.policyProfile.ownership.evaluate(ctx)
                    row.update({'pExpand':pE,'signalSide':side,'recoverable':bool(rec.get('recoverable')),'recoverabilityReason':rec.get('reason'),'ownershipDecision':dec.reason,'wouldCreateThesis':bool(dec.create_thesis),'ownershipSide':dec.side})
                except Exception as ex: row['evalError']=str(ex)
            self.continuationShadow.append(row)
        return out
    def run_shadow(self,models,winner):
        r=self.run_allocation_v2(models,winner);r['economicDeficitContinuationShadow']=self.continuationShadow;return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='eth_deficit_cont_shadow_'));stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({'heartbeat':'ECON_DEFICIT_CONT_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ECON_DEFICIT_CONT_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=EconomicDeficitContinuationShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try:r=s.run_shadow(models,cr['winner'])
            finally:s.close()
            sh=r.get('economicDeficitContinuationShadow',[]);rows.append({'marketId':mid,'pnl':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'shareSettlements':r.get('v80ShareRepairSettlements'),'deficitActive':r.get('v80EconomicDeficitActiveAtEnd'),'shadow':sh});print(json.dumps({'marketId':mid,'shareSettlements':r.get('v80ShareRepairSettlements'),'shadowChecks':len(sh),'wouldCreate':sum(bool(x.get('wouldCreateThesis')) for x in sh),'rows':sh[:6]},ensure_ascii=False),flush=True)
        flat=[z for r in rows for z in r['shadow']];out={'version':'ETH_REPAIR_ECONOMIC_DEFICIT_CONTINUATION_SHADOW','date':'2026-09-03','researchOnly':True,'behaviorChange':False,'markets':mids,'aggregate':{'markets':len(rows),'shareSettlementContinuationChecks':len(flat),'wouldCreateThesis':sum(bool(x.get('wouldCreateThesis')) for x in flat),'recoverable':sum(bool(x.get('recoverable')) for x in flat),'evalErrors':sum('evalError' in x for x in flat)},'rows':rows,'boundary':['shadow only; no submit/action mutation','tests structural repairParent dependency removal only','uses frozen V80 ownership module unchanged','strict-past state only; Target/winner not runtime input','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
