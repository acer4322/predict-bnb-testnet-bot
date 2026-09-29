from __future__ import annotations
import argparse,json,math,os,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_r303_successor_multi_action_reachability_v1 as reach
EPS=reach.EPS;v2=reach.v2

BRANCHES={'NATIVE_CORE_INVALIDATED_CANCEL','KEEP_STALE_REPAIR_OPTION'}
FROZEN={
  1946317:{'t':1788534352632,'generation':5,'key':'DOWN_39','reason':'CORE_INVALIDATED','side':'DOWN','ownPrice':0.54,'bid':0.55,'ask':0.59,'outstanding':2.0833333333333335,'repaidQty':0.0,'representedQuota':1.8518518518518516},
  1946640:{'t':1788535911300,'generation':3,'key':'DOWN_36','reason':'CORE_INVALIDATED','side':'DOWN','ownPrice':0.61,'bid':0.65,'ask':0.66,'outstanding':2.380952380952381,'repaidQty':0.0,'representedQuota':1.639344262295082},
}

def close(a,b,tol=1e-8):return abs(float(a)-float(b))<=tol

class SuccessorStaleRepairOptionFork(reach.SuccessorReachabilityAudit):
    def __init__(self,tape,mid,branch):
        if branch not in BRANCHES:raise ValueError(branch)
        super().__init__(tape,mid);self.branch=branch;self.succSpec=FROZEN[int(mid)]
        self.succForkTriggered=False;self.succForkActive=False;self.succForkResolved=False
        self.succTrigger=None;self.succEvent=None;self.succErrors=[];self.succBlocks=Counter();self.suppressedCancelCount=0;self.nativeCancelCount=0
        self.targetCum0=0.0;self.targetStatus0=None;self.obOutstanding0=None;self.obRepaid0=None;self.scopeGen0=None;self.scopeSide0=None
        self.activeCum0={};self.r303Overflow0=0.0;self.r303Risk0=0.0;self.submits0=None

    def _target_status(self):
        key=self.succSpec['key'];o=self.orders.get(key)
        if not o:return {'status':'MISSING','cum':0.0,'live':False,'cancelRequested':False}
        try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
        except Exception:st='';cum=float(o.get('cum') or 0.0)
        return {'status':st,'cum':cum,'live':st not in v2.TERMINAL_STATUSES,'cancelRequested':bool(o.get('cancelRequested'))}

    def _payoff(self):
        u=float(self.inv['UP']);d=float(self.inv['DOWN']);c=float(self.cost);best=max(u,d)-c;floor=min(u,d)-c
        return {'upQty':u,'downQty':d,'cost':c,'best':best,'floor':floor,'gap':best-floor}

    def _replacement_candidate(self):
        side=self.succSpec['side']
        try:c=self._candidate_from_levels_v8(side,'ECONOMIC_CORE',False)
        except Exception as e:return {'ok':False,'reason':'CANDIDATE_ERROR','detail':type(e).__name__}
        if c is None:return {'ok':False,'reason':'NO_LEGAL_PASSIVE_CANDIDATE'}
        p,q,proj,split=c
        return {'ok':True,'price':float(p),'qty':float(q),'projected':float(proj),
          'repairQty':float((split or {}).get('repairQty') or 0.0),'overflowQty':float((split or {}).get('overflowQty') or 0.0),
          'overflowRisk':float((split or {}).get('overflowRisk') or 0.0)}

    def _succ_trigger_snapshot(self,t,sid,reason):
        s=self.succSpec;key=self.slot_key.get(int(sid));o=self.orders.get(key) if key is not None else None;ob=self._obligation_current()
        gen=int(ob['generation']) if ob else -1;live=self._live_dedicated_repair_rows(gen) if ob else []
        quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
        qv=v2.base.quotes(self.book);qs=(qv or {}).get(s['side'],{})
        ts=self._target_status()
        snap={'t':int(t),'generation':gen,'key':str(key),'reason':str(reason),'side':str(o.get('side')) if o else None,
          'ownPrice':float(o.get('price') or 0.0) if o else None,'status':ts['status'],'cum':ts['cum'],'cancelRequested':ts['cancelRequested'],
          'obligationOutstanding':float(ob.get('outstanding') or 0.0) if ob else 0.0,'obligationRepaid':float(ob.get('repaidQty') or 0.0) if ob else 0.0,
          'representedQuota':float(quota),'bookBid':float(qs.get('bid')) if qs.get('bid') is not None else None,'bookAsk':float(qs.get('ask')) if qs.get('ask') is not None else None,
          'replacementCandidate':self._replacement_candidate(),'slots':len(self.slot_key),'active':len(self.activeKeys),'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
          'payoff':self._payoff(),'availableExpandCredit':float(self._available_expand_risk_credit()),'riskAuthorityCurrentGeneration':float(self._risk_authority_current_generation()),
          'r303OverflowQty':float(self.r303OverflowQty),'r303OverflowRisk':float(self.r303OverflowRisk)}
        errs=[]
        for fld in ['t','generation','key','reason','side']:
            if str(snap[fld])!=str(s[fld]):errs.append('MISMATCH_'+fld.upper())
        for fld,src in [('ownPrice','ownPrice'),('obligationOutstanding','outstanding'),('obligationRepaid','repaidQty'),('representedQuota','representedQuota'),('bookBid','bid'),('bookAsk','ask')]:
            if snap[fld] is None or not close(snap[fld],s[src]):errs.append('MISMATCH_'+fld.upper())
        if snap['cum']>EPS:errs.append('TARGET_NOT_ZERO_FILL')
        if snap['status'] in v2.TERMINAL_STATUSES:errs.append('TARGET_ALREADY_TERMINAL')
        if snap['replacementCandidate'].get('ok'):errs.append('REPLACEMENT_ALREADY_AVAILABLE')
        self.succErrors.extend(errs);return snap

    def _arm(self,t,sid,reason,pre_snapshot,native_ok=None):
        snap=pre_snapshot
        self.succForkTriggered=True;self.succForkActive=not bool(self.succErrors);self.succTrigger={'branch':self.branch,'state':snap,'nativeCancelReturned':native_ok}
        ts=self._target_status();self.targetCum0=float(ts['cum']);self.targetStatus0=ts['status'];ob=self._obligation_current()
        self.obOutstanding0=float(ob.get('outstanding') or 0.0) if ob else 0.0;self.obRepaid0=float(ob.get('repaidQty') or 0.0) if ob else 0.0
        self.scopeGen0=int(self.scopeGeneration);self.scopeSide0=self.scopeSide;self.activeCum0={k:self._target_like_status(k)['cum'] for k in list(self.activeKeys)}
        self.r303Overflow0=float(self.r303OverflowQty);self.r303Risk0=float(self.r303OverflowRisk);self.submits0=int(self.submits)

    def _target_like_status(self,key):
        o=self.orders.get(key)
        if not o:return {'status':'MISSING','cum':0.0,'live':False,'cancelRequested':False}
        try:s=self.snap(o);st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
        except Exception:st='';cum=float(o.get('cum') or 0.0)
        return {'status':st,'cum':cum,'live':st not in v2.TERMINAL_STATUSES,'cancelRequested':bool(o.get('cancelRequested'))}

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid))
        target=(not self.succForkTriggered and int(t)==int(self.succSpec['t']) and str(key)==self.succSpec['key'] and str(reason)==self.succSpec['reason'])
        if target:
            pre=self._succ_trigger_snapshot(t,sid,reason)
            if self.succErrors:
                return super()._request_cancel(t,sid,reason)
            if self.branch=='NATIVE_CORE_INVALIDATED_CANCEL':
                ok=super()._request_cancel(t,sid,reason)
                self.nativeCancelCount+=int(bool(ok))
                self._arm(t,sid,reason,pre,bool(ok))
                if not ok:self.succErrors.append('NATIVE_CANCEL_NOT_SENT')
                return ok
            self._arm(t,sid,reason,pre,None);self.suppressedCancelCount+=1
            return True
        if self.succForkActive:
            self.succBlocks['CANCEL']+=1;return False
        return super()._request_cancel(t,sid,reason)

    def _resolve(self,t,reasons,replacement=None):
        if not self.succForkActive or self.succForkResolved or not reasons:return
        ob=self.riskRepairObligations.get(int(self.succSpec['generation']));ts=self._target_status()
        self.succForkResolved=True;self.succForkActive=False
        self.succEvent={'t':int(t),'reasons':reasons,'target':ts,'replacementCandidate':replacement if replacement is not None else self._replacement_candidate(),
          'obligation':({k:ob.get(k) for k in ['bornQty','outstanding','repaidQty','passiveRepaidQty','activeRepaidQty','closeReason']} if ob else None),
          'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'payoff':self._payoff(),
          'r303OverflowQty':float(self.r303OverflowQty),'r303OverflowRisk':float(self.r303OverflowRisk),
          'submitsDuringFork':int(self.submits)-int(self.submits0 if self.submits0 is not None else self.submits),'activeFillDelta':sum(max(0.0,self._target_like_status(k)['cum']-c0) for k,c0 in self.activeCum0.items())}

    def _check_execution_events(self,t):
        if not self.succForkActive:return
        reasons=[];ts=self._target_status();ob=self.riskRepairObligations.get(int(self.succSpec['generation']))
        if ts['cum']>self.targetCum0+EPS:reasons.append({'type':'CONFIRMED_TARGET_REPAIR_FILL','fillDelta':ts['cum']-self.targetCum0})
        if ts['status'] in v2.TERMINAL_STATUSES and ts['status']!=self.targetStatus0:reasons.append({'type':'TARGET_REPAIR_TERMINAL','status':ts['status']})
        if ob:
            out=float(ob.get('outstanding') or 0.0);rep=float(ob.get('repaidQty') or 0.0)
            if abs(out-float(self.obOutstanding0))>EPS or abs(rep-float(self.obRepaid0))>EPS:
                reasons.append({'type':'SUCCESSOR_RESPONSIBILITY_PAYMENT','outstandingBefore':self.obOutstanding0,'outstandingAfter':out,'repaidBefore':self.obRepaid0,'repaidAfter':rep})
            if ob.get('closeReason') not in (None,''):reasons.append({'type':'SUCCESSOR_RESPONSIBILITY_TRANSITION','closeReason':ob.get('closeReason')})
        if int(self.scopeGeneration)!=int(self.scopeGen0) or self.scopeSide!=self.scopeSide0:reasons.append({'type':'SCOPE_TRANSITION','generation':int(self.scopeGeneration),'scopeSide':self.scopeSide})
        for k,c0 in self.activeCum0.items():
            st=self._target_like_status(k)
            if st['cum']>c0+EPS:reasons.append({'type':'INHERITED_ACTIVE_FILL','key':k,'fillDelta':st['cum']-c0})
        self._resolve(t,reasons)

    def process(self,t):
        super().process(t);self._check_execution_events(int(t))

    def _risk_contract_if_needed(self,t):
        if self.succForkActive:
            rep=self._replacement_candidate()
            if rep.get('ok'):
                self._resolve(t,[{'type':'LEGAL_REPLACEMENT_REPAIR_MATERIALIZED','candidate':rep}],replacement=rep)
                return super()._risk_contract_if_needed(t)
            self.succBlocks['RISK_CONTRACT']+=1;return
        return super()._risk_contract_if_needed(t)

    def _reanchor_stale(self,t):
        if self.succForkActive:self.succBlocks['REANCHOR']+=1;return
        return super()._reanchor_stale(t)

    def _open_one_option(self,t,qv,end):
        if self.succForkActive:self.succBlocks['OPEN_OPTION']+=1;return
        return super()._open_one_option(t,qv,end)

    def run_successor_fork(self,winner):
        r=self.run_audit(winner)
        correct=bool(r.get('residualCorrect')) and self.succForkTriggered and self.succForkResolved and not self.succErrors
        if self.branch=='NATIVE_CORE_INVALIDATED_CANCEL':correct=correct and self.nativeCancelCount==1
        else:correct=correct and self.suppressedCancelCount==1
        return {'marketId':self.marketId,'branch':self.branch,'triggered':self.succForkTriggered,'resolved':self.succForkResolved,'errors':self.succErrors,
          'trigger':self.succTrigger,'firstStructuralEvent':self.succEvent,'managerBlocksDuringFork':dict(self.succBlocks),'nativeCancelCount':self.nativeCancelCount,'suppressedCancelCount':self.suppressedCancelCount,
          'terminalSecondary':r.get('terminal'),'underlyingCorrect':bool(r.get('residualCorrect')),'correct':bool(correct)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_succ_fork_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for branch in ['NATIVE_CORE_INVALIDATED_CANCEL','KEEP_STALE_REPAIR_OPTION']:
                s=SuccessorStaleRepairOptionFork(tmp/f'{m}.json.xz',m,branch)
                try:r=s.run_successor_fork(co[m]['winner'])
                finally:s.close()
                rows.append(r);print(json.dumps({'marketId':m,'branch':branch,'triggered':r['triggered'],'resolved':r['resolved'],'event':(r.get('firstStructuralEvent') or {}).get('reasons'),'payoff':(r.get('firstStructuralEvent') or {}).get('payoff'),'terminal':r.get('terminalSecondary'),'correct':r['correct'],'errors':r['errors']},ensure_ascii=False),flush=True)
        parity={};vectors=[]
        for m in mids:
            rr=[x for x in rows if x['marketId']==m];ctl=next(x for x in rr if x['branch']=='NATIVE_CORE_INVALIDATED_CANCEL');kp=next(x for x in rr if x['branch']=='KEEP_STALE_REPAIR_OPTION')
            cs=(ctl.get('trigger') or {}).get('state');ks=(kp.get('trigger') or {}).get('state');parity[str(m)]=cs==ks
            for x in rr:
                tr=(x.get('trigger') or {}).get('state') or {};ev=x.get('firstStructuralEvent') or {};tp=tr.get('payoff') or {};ep=ev.get('payoff') or {};ob=ev.get('obligation') or {};tob={'outstanding':tr.get('obligationOutstanding'),'repaidQty':tr.get('obligationRepaid')}
                vec={'marketId':m,'branch':x['branch'],'eventT':ev.get('t'),'eventReasons':ev.get('reasons'),'correct':x['correct'],'target':ev.get('target'),'replacementCandidate':ev.get('replacementCandidate'),
                  'deltaBestFromFrozenTrigger':float(ep['best'])-float(tp['best']) if ep and tp else None,'deltaFloorFromFrozenTrigger':float(ep['floor'])-float(tp['floor']) if ep and tp else None,'deltaGapFromFrozenTrigger':float(ep['gap'])-float(tp['gap']) if ep and tp else None,
                  'responsibilityPaidDelta':float(ob.get('repaidQty') or 0.0)-float(tob.get('repaidQty') or 0.0),'outstandingDelta':float(ob.get('outstanding') or 0.0)-float(tob.get('outstanding') or 0.0),
                  'confirmedNewRiskOverflowQty':float(ev.get('r303OverflowQty') or 0.0)-float(tr.get('r303OverflowQty') or 0.0),'confirmedNewRiskOverflowRisk':float(ev.get('r303OverflowRisk') or 0.0)-float(tr.get('r303OverflowRisk') or 0.0),'activeUsage':float(ev.get('activeFillDelta') or 0.0),'submitsDuringFork':int(ev.get('submitsDuringFork') or 0)}
                vectors.append(vec)
        out={'version':'LANE_G_R303_SUCCESSOR_STALE_REPAIR_OPTION_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'actionVectors':vectors,
             'gates':{'allTriggered':all(x['triggered'] for x in rows),'allResolvedByStructuralEvent':all(x['resolved'] for x in rows),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['correct'] for x in rows),'noManagerSubmitDuringFork':all(int((x.get('firstStructuralEvent') or {}).get('submitsDuringFork') or 0)==0 for x in rows)},
             'triggerParityByMarket':parity,'boundary':['preregistered successor sent-cancel states only','V1C KEEP_SHARED_PARENT_RESIDUAL lineage','branch differs only in exact frozen CORE_INVALIDATED cancel decision','new Manager mutations frozen until first future structural event','existing bestBid>ownPrice is trigger state and not a future stopping event','no fixed-time fork horizon','terminal PnL secondary post-hoc only','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'vectors':vectors},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
