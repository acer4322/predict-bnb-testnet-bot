from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,math
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
front=sibling('resp_transition_for_tuning_v1',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
EPS=1e-9;v38=g.v38;v80=g.v80;v1=g.v1

class ThresholdTunedTransitionCandidate(front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,p_expand_threshold=.5,**kw):
        self.pExpandThreshold=float(p_expand_threshold);self.tuningEvents=[]
        super().__init__(*a,**kw)

    def _score_state(self,t,after_kind):
        # Frozen deterministic ResponsibilityTransition first.
        self._refresh_carrier_ledger(int(t))
        debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);th_side=th.get('side') if isinstance(th,dict) else None
        d=self.transitionPolicy.evaluate(front.trans.ResponsibilityTransitionContext(th_side,debt['UP'],debt['DOWN']))
        self.transitionChecks+=1
        tev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':th_side,'repairDebtBySide':debt,'debtRows':rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
        if after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR':
            self.transitionBlocks+=1;tev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(tev);return None
        self.transitionEvents.append(tev)

        # V83 admission seam copied exactly except the preregistered pExpand threshold.
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if after_kind!='REPAIR' or self._coordDebt<=EPS or self.repairParent is None or self.teacher is None:return None
        f=self._coord_feature(t);x=np.asarray([[float(f[c]) for c in self.teacher['features']]],np.float32);pE=float(self.teacher['model'].predict_proba(x)[0,1])
        qv=v1.quotes(self.book)
        if not qv:return None
        self._ownership_if_needed(t,pE,qv)
        row={'t':int(t),'parentId':int(self.repairParent.get('id')),'pExpand':pE,'threshold':self.pExpandThreshold,'submit':False,'reason':'MODEL_REPAIR'}
        self.v83AdmissionChecks+=1
        if pE<self.pExpandThreshold:
            row['reason']='OPPORTUNITY_BELOW_TUNED_THRESHOLD';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        if int(self.capEnd)-int(t)<=180000:
            row['reason']='LATE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        self._refresh_carrier_ledger_no_v70d();self._maybe_advance_generation(t)
        if self.v70gGenerationAuthorized:
            self.v70gDuplicateObjectiveBlocks+=1;row['reason']='GENERATION_ALREADY_OWNS_EXPAND';row['carrier']=self.v70gGenerationCarrier;self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        if self._expand_occupied():
            row['reason']='GLOBAL_EXPAND_OCCUPIED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        th=getattr(self,'thesis',None);side=th.get('side') if th else None
        if side not in ('UP','DOWN'):
            row['reason']='NO_RECOVERABLE_THESIS';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        rec=self._v75_recoverability(t,side,qv);row.update({'side':side,'recoverability':rec})
        if not bool(rec.get('recoverable')):
            row['reason']='WHOLE_PORTFOLIO_UNRECOVERABLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        px=float(qv[side]['bid']);qty=1.0/px if px>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.+EPS:
            row['reason']='VENUE_MIN_INFEASIBLE';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        oid=self._new_objective('EXPAND',side)['id'];n0=self.n
        self._pendingAuthorizedRole='EXPAND';self._pendingAuthorizedObjectiveId=oid;self._pendingParentId=None;self._pendingLane='V83_ECONOMIC_PARALLEL_EXPAND'
        ok=self.submit(t,side,px,qty)
        if not ok:
            row['reason']='SUBMIT_FAILED';self.v83AdmissionBlocks+=1;self.v83Admissions.append(row);self.tuningEvents.append(dict(row));return None
        key=f'{side}_{n0}';self.v44Keys.add(key);self.v44Submits+=1
        row.update({'submit':True,'reason':'TUNED_V83_ECONOMIC_ADMISSION','key':key,'objectiveId':oid,'price':px,'qty':qty})
        self.v44Decisions.append(dict(row));self.v83AdmissionAllows+=1;self.v83Admissions.append(dict(row));self.tuningEvents.append(dict(row))
        self._refresh_carrier_ledger_no_v70d()
        if key in self.carrierLedger:
            self.v70gInheritedExpandBinds+=1;self._bind_existing_expand(t,key,'TUNED_V83_ECONOMIC_ADMISSION')
        return True

    def run_tuned(self,models,winner):
        r=self.run_allocation_v2(models,winner)
        r.update({'pExpandThresholdTuned':self.pExpandThreshold,'transitionChecks':self.transitionChecks,'transitionBlocks':self.transitionBlocks,'transitionEvents':self.transitionEvents[:240],'tuningEvents':self.tuningEvents[:240]})
        return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--p-expand-threshold',type=float,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if not (0.0<=a.p_expand_threshold<=1.0):raise ValueError('threshold must be [0,1]')
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='eth_tune_v1_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'ETH_THRESHOLD_TUNING_V1','threshold':a.p_expand_threshold,'ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'ETH_THRESHOLD_TUNING_V1_START','markets':mids,'threshold':a.p_expand_threshold}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for i,mid in enumerate(mids,1):
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=ThresholdTunedTransitionCandidate(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile(),p_expand_threshold=a.p_expand_threshold)
            try:r=s.run_tuned(models,cr['winner'])
            finally:s.close()
            saf=front.safety(r);adm=[x for x in r.get('tuningEvents',[]) if x.get('submit')]
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnl':float(r.get('pnlDiagnosticOnly') or 0.0),'floor':float(r.get('floor') or 0.0),'fills':int(r.get('actualFillEvents') or 0),'submits':int(r.get('submits') or 0),'admissionAllows':len(adm),'admissionBlocks':int(r.get('v83AdmissionBlocks') or 0),'pExpandAtAllows':[float(x['pExpand']) for x in adm],'transitionBlocks':int(r.get('transitionBlocks') or 0),'safety':saf};rows.append(row)
            print(json.dumps({'idx':i,'of':len(mids),'threshold':a.p_expand_threshold,**{k:row[k] for k in ['marketId','pnl','floor','fills','admissionAllows','transitionBlocks']}},ensure_ascii=False),flush=True)
        traded=[r for r in rows if r['fills']>0];wins=[r for r in rows if r['pnl']>0];pnls=[r['pnl'] for r in rows];floors=[r['floor'] for r in rows]
        safety_zero=all(all(abs(float(v))<=EPS for v in r['safety'].values() if isinstance(v,(int,float))) for r in rows)
        cum=0.;peak=0.;maxdd=0.
        for x in pnls:
            cum+=x;peak=max(peak,cum);maxdd=max(maxdd,peak-cum)
        out={'version':'ETH_TRANSITION_CORRECT_EXPAND_THRESHOLD_TUNING_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'pExpandThreshold':a.p_expand_threshold,'markets':mids,'aggregate':{'marketCount':len(rows),'tradedMarkets':len(traded),'tradeCoverage':len(traded)/len(rows) if rows else 0.0,'positiveMarkets':len(wins),'winRateAllMarkets':len(wins)/len(rows) if rows else 0.0,'aggregatePnl':sum(pnls),'worstMarketPnl':min(pnls) if pnls else None,'bestMarketPnl':max(pnls) if pnls else None,'worstTerminalFloor':min(floors) if floors else None,'cumulativePnlMaxDrawdown':maxdd,'admissionAllows':sum(r['admissionAllows'] for r in rows),'transitionBlocks':sum(r['transitionBlocks'] for r in rows),'allSafetyZero':safety_zero},'rows':rows,'stableNextCompositeShadow':{'status':'KEEP_UNCHANGED','actionAuthority':False},'boundary':['only pExpand threshold differs across configs','ResponsibilityTransition deterministic Repair-first retained','venue-min sizing frozen','Expand price selection frozen','<=180s exposure prohibition frozen','no winner/PnL runtime input','realistic HFT only','no dream fill','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'threshold':a.p_expand_threshold,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
