from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,threading,time,zipfile,importlib,importlib.util,statistics
from pathlib import Path
import joblib,numpy as np

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9;MID=1945898

def load_or_staged(fullname,filename):
    print(f'IMPORT_STAGE {fullname} START',flush=True)
    try:
        m=importlib.import_module(fullname);print(f'IMPORT_STAGE {fullname} PASS project',flush=True);return m
    except ImportError:
        p=Path(__file__).with_name(filename);s=importlib.util.spec_from_file_location(fullname,p)
        if s is None or s.loader is None:raise ImportError(p)
        m=importlib.util.module_from_spec(s);sys.modules[fullname]=m;s.loader.exec_module(m);print(f'IMPORT_STAGE {fullname} PASS staged',flush=True);return m

ts=load_or_staged('tools.run_eth_phase_native_two_slot_parallel_cycle_1945898','run_eth_phase_native_two_slot_parallel_cycle_1945898.py')
RespCtx=ts.ResponsibilityTransitionContext
pe=ts.pe;pg=ts.pg;v1=ts.v1
GenerationAwareSharedParentDebtAllocationLedgerV3=ts.GenerationAwareSharedParentDebtAllocationLedgerV3
PhaseAdaptiveParallelCycleCapacityPolicy=ts.PhaseAdaptiveParallelCycleCapacityPolicy

class PhaseNativeNSlotHFT(ts.PhaseNativeTwoSlotHFT):
    CONFIG_MAX_SLOTS=2
    CONFIG_PRICE_STEP=0.01
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.maxSlots=max(1,int(self.CONFIG_MAX_SLOTS));self.priceStep=float(self.CONFIG_PRICE_STEP)
        self.allocationLedgerV2=GenerationAwareSharedParentDebtAllocationLedgerV3()
        self.phaseCapacity=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,self.maxSlots),(0.40,max(1,self.maxSlots-1)),(0.70,min(2,self.maxSlots)),(0.90,1)))
        self.slotRows=[];self.slotByKey={};self.slotKeys=[];self.slotBlocks={};self.slotChecks=0;self.slotEligible=0
        self._slotBusy=False;self._generationDebtHandled=set();self.generationDebtAttachEvents=[]
        self.secondSlotKey=None;self.secondSlotObjectiveId=None;self.secondSlotSubmittedAt=None;self.secondSlotFillAt=None;self.secondSlotFillQty=0.0

    def _block_slot(self,reason,row=None):
        self.slotBlocks[reason]=int(self.slotBlocks.get(reason,0))+1
        if row is not None:self.slotRows.append({**row,'event':'NSLOT_ADMISSION_BLOCK','reason':reason})
        return False

    def _current_existing_expand_price(self,side,t):
        self._refresh_carrier_ledger(int(t)) if hasattr(self,'_refresh_carrier_ledger') else None
        xs=[]
        for key,e in getattr(self,'carrierLedger',{}).items():
            if str(e.get('side') or '').upper()!=side:continue
            if str(e.get('objectiveRole') or '').upper()!='EXPAND':continue
            o=getattr(self,'orders',{}).get(str(key),{})
            px=float(o.get('price') or e.get('price') or 0.0)
            if px<=EPS:continue
            at=int(e.get('submittedAt') or o.get('submittedAt') or 0)
            xs.append((at,str(key),px))
        if xs:
            xs.sort();return xs[-1][2],xs[-1][1],'EXISTING_EXPAND_CARRIER'
        return None,None,'NO_EXISTING_EXPAND_PRICE'

    def _previous_slot_price(self,side,qv,t):
        if self.slotKeys:
            k=self.slotKeys[-1];z=self.slotByKey[k];return float(z['price']),k,'ADDED_SLOT'
        p,k,src=self._current_existing_expand_price(side,t)
        if p is not None:return p,k,src
        return float(qv[side]['bid']),None,'LIVE_BID_FALLBACK'

    def _attach_new_epoch_after_resolution(self,t):
        before=len(getattr(self,'generationEpochEvents',[]))
        # Bypass PhaseNativeTwoSlotHFT's single-key attachment and invoke the inherited
        # responsibility-resolution machinery directly. Added slot fills are attached below.
        out=pg.ProspectiveGuardParentOccupancyHFT._attach_new_epoch_after_resolution(self,t)
        for ev in list(getattr(self,'generationEpochEvents',[]))[before:]:
            if ev.get('event')!='EXISTING_PARENT_RESPONSIBILITY_EXECUTION_EPOCH_ATTACH':continue
            src=str(ev.get('sourceKey'))
            if src not in self.slotByKey:continue
            token=f"{src}:epoch:{int(ev.get('epoch') or 0)}"
            if token in self._generationDebtHandled:continue
            self._generationDebtHandled.add(token)
            pid=int(ev.get('parentId'));add=float(ev.get('addedDebt') or 0.0)
            pb=self.allocationLedgerV2.describe_parent(pid);ar=self.allocationLedgerV2.attach_generation_debt(pid,token,add);pa=self.allocationLedgerV2.describe_parent(pid)
            row={'t':int(t),'event':'NSLOT_GENERATION_DEBT_ATTACH','sourceKey':src,'parentId':pid,'epoch':int(ev.get('epoch') or 0),'parentBefore':pb,'parentAfter':pa,**ar.__dict__}
            self.generationDebtAttachEvents.append(row);self.slotRows.append(dict(row))
        return out

    def _scan_second_slot_fill(self,t):
        if not self.slotKeys:return
        self._refresh_carrier_ledger(int(t))
        for key in list(self.slotKeys):
            z=self.slotByKey[key];e=getattr(self,'carrierLedger',{}).get(key,{})
            now=float(e.get('actualFilled') or 0.0);old=float(z.get('fillSeen') or 0.0)
            if now<=old+EPS:continue
            inc=now-old;z['fillSeen']=now;z['fillQty']=float(z.get('fillQty') or 0.0)+inc
            if z.get('fillAt') is None:z['fillAt']=int(t)
            self.slotRows.append({'t':int(t),'event':'NSLOT_CONFIRMED_EXPAND_FILL','slotOrdinal':z['ordinal'],'key':key,'incQty':inc,'cumQty':now})
            if int(z['ordinal'])==2:
                self.secondSlotFillQty=float(z['fillQty']);self.secondSlotFillAt=z['fillAt']

    def _maybe_second_slot(self,t):
        if self._slotBusy or len(self.slotKeys)>=max(0,self.maxSlots-1):return False
        self._slotBusy=True
        try:
            th=getattr(self,'thesis',None);rp=getattr(self,'repairParent',None)
            if not isinstance(th,dict) or not bool(th.get('materialized')):return self._block_slot('FIRST_THESIS_NOT_MATERIALIZED')
            if not isinstance(rp,dict):return self._block_slot('NO_LIVE_REPAIR_PARENT')
            pid=int(rp.get('id'));parent_side=str(rp.get('side') or '').upper()
            if parent_side not in ('UP','DOWN'):return self._block_slot('INVALID_REPAIR_PARENT_SIDE')
            qv=v1.quotes(self.book)
            if not qv:return self._block_slot('NO_QUOTES')
            side=str(th.get('side') or '').upper();signal=self._signal_side(qv);ordinal=2+len(self.slotKeys)
            row={'t':int(t),'normalizedPhase':self._normalized_phase(t),'requestedMaxSlots':self.maxSlots,'priceStep':self.priceStep,'slotOrdinal':ordinal,'existingThesisId':th.get('id'),'thesisSide':side,'signalSide':signal,'repairParentId':pid,'repairParentSide':parent_side}
            self.slotChecks+=1
            if side not in ('UP','DOWN') or signal!=side:return self._block_slot('SIGNAL_NOT_SAME_AS_THESIS',row)
            if int(self.capEnd)-int(t)<=180000:return self._block_slot('INHERITED_LATE_EXPOSURE_FENCE',row)
            cap=int(self.phaseCapacity.capacity_at(row['normalizedPhase']));row['developmentCapacity']=cap
            if cap<ordinal:return self._block_slot('PHASE_CAPACITY_BELOW_NEXT_SLOT',row)
            pay=self._current_payoffs();gap=float(pay.get('gap') or 0.0);row['payoffGap']=gap;row['floorBefore']=float(pay.get('floor') or 0.0)
            debt,debt_rows=self._live_repair_debt_by_side();row['repairDebtBySide']=dict(debt);row['debtRows']=debt_rows[:8]
            if max(float(debt['UP']),float(debt['DOWN']))<=EPS or gap<=EPS:return self._block_slot('NO_POSITIVE_REPAIR_DEBT',row)
            parent_desc=self.allocationLedgerV2.describe_parent(pid);row['allocationParentBefore']=parent_desc
            if parent_desc is None:return self._block_slot('ALLOCATION_PARENT_NOT_REGISTERED',row)
            f=self._coord_feature(int(t));x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1]);row['pExpand']=pE
            if pE<.5:return self._block_slot('P_EXPAND_BELOW_FROZEN_THRESHOLD',row)
            rec=self._v75_recoverability(int(t),side,qv);row['recoverability']=rec
            if not bool(rec.get('recoverable')):return self._block_slot('UNRECOVERABLE',row)
            td=self.transitionPolicy.evaluate(RespCtx(side,float(debt['UP']),float(debt['DOWN'])));row.update({'transitionAllow':bool(td.allow_expand_ownership),'transitionRole':td.bind_role,'transitionReason':td.reason})
            if not td.allow_expand_ownership or td.bind_role!='EXPAND':return self._block_slot('RESPONSIBILITY_TRANSITION_BLOCK',row)
            # Co-adaptive V3: do not reuse the legacy global one-Expand physical occupancy lock.
            # Concurrency is bounded explicitly by requested maxSlots, distinct objective IDs and generation tokens.
            prev_px,prev_key,price_src=self._previous_slot_price(side,qv,t);px=round(prev_px+self.priceStep,2)
            row.update({'previousSlotPrice':prev_px,'previousSlotKey':prev_key,'previousPriceSource':price_src,'price':px})
            if px<=0.0+EPS or px>=1.0-EPS:return self._block_slot('PRICE_STEP_OUT_OF_VENUE_RANGE',row)
            if any(abs(float(z['price'])-px)<=1e-10 for z in self.slotByKey.values()):return self._block_slot('DUPLICATE_ADDED_SLOT_PRICE',row)
            qty=1.0/px if px>EPS else math.inf;row['qty']=qty
            if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:return self._block_slot('VENUE_MIN_INFEASIBLE',row)
            oid=self._new_objective('EXPAND',side)['id'];n0=self.n
            self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane=f'PHASE_NATIVE_NSLOT_{ordinal}'
            ok=self.submit(int(t),side,px,qty)
            if not ok:return self._block_slot('SUBMIT_FAILED',row)
            key=f'{side}_{n0}';z={'ordinal':ordinal,'key':key,'objectiveId':oid,'submittedAt':int(t),'price':px,'qty':qty,'fillSeen':0.0,'fillQty':0.0,'fillAt':None}
            self.slotKeys.append(key);self.slotByKey[key]=z
            if ordinal==2:
                self.secondSlotKey=key;self.secondSlotObjectiveId=oid;self.secondSlotSubmittedAt=int(t)
            if hasattr(self,'v44Keys'):self.v44Keys.add(key)
            if hasattr(self,'v44Submits'):self.v44Submits+=1
            admission={**row,'submit':True,'reason':'PHASE_NATIVE_NSLOT_ADMISSION','key':key,'objectiveId':oid}
            if hasattr(self,'v83Admissions'):self.v83Admissions.append(dict(admission))
            if hasattr(self,'v83AdmissionAllows'):self.v83AdmissionAllows+=1
            self.slotEligible+=1;self.slotRows.append({'event':'NSLOT_EXPAND_SUBMIT',**admission})
            return True
        finally:self._slotBusy=False

    def run_nslot(self,models,winner):
        r=self.run_guard(models,winner)
        r.update({'requestedMaxSlots':self.maxSlots,'priceStep':self.priceStep,'nslotChecks':self.slotChecks,'nslotEligible':self.slotEligible,'nslotBlocks':self.slotBlocks,'nslotRows':self.slotRows[:1200],'nslotKeys':list(self.slotKeys),'nslotState':{k:dict(v) for k,v in self.slotByKey.items()},'generationAwareAllocationLedger':getattr(self.allocationLedgerV2,'name',None),'generationDebtAttachEvents':self.generationDebtAttachEvents,'generationDebtAttachApplied':sum(bool(x.get('applied')) for x in self.generationDebtAttachEvents),'generationAttachments':self.allocationLedgerV2.describe_generation_attachments()})
        return r

def make_variant(max_slots,step):
    class ConfiguredNSlot(PhaseNativeNSlotHFT):
        CONFIG_MAX_SLOTS=int(max_slots);CONFIG_PRICE_STEP=float(step)
    ConfiguredNSlot.__name__=f'ConfiguredNSlot_{max_slots}_{"plus" if step>0 else "minus"}'
    return ConfiguredNSlot

def compact(r):
    st=r.get('nslotState') or {};fills=[float(x.get('fillQty') or 0.0) for x in st.values()]
    return {'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'fills':int(r.get('actualFillEvents') or 0),'rounds':int(r.get('v70dSemanticRounds') or r.get('rounds') or 0),'repairParentBirths':int(r.get('repairParentBirths') or 0),'repairParentCompletions':int(r.get('repairParentCompletions') or 0),'parallelRepairSubmits':int(r.get('parallelRepairSubmits') or 0),'parallelRepairActiveFillQty':float(r.get('parallelRepairActiveFillQty') or 0.0),'addedSlotSubmits':len(st),'addedSlotsFilled':sum(x>EPS for x in fills),'addedSlotFillQty':sum(fills),'generationDebtAttachApplied':int(r.get('generationDebtAttachApplied') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=MID);ap.add_argument('--max-slot-count',type=int,default=5);ap.add_argument('--output',default='AUTO');a=ap.parse_args()
    if int(a.market_id)!=MID:raise ValueError(a.market_id)
    maxn=max(2,min(8,int(a.max_slot_count)));tmp=Path(tempfile.mkdtemp(prefix='nslot_ladder_1945898_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'NSLOT_SWEEP_1945898','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'NSLOT_SWEEP_1945898_START','slotCounts':list(range(1,maxn+1)),'steps':[0.01,-0.01]}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{MID}.json.xz'
        # Slot-count 1 is the shared control and is executed once.
        b=pe.make(pg.ProspectiveGuardParentOccupancyHFT,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:br=b.run_guard(models,cr['winner']);bcons,bbound,bparents=pe.alloc(b,br)
        finally:b.close()
        bs=pe.safety(br);base=ts.compact(br);rows=[{'slotCount':1,'priceStep':0.0,'branch':'CONTROL','metrics':compact(br),'safety':bs,'allocationConservation':bool(bcons),'allocationParentDebtBounded':bool(bbound),'decision':'CONTROL'}]
        for step in (0.01,-0.01):
            for n in range(2,maxn+1):
                cls=make_variant(n,step);sim=pe.make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
                try:r=sim.run_nslot(models,cr['winner']);cons,bound,parents=pe.alloc(sim,r)
                finally:sim.close()
                ss=pe.safety(r);safe=all(float(v or 0.0)<=EPS for v in ss.values());m=compact(r);attachments=[x for x in (r.get('generationDebtAttachEvents') or []) if x.get('applied')]
                attach_qty=sum(float(x.get('added_debt') or x.get('addedDebt') or 0.0) for x in attachments)
                fill_qty=float(m['addedSlotFillQty']);attach_match=abs(attach_qty-fill_qty)<=1e-7 if fill_qty>EPS else len(attachments)==0
                decision='ACCOUNTING_FAIL' if not(safe and cons and bound and attach_match) else ('NO_ADDED_SLOT_FILL' if m['addedSlotsFilled']==0 else 'FUNCTIONAL')
                row={'slotCount':n,'priceStep':step,'branch':'PLUS_0P01' if step>0 else 'MINUS_0P01','decision':decision,'metrics':m,'deltaVsControl':{'pnlDiagnosticOnly':m['pnlDiagnosticOnly']-base['pnlDiagnosticOnly'],'floor':m['floor']-base['floor'],'fills':m['fills']-base['fills'],'rounds':m['rounds']-base['rounds'],'repairParentCompletions':m['repairParentCompletions']-base['repairParentCompletions']},'safety':ss,'allocationConservation':bool(cons),'allocationParentDebtBounded':bool(bound),'attachedDebtMatchesAddedSlotFill':attach_match,'slotState':r.get('nslotState'),'blocks':r.get('nslotBlocks'),'generationDebtAttachEvents':r.get('generationDebtAttachEvents',[])[:100]}
                rows.append(row);print(json.dumps({'progress':f'{row["branch"]} slots={n}','decision':decision,'metrics':m,'delta':row['deltaVsControl'],'safety':ss},ensure_ascii=False),flush=True)
        functional=[x for x in rows if x['slotCount']>1 and x['decision']=='FUNCTIONAL']
        # Same-market architecture ranking: prioritize realized cycle/liveness first, then PnL, then fewer slots.
        ranked=sorted(functional,key=lambda x:(x['metrics']['rounds'],x['metrics']['repairParentCompletions'],x['metrics']['fills'],x['metrics']['pnlDiagnosticOnly'],-x['slotCount']),reverse=True)
        best=ranked[0] if ranked else None
        out={'version':'PHASE_NATIVE_NSLOT_PRICE_LADDER_1945898_RESULT_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'control':rows[0],'rows':rows,'bestSameMarketArchitecture':({'slotCount':best['slotCount'],'branch':best['branch'],'priceStep':best['priceStep'],'metrics':best['metrics'],'deltaVsControl':best['deltaVsControl']} if best else None),'selectionCaveat':'One consumed same-market architecture sweep only. Best row is not promotion authority; replicate on strict structure-selected markets before cohort scoring.','globalObjectiveReminder':{'avgWinningPnlGt':2.0,'eachLosingPnlGt':-1.0,'winRateGt':0.50},'boundary':['same market only','1-5 slot sweep','deterministic previous-slot price +/-0.01','slot-scoped occupancy replaces legacy global one-Expand lock only for this development sweep','confirmed-fill generation debt only','co-adaptive Contract V3','no Target runtime input','realistic HFT only','no dream fill','no 8781']}
        op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json' if a.output=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'best':out['bestSameMarketArchitecture'],'rows':[{'slotCount':x['slotCount'],'branch':x['branch'],'decision':x['decision'],'metrics':x['metrics'],'delta':x.get('deltaVsControl')} for x in rows]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
