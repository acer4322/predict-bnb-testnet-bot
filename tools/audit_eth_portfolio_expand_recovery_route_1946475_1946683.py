from __future__ import annotations
import argparse, json, math, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path: sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9

import tools.run_eth_frontier_disconnected_existing_debt_active_ab_1945869 as base
from tools.eth_repair_modular.multi_slot_bundle_guard import (
    MultiSlotBundleRecoverabilityPolicyV1,
    MultiSlotBundleRecoverabilityContext,
    PendingExpandLeg,
    OwnedRepairLeg,
)

quotes_fn=base.g2.prev.fcr.basev1.quotes
pe=base.pe

class PortfolioExpandRecoveryRouteShadow(base.FrontierDisconnectedSalvageHFT):
    def __init__(self,*a,**kw):
        self.portfolioExpandRecoveryRows=[]
        self.portfolioBundlePolicy=MultiSlotBundleRecoverabilityPolicyV1()
        super().__init__(*a,**kw)

    def _owned_repair_legs_for(self, repair_side:str):
        out=[]; details=[]
        try: rows=self.lane_unresolved('REPAIR')
        except Exception: rows=[]
        for key,e,rem in rows:
            rem=float(rem or 0.0)
            if rem<=EPS or str(e.get('side') or '').upper()!=repair_side: continue
            o=getattr(self,'orders',{}).get(str(key),{})
            px=float(o.get('price') or e.get('price') or 0.0)
            if px<=EPS or not math.isfinite(px): continue
            out.append(OwnedRepairLeg(rem,px))
            details.append({'key':str(key),'qty':rem,'price':px,'terminal':bool(e.get('terminalConfirmed') or o.get('terminal')),'cancelRequested':bool(e.get('cancelRequested'))})
        return tuple(out),details

    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper()
        lane=str(getattr(self,'_pendingLane',None) or '')
        if role=='EXPAND' and lane=='V83_ECONOMIC_PARALLEL_EXPAND':
            try:
                self._refresh_carrier_ledger(int(t))
                floor,u,d,cost=self._raw_floor()
                qv=quotes_fn(self.book)
                sideU=str(side).upper();repair='DOWN' if sideU=='UP' else 'UP'
                rbid=None if not qv or repair not in qv else qv[repair].get('bid')
                owned,owned_details=self._owned_repair_legs_for(repair)
                ctx=MultiSlotBundleRecoverabilityContext(
                    thesis_side=sideU,
                    floor_before=float(floor),
                    up_qty=float(u),down_qty=float(d),cost=float(cost),
                    pending_expand_legs=(PendingExpandLeg(float(q),float(p)),),
                    owned_repair_legs=owned,
                    repair_bid=None if rbid is None else float(rbid),
                    max_future_venue_qty=12.0,
                )
                dec=self.portfolioBundlePolicy.evaluate(ctx)
                ceil=dec.economic_repair_ceiling
                frontier_connected=bool(
                    rbid is not None and ceil is not None and math.isfinite(float(rbid)) and math.isfinite(float(ceil))
                    and float(rbid)<=float(ceil)+EPS
                )
                owned_within=sum(x['qty'] for x in owned_details if ceil is not None and x['price']<=float(ceil)+EPS)
                self.portfolioExpandRecoveryRows.append({
                    't':int(t),'side':sideU,'price':float(p),'qty':float(q),'lane':lane,
                    'floorBefore':float(floor),'upQty':float(u),'downQty':float(d),'cost':float(cost),
                    'repairSide':repair,'repairBestBid':None if rbid is None else float(rbid),
                    'ownedRepair':owned_details,'ownedRepairQtyWithinCeiling':float(owned_within),
                    'bundleDecision':dec.__dict__,'repairFrontierConnectedAtBundleCeiling':frontier_connected,
                    'riskState':('RECOVERABLE_AND_FRONTIER_CONNECTED' if dec.recoverable and frontier_connected else
                                 'RECOVERABLE_ONLY_VIA_DISCONNECTED_FRONTIER' if dec.recoverable else
                                 'BUNDLE_UNRECOVERABLE'),
                })
            except Exception as ex:
                self.portfolioExpandRecoveryRows.append({'t':int(t),'side':str(side),'price':float(p),'qty':float(q),'lane':lane,'auditError':repr(ex)})
        return super().submit(t,side,p,q)

    def run_shadow(self,models,winner):
        r=self.run_first_carrier_relay(models,winner)
        r['portfolioExpandRecoveryRows']=self.portfolioExpandRecoveryRows
        return r


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:
        ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',default='1946475,1946683');ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='portfolio_expand_route_shadow_'));stop=threading.Event();started=time.time()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'PORTFOLIO_EXPAND_ROUTE_SHADOW','elapsedSeconds':round(time.time()-started,1)}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'PORTFOLIO_EXPAND_ROUTE_SHADOW_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp)
        cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a)
        t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']
        outrows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=pe.make(PortfolioExpandRecoveryRouteShadow,tape,models,life,cap,tim,econ,price,sur,t44,t47)
            try:r=s.run_shadow(models,cr['winner']);m=base.summarize(s,r)
            finally:s.close()
            rows=r.get('portfolioExpandRecoveryRows',[]) or []
            outrows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],'metrics':{k:v for k,v in m.items() if k not in ('physical','safety','allocationParents','frontierDisconnectedActiveEvents')},'physical':m.get('physical',[]),'expandRecoveryRows':rows})
            print(json.dumps({'marketId':mid,'fills':m['fills'],'pnl':m['pnl'],'floor':m['floor'],'expandRows':rows},ensure_ascii=False),flush=True)
        risky=[rr for x in outrows for rr in x['expandRecoveryRows'] if rr.get('riskState')=='RECOVERABLE_ONLY_VIA_DISCONNECTED_FRONTIER']
        gates={'bothMarketsObserved':len(outrows)==2,'harmfulExpandClocksObserved':all(len(x['expandRecoveryRows'])>0 for x in outrows),'disconnectedRecoverableObserved':len(risky)>0,'allAuditRowsNoError':all('auditError' not in rr for x in outrows for rr in x['expandRecoveryRows'])}
        out={'version':'PORTFOLIO_EXPAND_RECOVERY_ROUTE_SHADOW_V1_20260905','researchOnly':True,'behaviorChange':False,'marketIds':mids,'gates':gates,'rows':outrows,'interpretation':'Tests whether V83 parallel Expand can be algebraically recoverable only by assuming future Repair at/below an economic ceiling while the live Repair best bid is already above that ceiling. Shadow only; no action changes.','boundary':['frontier-disconnected Active salvage behavior frozen','V83 Expand behavior frozen','MultiSlotBundleRecoverabilityPolicyV1 used only as shadow package geometry','repair frontier connected iff current repair best bid <= computed bundle economic repair ceiling','no thresholds changed','winner posthoc only; realistic HFT; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'riskyRows':risky},ensure_ascii=False),flush=True)
    finally:
        stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
