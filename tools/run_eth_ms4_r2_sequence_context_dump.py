from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
import joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_1_fillability_active_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r21',_STAGED);r21=importlib.util.module_from_spec(sp);sp.loader.exec_module(r21)
else:
    import tools.run_eth_ms4_r2_1_fillability_active_repair as r21
r1=r21.r1;EPS=1e-9
REPAIR={'ECONOMIC_CORE','SATELLITE_REPAIR'}

def _last_time(hist, pred):
    for e in reversed(hist):
        if pred(e): return int(e.get('t',0))
    return None

def _count(hist,pred): return sum(1 for e in hist if pred(e))

class ContextEnumerator(r21.FillabilityActiveRepairSim):
    def __init__(self,tape,model):
        super().__init__(tape,model,4);self.rows=[];self.nsat=0
        self._end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        pure=role=='SATELLITE_REPAIR' and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q)
        if pure:
            score,diag=self._score(side,role,p,q,split)
            if score<0.5:
                self.nsat+=1;qv=r1.v2.base.quotes(self.book);ask=float(qv[side]['ask']) if qv and qv.get(side,{}).get('ask') is not None else None
                opp='DOWN' if side=='UP' else 'UP';oa=self.unmatched_avg(opp);gen=int(self.scopeGeneration);hist=self.slot_history
                scope_t=_last_time(hist,lambda e:int(e.get('generation',-999))==gen and e.get('event') in {'RESPONSIBILITY_SCOPE_BIRTH','RESPONSIBILITY_SCOPE_FLIP'})
                repair_credit_t=_last_time(hist,lambda e:int(e.get('generation',-999))==gen and e.get('event')=='CONFIRMED_REPAIR_ALLOCATED_CREDIT')
                any_fill_t=_last_time(hist,lambda e:e.get('event')=='ROLE_FILL_SPLIT')
                scope_rep_sub=_count(hist,lambda e:e.get('event')=='ROLE_SLOT_SUBMIT' and int(e.get('scopeGeneration',-999))==gen and e.get('role') in REPAIR)
                scope_rep_fill=_count(hist,lambda e:e.get('event')=='ROLE_FILL_SPLIT' and int(e.get('generationAtSubmit',-999))==gen and e.get('role') in REPAIR)
                scope_exp_sub=_count(hist,lambda e:e.get('event')=='ROLE_SLOT_SUBMIT' and int(e.get('scopeGeneration',-999))==gen and e.get('role')=='SATELLITE_EXPAND')
                scope_exp_fill=_count(hist,lambda e:e.get('event')=='ROLE_FILL_SPLIT' and int(e.get('generationAtSubmit',-999))==gen and e.get('role')=='SATELLITE_EXPAND')
                canceled_rep=_count(hist,lambda e:e.get('event')=='SLOT_RELEASE' and str(e.get('status'))=='CANCELED' and self.key_role.get(e.get('key')) in REPAIR and self.key_scope_gen.get(e.get('key'))==gen)
                self.rows.append({'occurrence':self.nsat,'t':int(t),'side':side,'role':role,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'bestPrice':diag.get('bestPrice'),'activeAsk':ask,'activePremium':(ask-float(p)) if ask is not None else None,'passivePairSum':(float(oa)+float(p)) if oa is not None else None,'activePairSum':(float(oa)+ask) if oa is not None and ask is not None else None,'floorBefore':float(self._physical_floor()),'debtQty':float(self._scope_debt_qty()),'repairDebtFraction':float(q)/max(float(self._scope_debt_qty()),EPS),'scopeGeneration':gen,'scopeRepairProgressClocks':int(self.scopeRepairProgressClocks),'totalRepairProgressClocks':int(self.totalRepairProgressClocks),'availableRiskCredit':float(self._available_expand_risk_credit()),'liveSlots':int(len(self.slot_key)),'scopeAgeMs':int(t)-scope_t if scope_t is not None else None,'sinceRepairCreditMs':int(t)-repair_credit_t if repair_credit_t is not None else None,'sinceAnyFillMs':int(t)-any_fill_t if any_fill_t is not None else None,'scopeRepairSubmitCount':scope_rep_sub,'scopeRepairFillCount':scope_rep_fill,'scopeExpandSubmitCount':scope_exp_sub,'scopeExpandFillCount':scope_exp_fill,'scopeCanceledRepairCount':canceled_rep,'secondsLeft':max(0.0,(self._end-int(t))/1000.0)})
        return r1.QueueAwareRepairRoutingSim._submit_role_v8(self,t,side,role,p,q,proj,split)
    def run_ctx(self,w):
        r=r1.QueueAwareRepairRoutingSim.run_v88(self,w);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='ms4_r2_ctx_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};outrows=[]
        for mid in mids:
            s=ContextEnumerator(tmp/'tapes'/f'{mid}.json.xz',model)
            try:r=s.run_ctx(co[mid]['winner'])
            finally:s.close()
            for x in s.rows: outrows.append({'marketId':mid,**x})
            print(json.dumps({'marketId':mid,'eligible':len(s.rows),'fills':r['fillEvents'],'submits':r['submits']},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_SEQUENCE_CONTEXT_DUMP_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':outrows,'boundary':['frozen MS4-R1 behavior','instrumentation only','strictly-past sequence context','no strategy change','no Target runtime input']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(outrows)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
