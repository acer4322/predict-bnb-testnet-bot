from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'
if (STAGED/'run_lane_g_multi_action_exact_fork_v1b.py').exists():
    sys.path.insert(0,str(STAGED));import run_lane_g_multi_action_exact_fork_v1b as ma
else:
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from tools import run_lane_g_multi_action_exact_fork_v1b as ma
EPS=ma.EPS

class DiagSim(ma.MultiActionExactForkSim):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.diagT=None;self.authorityExcessTimeline=[]
    def _live_diag(self):
        rows=[]
        for sid,key,o,role in self._live_role_rows():
            try:st=self._live_status(key)
            except Exception:st={}
            rows.append({'slotId':sid,'key':str(key),'side':str(o.get('side')),'role':str(role),'price':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),'scopeGen':self.key_scope_gen.get(key),'status':st.get('status'),'cum':st.get('cum'),'cancelRequested':st.get('cancelRequested'),'repairQuotaRemaining':float(self.keyRepairQuotaRemaining.get(key,0.0)),'overflowQtyRemaining':float(self.keyOverflowQtyRemaining.get(key,0.0))})
        return rows
    def _authority_diag(self,reason):
        try:reserved=float(self._reserved_current_expand_risk())
        except Exception:reserved=None
        try:service=float(self._service_claim())
        except Exception:service=None
        consumed=float(getattr(self,'scopeRiskCreditConsumed',0.0));total=float(getattr(self,'scopeRiskCreditTotal',0.0))
        committed=None if reserved is None or service is None else consumed+reserved+service
        inherited=sum(float(x.get('held',0.0))+float(x.get('spent',0.0)) for x in getattr(self,'riskTrancheMeta',{}).values() if int(x.get('generation',-1))==int(self.scopeGeneration))
        try:total_auth=float(self._risk_authority_current_generation())
        except Exception:total_auth=None
        ps=None
        if self.r303Parent:
            try:ps=self.r303Ledger.describe_parent(int(self.r303Parent['pid']))
            except Exception:pass
        return {'t':self.diagT,'reason':reason,'scopeGeneration':int(self.scopeGeneration),'scopeSide':self.scopeSide,'scopeRiskCreditTotal':total,'scopeRiskCreditConsumed':consumed,'reservedCurrentExpandRisk':reserved,'serviceClaim':service,'committed':committed,'inheritedR255Authority':inherited,'r303Authority':dict(self.r303Authority) if self.r303Authority else None,'totalRiskAuthority':total_auth,'allowance':None if total_auth is None else total+total_auth,'riskDebt':float(self._risk_debt_outstanding()),'serviceChecks':dict(self.serviceChecks),'riskTrancheOverrunMax':float(self.riskTrancheOverrunMax),'r303Parent':dict(self.r303Parent) if self.r303Parent else None,'r303ParentState':ps,'r303SharedKeys':sorted(self.r303SharedKeys),'r303SharedLive':{k:self._live_status(k) for k in sorted(self.r303SharedKeys)},'liveRows':self._live_diag(),'recentR303':self.r303Events[-8:],'recentR255':self.r255Events[-8:],'recentSplit':self.splitEvents[-8:],'recentSlotHistory':self.slot_history[-12:]}
    def _audit_r247(self):
        prev_c=float(self.serviceChecks.get('combinedAuthorityExcessMax',0.0));prev_r=float(getattr(self,'riskTrancheOverrunMax',0.0))
        super()._audit_r247()
        cur_c=float(self.serviceChecks.get('combinedAuthorityExcessMax',0.0));cur_r=float(getattr(self,'riskTrancheOverrunMax',0.0))
        if cur_c>prev_c+EPS or cur_r>prev_r+EPS:
            self.authorityExcessTimeline.append(self._authority_diag({'combinedBefore':prev_c,'combinedAfter':cur_c,'riskOverrunBefore':prev_r,'riskOverrunAfter':cur_r}))
    def process(self,t):
        self.diagT=int(t);return super().process(t)
    def _try_r263(self,t,end):
        self.diagT=int(t);return super()._try_r263(t,end)
    def _submit_role_v8(self,t,*a,**kw):
        self.diagT=int(t);return super()._submit_role_v8(t,*a,**kw)
    def _submit_active(self,t,*a,**kw):
        self.diagT=int(t);return super()._submit_active(t,*a,**kw)
    def _request_cancel(self,t,*a,**kw):
        self.diagT=int(t);return super()._request_cancel(t,*a,**kw)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    ma.FROZEN[a.market_id]=json.loads(Path(a.spec).read_text(encoding='utf-8'))['markets'][str(a.market_id)]
    td=Path(tempfile.mkdtemp(prefix='diag_r303_authority_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            cohort={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']};tape=td/f'{a.market_id}.json.xz';tape.write_bytes(z.read(f'tapes/{a.market_id}.json.xz'))
        s=DiagSim(tape,a.market_id,'R303_CONTINGENT_COMPOSITE',1,4)
        try:r=s.run_exact(cohort[a.market_id]['winner'])
        finally:s.close()
        out={'version':'LANE_G_R303_AUTHORITY_EXCESS_TIMELINE_V1_20260907','marketId':a.market_id,'correct':r['correct'],'trigger':r['trigger'],'firstStructuralEvent':r['firstStructuralEvent'],'postEventFirstManagerAction':r.get('postEventFirstManagerAction'),'terminal':r['terminal'],'r303':r['r303'],'authorityExcessTimeline':s.authorityExcessTimeline,'boundary':['diagnostic only','same frozen R303 branch','no behavior mutation','consumed only','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'marketId':a.market_id,'correct':r['correct'],'excessEvents':len(s.authorityExcessTimeline),'timeline':s.authorityExcessTimeline[:8]},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(td,ignore_errors=True)
if __name__=='__main__':main()
