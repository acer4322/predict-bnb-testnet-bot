from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_17_recoverable_safe_surplus_shadow.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r217',_STAGED);r217=importlib.util.module_from_spec(sp);sp.loader.exec_module(r217)
else:
    import tools.run_eth_ms4_r2_17_recoverable_safe_surplus_shadow as r217
r28=r217.r28;v2=r217.v2;EPS=1e-9

class RecoverabilityBackedSurplusSim(r217.RecoverableSafeSurplusShadow):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.r218=Counter();self.r218events=[];self.recoverableExpandKeys=set();self.recoverableRiskRefunded=0.0
    def _has_live_recoverable_expand(self):
        for k in list(self.recoverableExpandKeys):
            o=self.orders.get(k)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st not in v2.TERMINAL_STATUSES:return True
        return False
    def _expand_authority(self,t,side,p,q):
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after);real=float(self._available_expand_risk_credit())
        if real+EPS>=risk:return {'ok':True,'source':'REALIZED_CREDIT','riskCost':risk,'realCredit':real,'recoverability':None}
        if self._has_live_recoverable_expand():return {'ok':False,'source':'RECOVERABILITY_ALREADY_LIVE','riskCost':risk,'realCredit':real,'recoverability':None}
        rec=self._recoverability(side,p,q)
        if not rec.get('recoverable'):
            return {'ok':False,'source':'NOT_RECOVERABLE','riskCost':risk,'realCredit':real,'recoverability':rec}
        return {'ok':True,'source':'RECOVERABILITY_BACKED','riskCost':risk,'realCredit':real,'recoverability':rec}
    def _submit_expand_with_authority(self,t,side,p,q,proj,auth,event_name):
        before_n=self.n
        if not self._submit_role_v8(t,side,'SATELLITE_EXPAND',p,q,proj,None):return False
        key=f'{side}_{before_n}'
        if auth['source']=='RECOVERABILITY_BACKED':
            self.recoverableExpandKeys.add(key);self.r218['RECOVERABILITY_BACKED_EXPAND_SUBMIT']+=1
            rec=auth.get('recoverability') or {};ev={'t':int(t),'event':event_name,'key':key,'generation':int(self.key_scope_gen.get(key,self.scopeGeneration)),'side':side,'expandPrice':float(p),'expandQty':float(q),'riskCost':float(auth['riskCost']),'realCredit':float(auth['realCredit']),'forwardPairSum':rec.get('forwardPairSum'),'futureRepairPrice':rec.get('futureRepairPrice'),'futureRepairQty':rec.get('futureRepairQty'),'floorBefore':rec.get('floorBefore'),'floorAfterExpand':rec.get('floorAfterExpand'),'floorAfterOwned':rec.get('floorAfterOwned')};self.r218events.append(ev);self.slot_history.append(ev)
        return True
    def _try_parallel_expand_r218(self,t,side):
        if self.scopeSide is None or side!=self.scopeSide:return False
        if len(self.slot_key)>=self.max_slots or len(self._live_role_rows(side=side))>=self.max_slots:return False
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return False
        p,q,proj,split=cand;auth=self._expand_authority(t,side,p,q)
        if not auth['ok']:
            self.r218['PARALLEL_EXPAND_AUTHORITY_BLOCK_'+auth['source']]+=1;return False
        if self._submit_expand_with_authority(t,side,p,q,proj,auth,'RECOVERABILITY_BACKED_PARALLEL_EXPAND_SUBMIT'):
            self.parallelExpandFallbackSubmits+=1;return True
        return False
    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS:self.veto['LATE_180S']+=1;return
        if self._has_stale_scope_reservation():self.staleScopeWaits+=1;self.veto['STALE_SCOPE_RESERVATION_WAIT']+=1;return
        decision=self._role_decision(qv)
        if decision is None:
            if self.scopeSide is not None and self._reserved_repair_quota()>=self._scope_debt_qty()-EPS:
                if self._try_parallel_expand_r218(t,self.scopeSide):
                    if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
                    return
            self.veto['ROLE_WAIT']+=1
            if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
            return
        side,role,require_pair,require_budget=decision
        if len(self._live_role_rows(side=side))>=self.max_slots:self.veto['SIDE_SLOT_CAP_FULL']+=1;return
        cand=self._candidate_from_levels_v8(side,role,require_pair)
        if cand is None:
            self.role_budget_blocks[role]+=1
            if role=='SATELLITE_REPAIR' and self._core_for_side(side) is not None:self._try_parallel_expand_r218(t,self.scopeSide)
            if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
            return
        p,q,proj,split=cand
        if role=='SATELLITE_EXPAND':
            auth=self._expand_authority(t,side,p,q)
            if not auth['ok']:
                self.expandCreditBlocks+=1;self.r218['PRIMARY_EXPAND_AUTHORITY_BLOCK_'+auth['source']]+=1
                if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
                return
            self._submit_expand_with_authority(t,side,p,q,proj,auth,'RECOVERABILITY_BACKED_PRIMARY_EXPAND_SUBMIT')
        else:self._submit_role_v8(t,side,role,p,q,proj,split)
        if self.scopeSide is not None:self._parallel_repair_fill(t,self._repair_side())
    def process(self,t):
        old_scope=self.scopeSide;old_gen=int(self.scopeGeneration);before={k:float(self.orders.get(k,{}).get('cum') or 0.0) for k in self.recoverableExpandKeys}
        # Bypass shadow behavior wrapper; CAP1/R2.2 physical/accounting remains authoritative.
        r28.FanoutRoleCapacitySim.process(self,t)
        if old_scope is not None and self.scopeSide==old_scope and int(self.scopeGeneration)==old_gen:
            refund=0.0
            for k,old in before.items():
                o=self.orders.get(k)
                if not o or int(self.key_scope_gen.get(k,-1))!=old_gen:continue
                inc=max(0.0,float(o.get('cum') or 0.0)-old)
                if inc>EPS:refund+=inc*float(o['price'])
            if refund>EPS:
                self.scopeRiskCreditConsumed=max(0.0,float(self.scopeRiskCreditConsumed)-refund);self.recoverableRiskRefunded+=refund;self.r218['RECOVERABILITY_BACKED_EXPAND_FILL']+=1
    def run_r218(self,w):
        r=super().run_shadow(w);r['r218Stats']=dict(self.r218);r['r218Events']=self.r218events[:2000];r['recoverabilityBackedExpandSubmits']=int(self.r218.get('RECOVERABILITY_BACKED_EXPAND_SUBMIT',0));r['recoverabilityBackedRiskRefunded']=float(self.recoverableRiskRefunded);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r218_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=RecoverabilityBackedSurplusSim(tape,4)
            try:c=sim.run_r218(cr['winner'])
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'MS4_R28_CAP1_CONTROL','winnerPostHocOnly':cr['winner'],**b},{'marketId':mid,'cell':'MS4_R218_RECOVERABILITY_BACKED_SURPLUS','winnerPostHocOnly':cr['winner'],**c}]
            print(json.dumps({'marketId':mid,'control':{'fills':b['fillEvents'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},'candidate':{'fills':c['fillEvents'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best'],'recSubs':c['recoverabilityBackedExpandSubmits'],'r218':c['r218Stats']},'unauth':c['unauthorizedOverflowQty'],'quotaExcess':c['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        B={r['marketId']:r for r in rows if r['cell']=='MS4_R28_CAP1_CONTROL'};C={r['marketId']:r for r in rows if r['cell']=='MS4_R218_RECOVERABILITY_BACKED_SURPLUS'};cmp=[]
        for m in mids:
            b,c=B[m],C[m];cmp.append({'marketId':m,'fillDelta':c['fillEvents']-b['fillEvents'],'fillRetention':c['fillEvents']/b['fillEvents'] if b['fillEvents'] else None,'pnlDelta':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floorDelta':c['floor']-b['floor'],'bestDelta':c['best']-b['best'],'recoverabilityBackedExpandSubmits':c['recoverabilityBackedExpandSubmits']})
        correct=all(float(C[m].get('unauthorizedOverflowQty',0))<=EPS and float(C[m].get('repairQuotaExcessMax',0))<=EPS for m in mids);anti=all(C[m]['fillEvents']>=0.5*B[m]['fillEvents'] for m in mids if B[m]['fillEvents']>0)
        out={'version':'MS4_R2_18_RECOVERABILITY_BACKED_SURPLUS_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparisonVsCap1':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':anti},'boundary':['CAP1 max4/fanout1 responsibility accounting frozen','realized monetary credit remains normal Expand authority','only when realized credit is insufficient may venue-min Expand borrow recoverability authority','recoverability requires current-generation live Repair reservations plus at most one execution-priority visible passive Repair tranche to restore the exact pre-Expand physical Floor','at most one recoverability-backed Expand live at once','forward pairSum is logged only and never hard-gated','recoverability-backed Expand fill risk is tracked separately and does not consume realized monetary credit','no Target/winner/future runtime input','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
