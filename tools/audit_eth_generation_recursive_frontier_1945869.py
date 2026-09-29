from __future__ import annotations
import argparse,json,math,os,shutil,sys,tempfile,zipfile
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
EPS=1e-9;GRID=.01
import importlib.util
def _sib(name, fn):
    q=Path(__file__).resolve().with_name(fn); sp=importlib.util.spec_from_file_location(name,q); m=importlib.util.module_from_spec(sp); sys.modules[name]=m; sp.loader.exec_module(m); return m
try:
    import tools.run_eth_generation_debt_x_active_rearm_2x2_1945869 as g2
except ImportError:
    g2=_sib('g2frontier','run_eth_generation_debt_x_active_rearm_2x2_1945869.py')
try:
    from tools.eth_repair_modular.recoverability import RecursiveCompositeCurrentCoordinateRecoverabilityPolicy,RecursiveCompositeRecoverabilityContext
except ImportError:
    _rc=_sib('recoverability_frontier','recoverability.py'); RecursiveCompositeCurrentCoordinateRecoverabilityPolicy=_rc.RecursiveCompositeCurrentCoordinateRecoverabilityPolicy; RecursiveCompositeRecoverabilityContext=_rc.RecursiveCompositeRecoverabilityContext
pe=g2.pe

class FrontierAudit(g2.D_Both):
    def __init__(self,*a,**kw):
        self.frontierSubmitRows=[];self.frontierLiveRows=[];self._frontierLast={};self.recursiveFrontierPolicy=RecursiveCompositeCurrentCoordinateRecoverabilityPolicy();super().__init__(*a,**kw)
    def _qv(self):
        try:return g2.prev.fcr.basev1.quotes(self.book)
        except Exception:return None
    def submit(self,t,side,p,q):
        role=str(getattr(self,'_pendingAuthorizedRole',None) or '').upper();pid=getattr(self,'_pendingParentId',None);qv=self._qv()
        if role=='REPAIR' and pid is not None and qv and side in qv:
            bb=qv[side].get('bid');aa=qv[side].get('ask');st=getattr(self,'generationEpochByParent',{}).get(int(pid));ep=int(getattr(st,'epoch',0)) if st is not None else 0
            debt=float(self._manager_debt_for_parent(int(pid),float(self._current_payoffs().get('gap') or 0.0))) if hasattr(self,'_manager_debt_for_parent') else None
            self.frontierSubmitRows.append({'t':int(t),'parentId':int(pid),'epoch':ep,'side':side,'orderPrice':float(p),'qty':float(q),'liveBid':float(bb) if bb is not None else None,'liveAsk':float(aa) if aa is not None else None,'behindBestBidTicks':((float(bb)-float(p))/GRID if bb is not None else None),'managerDebt':debt,'floor':float(self._current_payoffs().get('floor') or 0.0)})
        return super().submit(t,side,p,q)
    def _capture_live(self,t):
        qv=self._qv()
        if not qv:return
        for key,e in getattr(self,'carrierLedger',{}).items():
            if str(e.get('objectiveRole') or '').upper()!='REPAIR':continue
            pid=e.get('parentId')
            if pid is None:continue
            st=getattr(self,'generationEpochByParent',{}).get(int(pid));ep=int(getattr(st,'epoch',0)) if st is not None else 0
            if ep<2:continue
            o=getattr(self,'orders',{}).get(key,{})
            try:live=bool(o and g2.prev.fcr.basev1.live(self.snap(o).get('status')))
            except Exception:live=False
            if not live:continue
            side=str(e.get('side') or o.get('side') or '')
            if side not in ('UP','DOWN') or side not in qv:continue
            p=float(o.get('price') or e.get('price') or 0.0);bb=qv[side].get('bid');aa=qv[side].get('ask');sig=(round(float(bb or 0),6),round(float(aa or 0),6),round(p,6))
            if self._frontierLast.get(str(key))==sig:continue
            self._frontierLast[str(key)]=sig
            debt=float(self._manager_debt_for_parent(int(pid),float(self._current_payoffs().get('gap') or 0.0))) if hasattr(self,'_manager_debt_for_parent') else None
            self.frontierLiveRows.append({'t':int(t),'key':str(key),'parentId':int(pid),'epoch':ep,'side':side,'orderPrice':p,'liveBid':float(bb) if bb is not None else None,'liveAsk':float(aa) if aa is not None else None,'behindBestBidTicks':((float(bb)-p)/GRID if bb is not None else None),'managerDebt':debt,'floor':float(self._current_payoffs().get('floor') or 0.0)})
    def process(self,t):
        out=super().process(t);self._capture_live(int(t));return out
    def cancel_expired(self,t):
        self._capture_live(int(t));return super().cancel_expired(t)
    def run_frontier(self,models,winner):
        r=super().run_first_carrier_relay(models,winner);r.update({'frontierSubmitRows':self.frontierSubmitRows,'frontierLiveRows':self.frontierLiveRows[:1200]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id);outp=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output));tmp=Path(tempfile.mkdtemp(prefix='frontier_audit_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid];models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        s=pe.make(FrontierAudit,tape,models,life,cap,tim,econ,price,sur,t44,t47)
        try:
            r=s.run_frontier(models,cr['winner']);cons,bound,parents=pe.alloc(s,r);ss=pe.safety(r)
            carrier_snapshot={str(k):dict(v) for k,v in getattr(s,'carrierLedger',{}).items()}
        finally:s.close()
        sub=list(r.get('frontierSubmitRows') or []);live=list(r.get('frontierLiveRows') or [])
        post=[x for x in sub if int(x.get('epoch') or 0)>=2]
        behind=[float(x['behindBestBidTicks']) for x in post if x.get('behindBestBidTicks') is not None]
        active_reasons={}
        for e in r.get('reservationAwareEvents') or []:
            if e.get('event')=='RESERVATION_AWARE_SAME_PARENT_EVALUATION':active_reasons[str(e.get('reason'))]=active_reasons.get(str(e.get('reason')),0)+1
        out={'version':'ETH_GENERATION_RECURSIVE_FRONTIER_AUDIT_1945869_V1','date':'2026-09-05','researchOnly':True,'behaviorChange':False,'marketId':mid,'winnerPostHocOnly':cr['winner'],'summary':{'fills':r.get('actualFillEvents'),'pnlDiagnosticOnly':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'postGenerationRepairSubmits':len(post),'postGenerationRepairFilledQty':sum(float(e.get('actualFilled') or 0.0) for e in carrier_snapshot.values() if str(e.get('objectiveRole') or '').upper()=='REPAIR' and int(e.get('parentId') or -1)==1)-1.6666666666666667,'behindBestBidTicks':{'min':min(behind) if behind else None,'median':sorted(behind)[len(behind)//2] if behind else None,'max':max(behind) if behind else None},'activeReasonCounts':active_reasons},'submitRows':sub,'liveRows':live,'allocationParents':parents,'safety':ss,'allocationConservation':bool(cons),'allocationBounded':bool(bound),'boundary':['behavior-inert audit of D_BOTH generation-debt+active-rearm candidate','records current best bid/ask versus post-generation passive Repair carrier price','does not reprice, cancel, submit or relax economic ceiling','realistic HFT only; winner post-hoc only; no dream fill; no 8781']}
        outp.parent.mkdir(parents=True,exist_ok=True);outp.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'submitRows':sub,'safety':ss,'allocationConservation':cons,'allocationBounded':bound},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
