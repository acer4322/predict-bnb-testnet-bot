from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_multislot_v16_economic_preflight_1946475 as ep
import tools.run_eth_spread_aware_rolling_handoff_1946475 as spread
import tools.run_eth_reanchor_handoff_lease_1946475 as lease
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
EPS=1e-9;MID=1946475;pe=base.pe
BASE={'pnlDiagnosticOnly':-0.2631147540983605,'floor':-0.2631147540983605,'activeRepairFillQty':1.639344262295082}
class PriceLeaseHFT(ep.EconPreflightHFT):
    def __init__(self,*a,**kw):self.priceLeases={};self.priceLeaseEvents=[];super().__init__(*a,**kw)
    def _request_cancel(self,t,row,reason):
        side=str(row.get('side') or '').upper();target,bid,ask=self._target(side)
        if target is None:return False
        pid=int(row['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));hyp=avail+float(row.get('remaining') or 0.0);qty=min(float(row.get('remaining') or 0.0),hyp);ev=self._econ(t,side,qty,target);ev.update({'event':'V16_PRICE_LEASE_PREFLIGHT','stage':'BEFORE_CANCEL','parentId':pid,'oldKey':row['key'],'oldPrice':row['price'],'bestBid':bid,'bestAsk':ask,'triggerReason':reason});self.economicEvents.append(ev)
        if not ev['allow']:return False
        self.priceLeases[str(row['key'])]={'targetPrice':float(target),'approvedAt':int(t),'economic':dict(ev)};self.priceLeaseEvents.append({'t':int(t),'event':'V16_PRICE_LEASE_GRANTED','oldKey':row['key'],'parentId':pid,'side':side,'leasedPrice':float(target),'bestBidAtGrant':bid,'bestAskAtGrant':ask})
        return spread.SpreadAwareRollingHandoffHFT._request_cancel(self,int(t),row,reason)
    def _submit_replacement(self,t,meta,rows,best):
        k=str(meta['key']);lease_meta=self.priceLeases.get(k)
        if not lease_meta:
            self.pendingRollingCancel=None;return False
        target=float(lease_meta['targetPrice']);side=str(meta.get('side') or '').upper();olde=getattr(self,'carrierLedger',{}).get(k,{});pid=int(meta['parentId']);debt=float(self._parent_debt_now(pid));self._sync_parent_occupancy();avail=float(self.parentExecutionOccupancy.available(pid,debt));oldrem=max(0.0,float(olde.get('submittedQty') or 0.0)-float(olde.get('actualFilled') or 0.0));qty=min(oldrem,avail);ev=self._econ(t,side,qty,target);ev.update({'event':'V16_PRICE_LEASE_RECHECK','stage':'AFTER_CANCEL_BEFORE_REPLACEMENT','parentId':pid,'oldKey':k,'leasedPrice':target,'approvedAt':lease_meta['approvedAt']});self.economicEvents.append(ev)
        if not ev['allow']:
            self.priceLeaseEvents.append({'t':int(t),'event':'V16_PRICE_LEASE_ABANDON','oldKey':k,'parentId':pid,'side':side,'leasedPrice':target,'reason':ev['reason']});self.priceLeases.pop(k,None);self.pendingRollingCancel=None;return False
        self.priceLeaseEvents.append({'t':int(t),'event':'V16_PRICE_LEASE_USE','oldKey':k,'parentId':pid,'side':side,'leasedPrice':target,'currentBest':float(best)})
        ok=lease.ReanchorHandoffLeaseHFT._submit_replacement(self,int(t),meta,rows,target)
        self.priceLeases.pop(k,None);return bool(ok)
    def run_price_lease(self,models,winner):
        r=self.run_spread(models,winner);r.update({'v16PriceLeaseEvents':self.priceLeaseEvents,'rollingEconomicEvents':self.economicEvents});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='v16_price_lease_1946475_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz';s=pe.make(PriceLeaseHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:r=s.run_price_lease(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);occ=r.get('occupancyParents') or {}
        finally:s.close()
        ss=pe.safety(r);safe=all(float(x or 0)<=EPS for x in ss.values());occok=all(float(x.get('overReservedQty') or 0)<=EPS for x in occ.values()) if occ else True;m=base.slim(r);lev=r.get('v16PriceLeaseEvents') or [];econ=r.get('rollingEconomicEvents') or []
        gates={'priceLeaseGranted':any(x.get('event')=='V16_PRICE_LEASE_GRANTED' for x in lev),'priceLeaseUsed':any(x.get('event')=='V16_PRICE_LEASE_USE' for x in lev),'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occok,'noNewRepairParent':m['repairParentBirths']==1}
        decision='V16_PRICE_LEASE_ACCOUNTING_FAIL' if not(safe and cons and bound and occok and gates['noNewRepairParent']) else ('V16_PRICE_LEASE_FUNCTIONAL_PASS' if gates['priceLeaseUsed'] else 'V16_PRICE_LEASE_NOT_MATERIALIZED')
        out={'version':'ETH_MULTISLOT_V16_PRICE_LEASE_1946475_V1','date':'2026-09-05','marketId':MID,'researchOnly':True,'decision':decision,'candidate':m,'deltaVsFrozenPartition':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-BASE['pnlDiagnosticOnly'],'floor':m['floor']-BASE['floor'],'activeRepairFillQty':m['activeRepairFillQty']-BASE['activeRepairFillQty']},'priceLeaseEvents':lev,'economicEvents':econ[:300],'repeatedRollingFillQty':r.get('repeatedRollingFillQty'),'reanchorCycles':r.get('reanchorCycles'),'gates':gates,'safety':ss,'allocationParents':parents,'occupancyParents':occ,'boundary':['same initial 2-slot Repair parent','cancel only after V16-approved replacement price exists','approved price is leased across cancel latency and cannot increase','V16 rechecked before replacement submit','blocked future frontier prices do not cause management cancel','handoff lease/Active router otherwise frozen','no Target runtime input','realistic HFT','no dream fill','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':m,'delta':out['deltaVsFrozenPartition'],'leaseEvents':lev,'econTail':econ[-12:],'gates':gates,'safety':ss},ensure_ascii=False))
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
