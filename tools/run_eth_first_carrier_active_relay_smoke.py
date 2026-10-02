from __future__ import annotations
import argparse, json, math, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9
ACTIVE_WINDOW_MS=500

import tools.run_eth_parent_occupancy_prospective_guard_ab as pg
import tools.run_eth_dagger60_smoke_v1 as basev1
try:
    from tools.eth_repair_modular.first_carrier_execution_liveness import FirstCarrierExecutionLivenessPolicyV1, FirstCarrierLivenessContext
except ImportError:
    import importlib.util
    _q=Path(__file__).resolve().with_name('first_carrier_execution_liveness.py'); _sp=importlib.util.spec_from_file_location('first_carrier_liveness_sib',_q); _m=importlib.util.module_from_spec(_sp); sys.modules['first_carrier_liveness_sib']=_m; _sp.loader.exec_module(_m); FirstCarrierExecutionLivenessPolicyV1=_m.FirstCarrierExecutionLivenessPolicyV1; FirstCarrierLivenessContext=_m.FirstCarrierLivenessContext

pe=pg.pe

class FirstCarrierActiveRelayHFT(pg.ProspectiveGuardParentOccupancyHFT):
    def __init__(self,*a,**kw):
        self.firstCarrierPolicy=FirstCarrierExecutionLivenessPolicyV1()
        self.firstCarrierEvents=[]
        self.firstCarrierActiveKeys=set()
        self.firstCarrierActiveSubmits=0
        self.firstCarrierActiveFillQty=0.0
        self.firstCarrierBuilderReleases=0
        self._firstCarrierActiveState={}
        super().__init__(*a,**kw)

    def _submit_first_carrier_active(self,t,source_key,e,o,qty,ask):
        side=str(e.get('side') or o.get('side') or '')
        oid=e.get('objectiveId') or o.get('objective_id')
        if side not in ('UP','DOWN') or oid is None: return None
        n=self.n; self.n+=1
        native_side,native_price=basev1.ex.native_order(side,float(ask))
        try:
            if native_side=='BUY': rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(qty),basev1.ex.hbt.GTC,basev1.ex.LIMIT,False))
            else: rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(qty),basev1.ex.hbt.GTC,basev1.ex.LIMIT,False))
        except Exception as ex:
            self.firstCarrierEvents.append({'t':int(t),'event':'FIRST_CARRIER_ACTIVE_RELAY_SUBMIT_ERROR','sourceKey':source_key,'error':str(ex)})
            return None
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':float(ask),'qty':float(qty),'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'EXPAND','objective_id':oid,'execution_role':'TAKER_ACTIVE_FIRST_CARRIER_RELAY'}
        self.placeHist.append((int(t),side,float(qty),float(ask))); self.submits+=1
        if hasattr(self,'localPending'): self.localPending[side][key]={'remaining':float(qty),'submitted':int(t)}
        if hasattr(self,'submitRoleObserved'): self.submitRoleObserved[key]='EXPAND'
        if hasattr(self,'submitRoleTruth'): self.submitRoleTruth[key]='EXPAND'
        if hasattr(self,'submitRoleAuthorized'): self.submitRoleAuthorized[key]='EXPAND'
        self.carrierLedger[key]={'key':key,'side':side,'objectiveId':oid,'objectiveRole':'EXPAND','submittedQty':float(qty),'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':None,'lane':'ACTIVE_FIRST_CARRIER_RELAY'}
        if hasattr(self,'submitTrace'): self.submitTrace.append({'t':int(t),'side':side,'qty':float(qty),'price':float(ask),'pendingRole':'EXPAND','parentId':None,'lane':'ACTIVE_FIRST_CARRIER_RELAY'})
        self.firstCarrierActiveKeys.add(key); self.firstCarrierActiveSubmits+=1
        self._firstCarrierActiveState[key]={'sourceKey':str(source_key),'submitAt':int(t),'fillSeen':0.0}
        self.firstCarrierEvents.append({'t':int(t),'event':'FIRST_CARRIER_ACTIVE_RELAY_SUBMIT','sourceKey':str(source_key),'key':key,'objectiveId':oid,'side':side,'ask':float(ask),'qty':float(qty),'submitRc':rc})
        return key

    def _release_unmaterialized(self,t,rb,reason):
        self.firstCarrierBuilderReleases+=1
        self.firstCarrierEvents.append({'t':int(t),'event':'UNMATERIALIZED_FIRST_CARRIER_RELEASE','builderId':rb.get('id'),'firstKey':rb.get('firstKey'),'reason':reason})
        self.reserveBuilder=None
        th=getattr(self,'thesis',None)
        if isinstance(th,dict) and not bool(th.get('materialized')):
            self.firstCarrierEvents.append({'t':int(t),'event':'UNMATERIALIZED_THESIS_RELEASE','thesisId':th.get('id'),'side':th.get('side')})
            self.thesis=None

    def _reconcile_first_carrier_active(self,t):
        self._refresh_carrier_ledger(int(t))
        for key,a in list(self._firstCarrierActiveState.items()):
            e=self.carrierLedger.get(key,{})
            cur=float(e.get('actualFilled') or 0.0); old=float(a.get('fillSeen') or 0.0)
            if cur>old+EPS:
                inc=cur-old; a['fillSeen']=cur; self.firstCarrierActiveFillQty+=inc
                self.firstCarrierEvents.append({'t':int(t),'event':'FIRST_CARRIER_ACTIVE_RELAY_FILL','key':key,'sourceKey':a['sourceKey'],'incQty':inc,'cumQty':cur})
            o=self.orders.get(key)
            try: live=bool(o and basev1.live(self.snap(o).get('status')))
            except Exception: live=False
            if live and int(t)-int(a['submitAt'])>=ACTIVE_WINDOW_MS and key not in getattr(self,'cancelRequestedAt',{}):
                if self._cancel_key(int(t),key):
                    self.firstCarrierEvents.append({'t':int(t),'event':'FIRST_CARRIER_ACTIVE_RELAY_CANCEL_REMAINDER','key':key,'ageMs':int(t)-int(a['submitAt'])})

    def _maybe_first_carrier_relay(self,t):
        rb=getattr(self,'reserveBuilder',None)
        if not isinstance(rb,dict) or rb.get('firstFillAt') is not None: return False
        key=str(rb.get('firstKey') or '')
        if not key: return False
        self._refresh_carrier_ledger(int(t))
        e=self.carrierLedger.get(key,{})
        o=self.orders.get(key,{})
        qv=basev1.quotes(self.book)
        ask=None
        side=str(e.get('side') or o.get('side') or rb.get('firstSide') or '')
        if qv and side in ('UP','DOWN') and qv.get(side,{}).get('ask') is not None: ask=float(qv[side]['ask'])
        attempted=bool(rb.get('activeFallbackAttempted'))
        ctx=FirstCarrierLivenessContext(
            authorized_role=str(e.get('objectiveRole') or o.get('objective_role') or 'EXPAND'),
            objective_id=e.get('objectiveId') or o.get('objective_id'),
            terminal_confirmed=bool(e.get('terminalConfirmed')),
            actual_filled=float(e.get('actualFilled') or 0.0),
            submitted_qty=float(e.get('submittedQty') or o.get('qty') or rb.get('firstQty') or 0.0),
            active_fallback_already_attempted=attempted,
            seconds_left=(int(self.capEnd)-int(t))/1000.0,
            live_ask=ask,
        )
        d=self.firstCarrierPolicy.evaluate(ctx)
        if d.release_unmaterialized_builder:
            self._release_unmaterialized(int(t),rb,d.reason); return True
        if not d.allow_active_fallback: return False
        new_key=self._submit_first_carrier_active(int(t),key,e,o,float(d.active_qty),float(ask))
        if not new_key: return False
        rb['originalPassiveFirstKey']=key; rb['firstKey']=new_key; rb['firstSide']=side; rb['firstPrice']=float(ask); rb['firstQty']=float(d.active_qty); rb['submittedAt']=int(t); rb['activeFallbackAttempted']=True
        self.firstCarrierEvents.append({'t':int(t),'event':'FIRST_CARRIER_BUILDER_REBOUND_TO_ACTIVE','builderId':rb.get('id'),'sourceKey':key,'activeKey':new_key,'objectiveId':e.get('objectiveId') or o.get('objective_id')})
        return True

    def process(self,t):
        super().process(t)
        self._reconcile_first_carrier_active(t)
        self._maybe_first_carrier_relay(t)

    def cancel_expired(self,t):
        super().cancel_expired(t)
        self._reconcile_first_carrier_active(t)
        self._maybe_first_carrier_relay(t)

    def run_first_carrier_relay(self,models,winner):
        r=self.run_guard(models,winner)
        self._reconcile_first_carrier_active(int(self.capEnd))
        r.update({'firstCarrierExecutionLivenessPolicy':self.firstCarrierPolicy.name,'firstCarrierActiveSubmits':self.firstCarrierActiveSubmits,'firstCarrierActiveFillQty':self.firstCarrierActiveFillQty,'firstCarrierBuilderReleases':self.firstCarrierBuilderReleases,'firstCarrierActiveKeys':sorted(self.firstCarrierActiveKeys),'firstCarrierEvents':self.firstCarrierEvents[:500]})
        return r


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); mid=int(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix=f'first_carrier_active_relay_{mid}_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{mid}.json.xz'
        b=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: br=b.run_guard(models,cr['winner']); bcons,bbound,_=pe.alloc(b,br)
        finally: b.close()
        c=pe.make(FirstCarrierActiveRelayHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try: rr=c.run_first_carrier_relay(models,cr['winner']); cons,bound,parents=pe.alloc(c,rr)
        finally: c.close()
        bs=pe.safety(br); ss=pe.safety(rr); safe=all(float(v or 0)<=EPS for v in ss.values()); occ=all(float(p.get('overReservedQty') or 0)<=EPS for p in (rr.get('occupancyParents') or {}).values())
        physical=float(rr.get('firstCarrierActiveFillQty') or 0)>EPS
        repair_after=physical and int(rr.get('repairParentBirths') or 0)>int(br.get('repairParentBirths') or 0)
        gates={'baselineZeroFill':int(br.get('actualFillEvents') or 0)==0,'relayPolicyActive':rr.get('firstCarrierExecutionLivenessPolicy')=='first_carrier_execution_liveness_v1','activeRelaySubmitted':int(rr.get('firstCarrierActiveSubmits') or 0)>0,'activeRelayPhysicalFill':physical,'repairResponsibilityBornAfterMaterialization':repair_after,'candidateSafetyZero':safe,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'executionOccupancyBounded':occ}
        decision='FUNCTIONAL_PASS_FIRST_CARRIER_ACTIVE_RELAY' if all(gates.values()) else ('REJECT_FIRST_CARRIER_RELAY_SAFETY' if not safe or not cons or not bound or not occ else 'INCOMPLETE_FIRST_CARRIER_RELAY')
        out={'version':'ETH_FIRST_CARRIER_ACTIVE_RELAY_SMOKE_V1','date':'2026-09-05','researchOnly':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baseline':{'fills':int(br.get('actualFillEvents') or 0),'submits':int(br.get('submits') or 0),'rounds':int(br.get('v70dSemanticRounds') or br.get('rounds') or 0),'pnl':float(br.get('pnlDiagnosticOnly') or 0),'floor':float(br.get('floor') or 0),'repairParentBirths':int(br.get('repairParentBirths') or 0)},'candidate':{'fills':int(rr.get('actualFillEvents') or 0),'submits':int(rr.get('submits') or 0),'rounds':int(rr.get('v70dSemanticRounds') or rr.get('rounds') or 0),'pnl':float(rr.get('pnlDiagnosticOnly') or 0),'floor':float(rr.get('floor') or 0),'repairParentBirths':int(rr.get('repairParentBirths') or 0),'repairParentCompletions':int(rr.get('repairParentCompletions') or 0),'firstCarrierActiveSubmits':int(rr.get('firstCarrierActiveSubmits') or 0),'firstCarrierActiveFillQty':float(rr.get('firstCarrierActiveFillQty') or 0),'firstCarrierBuilderReleases':int(rr.get('firstCarrierBuilderReleases') or 0)},'baselineSafety':bs,'candidateSafety':ss,'allocationParents':parents,'events':rr.get('firstCarrierEvents',[])[:500],'boundary':['single chronological zero-fill market first','same authorized EXPAND objective only; execution route changes Passive->Active after terminal zero-fill','GTC active limit at current ask; min legal qty capped by original authorized remainder','one Active fallback attempt per unmaterialized builder','if Active also terminal zero-fill, release unmaterialized builder/thesis rather than permanent ownership deadlock','any confirmed first fill hands control back to existing Repair lifecycle','<=180s no-new-exposure preserved','no Target runtime input; winner post-hoc only; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baseline'],'candidate':out['candidate'],'candidateSafety':ss},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
