from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
_local=Path(__file__).resolve().with_name('responsibility_transition.py')
trans=sibling('responsibility_transition_shadow_runtime',_local if _local.exists() else ROOT/'tools'/'eth_repair_modular'/'responsibility_transition.py')
EPS=1e-9;v38=g.v38;v80=g.v80
FIXED=[1916830,1916845,1916847,1916869,1916924,1916954,1917062,1917146,1917154,1917197,1917298,1917324,1917544,1917552,1917645,1917648]

class TransitionShadow(g.ModularAllocationLedgerV2):
    def __init__(self,*a,**kw):
        self.transitionPolicy=trans.RepairFirstResponsibilityTransitionV1();self.transitionShadow=[];self.transitionConflicts=0
        super().__init__(*a,**kw)
    def _live_repair_debt_by_side(self):
        debt={'UP':0.0,'DOWN':0.0};rows=[]
        for key,m in getattr(self,'v84Composite',{}).items():
            rem=max(0.0,float(m.get('overflowDebt') or 0)-float(m.get('overflowPaid') or 0))
            if rem<=EPS or m.get('overflowBornAt') is None:continue
            side='DOWN' if str(m.get('side')).upper()=='UP' else 'UP';debt[side]+=rem;rows.append({'key':key,'repairSide':side,'remainingDebt':rem,'bornAt':m.get('overflowBornAt')})
        return debt,rows
    def _score_state(self,t,after_kind):
        debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);side=th.get('side') if isinstance(th,dict) else None;d=self.transitionPolicy.evaluate(trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']))
        conflict=bool(after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR')
        if conflict:self.transitionConflicts+=1
        self.transitionShadow.append({'t':int(t),'afterKind':after_kind,'thesisSide':side,'repairDebtBySide':debt,'debtRows':rows,'conflict':conflict,'reason':d.reason,'liveRepairDebt':d.live_repair_debt})
        return super()._score_state(t,after_kind)
    def run_shadow(self,models,winner):
        r=self.run_allocation_v2(models,winner);r.update({'transitionShadowChecks':len(self.transitionShadow),'transitionShadowConflicts':self.transitionConflicts,'transitionShadow':self.transitionShadow[:180]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if mids!=FIXED:raise ValueError(f'fixed mismatch {mids}')
    tmp=Path(tempfile.mkdtemp(prefix='eth_transition_shadow16_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'TRANSITION_SHADOW16','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'TRANSITION_SHADOW16_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for i,mid in enumerate(mids,1):
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';s=TransitionShadow(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
            try:r=s.run_shadow(models,cr['winner'])
            finally:s.close()
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnl':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'fills':r.get('actualFillEvents'),'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'transitionChecks':r.get('transitionShadowChecks'),'transitionConflicts':r.get('transitionShadowConflicts'),'conflictRows':[x for x in r.get('transitionShadow',[]) if x.get('conflict')],'safety':g.safety(r)};rows.append(row);print(json.dumps({'idx':i,'of':len(mids),**{k:row[k] for k in ['marketId','pnl','floor','fills','truthMismatch','transitionConflicts']}},ensure_ascii=False),flush=True)
        mc=sum(int(r['transitionConflicts'] or 0)>0 for r in rows);tc=sum(int(r['transitionConflicts'] or 0) for r in rows);tm=sum(float(r['truthMismatch'] or 0)>0 for r in rows);overlap=sum((int(r['transitionConflicts'] or 0)>0) and (float(r['truthMismatch'] or 0)>0) for r in rows)
        out={'version':'ETH_REPAIR_RESPONSIBILITY_TRANSITION_SHADOW_FRESH16','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'fixedMarkets':mids,'aggregate':{'markets':len(rows),'marketsWithTransitionConflict':mc,'transitionConflictChecks':tc,'truthMismatchMarkets':tm,'conflictToTruthMismatchOverlapMarkets':overlap,'aggregatePnlDiagnostic':sum(float(r['pnl'] or 0) for r in rows)},'rows':rows,'boundary':['shadow only','no action mutation','AllocationLedger/Router/Manager frozen','winner post-hoc only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
