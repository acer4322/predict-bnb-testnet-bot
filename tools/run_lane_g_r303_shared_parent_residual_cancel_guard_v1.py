from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_multi_action_exact_fork_v1b as ma
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma
EPS=ma.EPS;v2=ma.v2
MODES={'INHERITED_SCOPE_CONTRACT_CANCEL','KEEP_SHARED_PARENT_RESIDUAL'}

class ResidualCancelGuardSim(ma.MultiActionExactForkSim):
    def __init__(self,tape,mid,mode):
        if mode not in MODES:raise ValueError(mode)
        super().__init__(tape,mid,'R303_CONTINGENT_COMPOSITE',1,4)
        self.mode=mode;self.residualDecisionTriggered=False;self.residualDecisionActive=False;self.residualDecisionResolved=False
        self.residualTrigger=None;self.residualEvent=None;self.residualTargetKey=None;self.residualCum0={};self.residualStatus0={};self.residualParentRemaining0=None
        self.residualOverflow0=0.0;self.residualManagerBlocks={'riskContract':0,'reanchor':0,'openOption':0};self.residualGuardBlocks=0

    def _parent_state(self):
        if not self.r303Parent:return None
        return self.r303Ledger.describe_parent(int(self.r303Parent['pid']))

    def _shared_snapshot(self):
        ps=self._parent_state();keys=sorted(self.r303SharedKeys)
        return {'parent':ps,'payoff':self._payoff(),'shared':{k:self._live_status(k) for k in keys},'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,
                'r303OverflowQty':float(self.r303OverflowQty),'r303OverflowRisk':float(self.r303OverflowRisk),'slots':len(self.slot_key),'active':len(self.activeKeys)}

    def _arm_residual_decision(self,t,key,reason):
        ps=self._parent_state();self.residualDecisionTriggered=True;self.residualDecisionActive=True;self.residualTargetKey=str(key)
        self.residualParentRemaining0=float(ps.get('remainingDebt') or 0.0) if ps else None;self.residualOverflow0=float(self.r303OverflowQty)
        self.residualCum0={k:self._live_status(k)['cum'] for k in self.r303SharedKeys};self.residualStatus0={k:self._live_status(k)['status'] for k in self.r303SharedKeys}
        self.residualTrigger={'t':int(t),'mode':self.mode,'targetKey':str(key),'reason':str(reason),'state':self._shared_snapshot()}

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));ps=self._parent_state();rem=float(ps.get('remainingDebt') or 0.0) if ps else 0.0
        eligible=(not self.residualDecisionTriggered and self.forkResolved and str(reason)=='REPAIR_BUNDLE_SCOPE_CONTRACT' and key in self.r303SharedKeys and rem>EPS)
        if eligible:
            self._arm_residual_decision(t,key,reason)
            if self.mode=='KEEP_SHARED_PARENT_RESIDUAL':
                self.residualGuardBlocks+=1
                # Behavior fork: do not send cancel, but report handled so inherited scope-contract
                # does not search for a substitute cancellation in the same decision.
                return True
            ok=super()._request_cancel(t,sid,reason)
            if not ok:self.triggerParityErrors.append('CONTROL_SCOPE_CANCEL_NOT_SENT')
            return ok
        return super()._request_cancel(t,sid,reason)

    def _risk_contract_if_needed(self,t):
        if self.residualDecisionActive:
            self.residualManagerBlocks['riskContract']+=1;return
        return super()._risk_contract_if_needed(t)
    def _reanchor_stale(self,t):
        if self.residualDecisionActive:
            self.residualManagerBlocks['reanchor']+=1;return
        return super()._reanchor_stale(t)
    def _open_one_option(self,t,qv,end):
        if self.residualDecisionActive:
            self.residualManagerBlocks['openOption']+=1;return
        return super()._open_one_option(t,qv,end)

    def _maybe_resolve_residual(self,t):
        if not self.residualDecisionActive or self.residualDecisionResolved:return
        ps=self._parent_state();reasons=[]
        rem=float(ps.get('remainingDebt') or 0.0) if ps else 0.0
        if self.residualParentRemaining0 is not None and abs(rem-float(self.residualParentRemaining0))>EPS:
            reasons.append({'type':'PARENT_REMAINING_DEBT_CHANGE','before':self.residualParentRemaining0,'after':rem})
        for k,c0 in self.residualCum0.items():
            st=self._live_status(k)
            if st['cum']>c0+EPS:reasons.append({'type':'CONFIRMED_SHARED_FILL','key':k,'fillDelta':st['cum']-c0})
            if st['status'] in v2.TERMINAL_STATUSES and str(st['status'])!=str(self.residualStatus0.get(k)):reasons.append({'type':'SHARED_CARRIER_TERMINAL','key':k,'status':st['status'],'priorStatus':self.residualStatus0.get(k)})
        if float(self.r303OverflowQty)>float(self.residualOverflow0)+EPS:
            reasons.append({'type':'CONFIRMED_OVERFLOW_BIRTH','qtyDelta':float(self.r303OverflowQty)-float(self.residualOverflow0),'riskTotal':float(self.r303OverflowRisk)})
        if ps is not None and rem<=EPS:reasons.append({'type':'SHARED_PARENT_COMPLETE'})
        if reasons:
            self.residualDecisionResolved=True;self.residualDecisionActive=False;self.residualEvent={'t':int(t),'reasons':reasons,'state':self._shared_snapshot()}

    def process(self,t):
        super().process(t);self._maybe_resolve_residual(int(t))

    def run_guard(self,w):
        r=super().run_exact(w)
        correct=bool(r['correct']) and self.residualDecisionTriggered and self.residualDecisionResolved and not self.triggerParityErrors
        r.update({'residualMode':self.mode,'residualDecisionTriggered':self.residualDecisionTriggered,'residualDecisionResolved':self.residualDecisionResolved,
                  'residualTrigger':self.residualTrigger,'residualEvent':self.residualEvent,'residualManagerBlocks':self.residualManagerBlocks,
                  'residualGuardBlocks':self.residualGuardBlocks,'residualCorrect':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_residual_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            for mode in ['INHERITED_SCOPE_CONTRACT_CANCEL','KEEP_SHARED_PARENT_RESIDUAL']:
                s=ResidualCancelGuardSim(tmp/f'{m}.json.xz',m,mode)
                try:r=s.run_guard(co[m]['winner'])
                finally:s.close()
                rows.append(r);print(json.dumps({'marketId':m,'mode':mode,'triggered':r['residualDecisionTriggered'],'resolved':r['residualDecisionResolved'],'trigger':r.get('residualTrigger'),'event':r.get('residualEvent'),'terminal':r['terminal'],'correct':r['residualCorrect'],'errors':r['triggerParityErrors']},ensure_ascii=False),flush=True)
        vectors=[];parity={}
        for m in mids:
            rr=[x for x in rows if x['marketId']==m];c=next(x for x in rr if x['residualMode']=='INHERITED_SCOPE_CONTRACT_CANCEL');k=next(x for x in rr if x['residualMode']=='KEEP_SHARED_PARENT_RESIDUAL')
            cs=(c.get('residualTrigger') or {}).get('state');ks=(k.get('residualTrigger') or {}).get('state');parity[str(m)]=cs==ks
            for x in rr:
                ev=(x.get('residualEvent') or {}).get('state') or {};ctrl=(c.get('residualEvent') or {}).get('state') or {}
                ep=ev.get('payoff');cp=ctrl.get('payoff');par=ev.get('parent') or {};cpar=ctrl.get('parent') or {}
                vec={'marketId':m,'mode':x['residualMode'],'eventT':(x.get('residualEvent') or {}).get('t'),'eventReasons':(x.get('residualEvent') or {}).get('reasons'),'correct':x['residualCorrect'],
                     'parentRemainingDebt':par.get('remainingDebt'),'confirmedRepairPaid':par.get('repairPaid'),'overflowQty':ev.get('r303OverflowQty'),'overflowRisk':ev.get('r303OverflowRisk')}
                if ep and cp:vec.update({'deltaBestVsCancel':float(ep['best'])-float(cp['best']),'deltaFloorVsCancel':float(ep['floor'])-float(cp['floor']),'deltaGapVsCancel':float(ep['gap'])-float(cp['gap'])})
                vectors.append(vec)
        out={'version':'LANE_G_R303_SHARED_PARENT_RESIDUAL_CANCEL_GUARD_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'vectors':vectors,
             'gates':{'allDecisionTriggers':all(x['residualDecisionTriggered'] for x in rows),'allLocalStopsResolved':all(x['residualDecisionResolved'] for x in rows),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['residualCorrect'] for x in rows)},'triggerParityByMarket':parity,
             'boundary':['preregistered shared-parent residual cancel guard only','no fixed-time horizon','same R303 shared parent and one-unit authority','no extra risk/credit/qty/slot','manager actions frozen only from cancel/keep fork until next shared structural event','Active unchanged','consumed only','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'vectors':vectors},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
