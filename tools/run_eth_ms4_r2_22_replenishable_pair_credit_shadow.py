from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_8_fanout_role_capacity_ablation.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r28',_STAGED);r28=importlib.util.module_from_spec(sp);sp.loader.exec_module(r28)
else:
    import tools.run_eth_ms4_r2_8_fanout_role_capacity_ablation as r28
v2=r28.v2;EPS=1e-9

class ReplenishablePairCreditShadow(r28.FanoutRoleCapacitySim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,1,max_slots);self.r222=Counter();self.lots=[];self.shadowEvents=[];self._seen=set()
    def _clean(self):
        g=int(self.scopeGeneration);self.lots=[x for x in self.lots if int(x['generation'])==g and float(x['remaining'])>EPS]
    def _consume_for_expand(self,side,pE,q):
        self._clean();rem=float(q);used=[]
        # Conservatively consume favorable lots first for EVERY actual Expand fill so they cannot later be reused.
        for x in sorted(self.lots,key=lambda z:(float(z['repairPrice']),int(z['t']))):
            if rem<=EPS:break
            if x['side']!=side or float(x['repairPrice'])+float(pE)>1.0+EPS or float(x['remaining'])<=EPS:continue
            take=min(rem,float(x['remaining']));x['remaining']-=take;rem-=take;used.append({'repairPrice':float(x['repairPrice']),'qty':take,'pairSum':float(x['repairPrice'])+float(pE)})
        return used,rem
    def _eligible_qty(self,side,pE):
        self._clean();return sum(float(x['remaining']) for x in self.lots if x['side']==side and float(x['repairPrice'])+float(pE)<=1.0+EPS)
    def process(self,t):
        before=len(self.splitEvents);super().process(t)
        for e in self.splitEvents[before:]:
            if e.get('event')!='ROLE_FILL_SPLIT':continue
            gen=int(e.get('generationAtSubmit') or -1);role=e.get('role');side=e.get('side');price=float(e.get('price') or 0);rq=float(e.get('repairAllocated') or 0);q=float(e.get('fillInc') or 0)
            if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and rq>EPS and gen==int(self.scopeGeneration):
                surplus='DOWN' if side=='UP' else 'UP';self.lots.append({'t':int(e.get('t',t)),'generation':gen,'side':surplus,'repairPrice':price,'remaining':rq});self.r222['REPAIR_LOT_BORN']+=1;self.r222['REPAIR_LOT_QTY_MILLI']+=int(round(rq*1000))
            if role=='SATELLITE_EXPAND' and q>EPS:
                used,left=self._consume_for_expand(str(side),price,q)
                if used:self.r222['ORDINARY_EXPAND_CONSUMED_REPAIR_LOT']+=1;self.r222['ORDINARY_EXPAND_CONSUMED_QTY_MILLI']+=int(round(sum(x['qty'] for x in used)*1000))
        self._clean()
    def _shadow(self,t):
        if self.scopeSide is None:return
        side=str(self.scopeSide)
        cand=self._candidate_from_levels_v8(side,'SATELLITE_EXPAND',False)
        if cand is None:return
        p,q,proj,split=cand;risk=max(0.0,float(self._physical_floor())-float(self._candidate_alone_floor(side,p,q)));credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return
        key=(int(t),int(self.scopeGeneration),side,round(float(p),8),round(float(q),8))
        if key in self._seen:return
        self._seen.add(key);self.r222['MONETARY_CREDIT_BLOCKED_OPPORTUNITY']+=1
        elig=self._eligible_qty(side,p)
        if elig+EPS>=q:
            self.r222['UNCONSUMED_FAVORABLE_REPAIR_LOT_CAN_REPLENISH']+=1
            ev={'t':int(t),'generation':int(self.scopeGeneration),'side':side,'expandPrice':float(p),'expandQty':float(q),'riskCost':risk,'availableCredit':credit,'eligibleUnconsumedRepairQty':elig,'marginalPairStructural':True};self.shadowEvents.append(ev)
        elif elig>EPS:self.r222['UNCONSUMED_PAIR_LOT_BELOW_VENUE_MIN']+=1
    def _open_one_option(self,t,qv,end):
        self._shadow(t);return super()._open_one_option(t,qv,end)
    def run_shadow(self,w):
        r=super().run_cap(w);r['r222Stats']=dict(self.r222);r['r222Events']=self.shadowEvents[:2500];r['remainingReplenishmentLots']=[x for x in self.lots if float(x['remaining'])>EPS][:200];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r222_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];sim=ReplenishablePairCreditShadow(tmp/'tapes'/f'{mid}.json.xz',4)
            try:r=sim.run_shadow(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'winnerPostHocOnly':cr['winner'],**r});print(json.dumps({'marketId':mid,'fills':r['fillEvents'],'pnl':r['pnlDiagnosticOnly'],'floor':r['floor'],'shadow':r['r222Stats']},ensure_ascii=False),flush=True)
        agg=Counter();
        for r in rows:agg.update(r.get('r222Stats') or {})
        out={'version':'MS4_R2_22_REPLENISHABLE_PAIR_CREDIT_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'aggregate':dict(agg),'rows':rows,'boundary':['CAP1 behavior exactly unchanged','actual Repair allocations create price/qty replenishment lots only in shadow','every actual ordinary Expand fill conservatively consumes eligible favorable Repair lots before any later shadow eligibility, preventing repair-lot reuse','shadow replenishment requires remaining unconsumed actual Repair qty >= full venue-min Expand qty and each consumed unit structurally satisfies repairPrice+expandPrice<=1','general monetary-credit blocks are observed, not changed','no winner/Target/future runtime input','realistic HFT','no dream fill','no 8781']};op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
