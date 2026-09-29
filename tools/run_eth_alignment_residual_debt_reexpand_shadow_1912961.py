from __future__ import annotations
import argparse,importlib.util,json,math,os,shutil,sys,tempfile,threading,time,zipfile
from collections import Counter
from pathlib import Path
import joblib
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name,file):
    p=Path(__file__).with_name(file); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

v90d=sib('eth_v90d_for_alignment_shadow','run_eth_repair_v90d_min_legal_incremental_repair_1912961.py')
v90=v90d.v90; v80=v90d.v80; v38=v90d.v38; v1=v90d.v1

class ResidualDebtReexpandShadow(v90d.V90DMinLegalIncremental):
    def __init__(self,*a,**kw):
        self.alignRows=[]; self.alignReasonCounts=Counter(); self.alignFirstFillAt=None; self.alignLastT=None
        self.alignEligible=0; self.alignPostPartialClocks=0; self.alignErrors=[]
        super().__init__(*a,**kw)

    def _filled_v90d_qty(self):
        self._refresh_carrier_ledger(int(getattr(self,'now',0) or 0))
        return sum(float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.0) for k in getattr(self,'v90dSubmitKeys',[]) or [])

    def _classify_shadow(self,t,qv,pE,thesis_side,rec,expand_occ,generation_auth):
        if pE is None or pE < 0.5-EPS: return 'OPPORTUNITY_BELOW_FROZEN_THRESHOLD'
        if int(self.capEnd)-int(t) <= 180000: return 'LATE_NEW_EXPOSURE_FENCE'
        if generation_auth: return 'GENERATION_ALREADY_OWNS_EXPAND'
        if expand_occ: return 'GLOBAL_EXPAND_OCCUPIED'
        if thesis_side not in ('UP','DOWN'): return 'NO_THESIS'
        if isinstance(rec,dict) and rec.get('recoverable') is False: return 'CURRENT_RECOVERABILITY_REJECT'
        px=float(qv[thesis_side]['bid']) if qv and qv[thesis_side].get('bid') is not None else 0.0
        qty=1.0/px if px>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS: return 'EXPAND_VENUE_MIN_INFEASIBLE'
        return 'SHADOW_ELIGIBLE'

    def _prospective_geometry(self,qv,side):
        if side not in ('UP','DOWN') or not qv: return None
        ep=float(qv[side].get('bid') or 0.0)
        if not (EPS<ep<1.0-EPS): return None
        eq=1.0/ep
        floor,u,d,cost=self._raw_floor()
        hu=float(u)+(eq if side=='UP' else 0.0); hd=float(d)+(eq if side=='DOWN' else 0.0); hc=float(cost)+eq*ep
        floor_after_expand=min(hu,hd)-hc
        rs='DOWN' if side=='UP' else 'UP'; rp=float(qv[rs].get('bid') or 0.0)
        if not (EPS<rp<1.0-EPS):
            return {'expandSide':side,'expandPrice':ep,'expandQty':eq,'floorBefore':float(floor),'floorAfterExpand':float(floor_after_expand),'repairSide':rs,'repairPrice':None}
        legal=1.0/rp; debt=abs(hu-hd); alloc=min(debt,legal); overflow=max(0.0,legal-debt)
        ru=hu+(legal if rs=='UP' else 0.0); rd=hd+(legal if rs=='DOWN' else 0.0); rc=hc+legal*rp
        floor_after_repair=min(ru,rd)-rc
        return {
            'expandSide':side,'expandPrice':ep,'expandQty':eq,'floorBefore':float(floor),'floorAfterExpand':float(floor_after_expand),
            'repairSide':rs,'repairPrice':rp,'repairVenueMinQty':legal,'repairDebtAfterHypExpand':debt,
            'repairAllocationInOneLegalCarrier':alloc,'overflowInOneLegalCarrier':overflow,'debtCoversOneLegalRepairSlice':bool(debt+EPS>=legal),
            'floorAfterOneLegalRepairCarrier':float(floor_after_repair),'floorRecoveryFromOneLegalRepairCarrier':float(floor_after_repair-floor_after_expand)
        }

    def process(self,t):
        super().process(t)
        if self.alignLastT==int(t): return
        self.alignLastT=int(t)
        try:
            self._refresh_carrier_ledger(int(t))
            filled=sum(float(self.carrierLedger.get(k,{}).get('actualFilled') or 0.0) for k in getattr(self,'v90dSubmitKeys',[]) or [])
            if filled<=EPS: return
            if self.alignFirstFillAt is None: self.alignFirstFillAt=int(t)
            ai=self.auth_inv(); residual=abs(float(ai['UP'])-float(ai['DOWN']))
            if residual<=EPS: return
            rp=getattr(self,'repairParent',None)
            if not isinstance(rp,dict): return
            paid_parents=set(getattr(self,'v90dPaidParents',set()) or set())
            if int(rp.get('id') or -1) not in paid_parents: return
            qv=v1.quotes(self.book)
            if not qv: return
            self.alignPostPartialClocks+=1
            f=self._coord_feature(t) if hasattr(self,'_coord_feature') else {}
            pE=None
            teacher=getattr(self,'teacher',None)
            if teacher is not None and f:
                x=np.asarray([[float(f[c]) for c in teacher['features']]],np.float32); pE=float(teacher['model'].predict_proba(x)[0,1])
            th=getattr(self,'thesis',None); thside=th.get('side') if isinstance(th,dict) else None
            signal=self._signal_side(qv) if hasattr(self,'_signal_side') else None
            rec=None
            if hasattr(self,'_v75_recoverability') and thside in ('UP','DOWN'):
                try: rec=self._v75_recoverability(t,thside,qv)
                except Exception as e: rec={'recoverable':None,'reason':'RECOVERABILITY_EVAL_ERROR','error':repr(e)}
            expand_occ=False
            if hasattr(self,'_expand_occupied'):
                try: expand_occ=bool(self._expand_occupied())
                except Exception as e: self.alignErrors.append({'t':int(t),'where':'expand_occupied','error':repr(e)})
            generation_auth=bool(getattr(self,'v70gGenerationAuthorized',False))
            reason=self._classify_shadow(t,qv,pE,thside,rec,expand_occ,generation_auth)
            self.alignReasonCounts[reason]+=1
            if reason=='SHADOW_ELIGIBLE': self.alignEligible+=1
            row={
                't':int(t),'secondsLeft':(int(self.capEnd)-int(t))/1000.0,'parentId':int(rp.get('id')),'parentSide':rp.get('side'),
                'residualRepairDebt':residual,'v90dFilledQty':filled,'floor':float(self._raw_floor()[0]),'best':float(max(self.auth_inv()['UP'],self.auth_inv()['DOWN'])-(self._raw_floor()[3])),
                'repairProgressFrac':f.get('repairProgressFrac') if isinstance(f,dict) else None,'pExpand':pE,'thesisSide':thside,'signalSide':signal,
                'generationAuthorized':generation_auth,'expandOccupied':expand_occ,'currentRecoverability':rec,'classification':reason,
                'prospective':self._prospective_geometry(qv,thside if thside in ('UP','DOWN') else signal)
            }
            if len(self.alignRows)<1200: self.alignRows.append(row)
        except Exception as e:
            if len(self.alignErrors)<50: self.alignErrors.append({'t':int(t),'where':'process_shadow','error':repr(e)})

    def run_alignment(self,models,winner):
        r=self.run_v90d(models,winner)
        r.update({'alignmentPostPartialClocks':self.alignPostPartialClocks,'alignmentEligibleClocks':self.alignEligible,'alignmentReasonCounts':dict(self.alignReasonCounts),'alignmentFirstV90DFillObservedAt':self.alignFirstFillAt,'alignmentRows':self.alignRows,'alignmentErrors':self.alignErrors})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']: ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    if a.market_id!=MID: raise ValueError(a.market_id)
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix='eth_alignment_residual_')); stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({'heartbeat':'RESIDUAL_DEBT_REEXPAND_SHADOW','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({'heartbeat':'RESIDUAL_DEBT_REEXPAND_SHADOW_START','market':MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM']; t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM']; tape=tmp/'tapes'/f'{MID}.json.xz'
        sim=ResidualDebtReexpandShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        try: r=sim.run_alignment(models,cr['winner'])
        finally: sim.close()
        rows=r.get('alignmentRows') or []; pvals=[float(x['pExpand']) for x in rows if x.get('pExpand') is not None]
        elig=[x for x in rows if x.get('classification')=='SHADOW_ELIGIBLE']; rec_rej=[x for x in rows if x.get('classification')=='CURRENT_RECOVERABILITY_REJECT']
        out={
            'version':'RESIDUAL_DEBT_REEXPAND_ELIGIBILITY_SHADOW_1912961','date':'2026-09-04','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,'marketId':MID,
            'summary':{
                'v90dFillQty':r.get('v90dFillQty'),'terminalResidualDebt':r.get('v90dTerminalAbsGap'),'postPartialClocks':r.get('alignmentPostPartialClocks'),'eligibleClocks':r.get('alignmentEligibleClocks'),
                'reasonCounts':r.get('alignmentReasonCounts'),'pExpandMin':min(pvals) if pvals else None,'pExpandMedian':float(np.median(pvals)) if pvals else None,'pExpandMax':max(pvals) if pvals else None,
                'firstEligibleT':elig[0]['t'] if elig else None,'firstEligibleSecondsLeft':elig[0]['secondsLeft'] if elig else None,'recoverabilityRejectClocks':len(rec_rej),'errors':len(r.get('alignmentErrors') or [])
            },
            'rows':rows,'errors':r.get('alignmentErrors') or [],
            'behaviorParity':{'fills':r.get('actualFillEvents'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'v90dFillQty':r.get('v90dFillQty')},
            'decision':'PREREGISTER_ONE_RESIDUAL_DEBT_REEXPAND_BEHAVIOR_SEAM' if elig else 'LOCALIZE_DOMINANT_RESIDUAL_DEBT_BLOCKER',
            'boundary':['behavior-inert shadow only','V90D behavior unchanged','strict-past OUR state/quotes only','no Target future action/winner authority','no threshold tuning','<=180s speculative fence unchanged','no 8781']
        }
        op.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'summary':out['summary'],'decision':out['decision'],'behaviorParity':out['behaviorParity']},ensure_ascii=False),flush=True)
    finally:
        stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__': main()
