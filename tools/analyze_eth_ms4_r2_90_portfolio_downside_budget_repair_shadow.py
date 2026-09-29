from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_64_execution_represented_pre_repair_reexpand as r264
EPS=1e-9
BUDGET=1.0

class PortfolioBudgetAudit(r264.ExecutionRepresentedPreRepairReexpandSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots);self.r290=[]
    def process(self,t):
        floor0=float(self._physical_floor());p0=len(self.r257Events);s0=len(self.splitEvents)
        super().process(t)
        floor1=float(self._physical_floor())
        pays=[x for x in self.r257Events[p0:] if x.get('event')=='R257_RISK_REPAIR_OBLIGATION_PAYMENT' and float(x.get('paid') or 0.0)>EPS]
        if not pays:return
        splits=[x for x in self.splitEvents[s0:] if x.get('event')=='ROLE_FILL_SPLIT' and float(x.get('fillInc') or 0.0)>EPS]
        lookup={(int(x.get('t') or t),str(x.get('key'))):x for x in splits}
        total=passive=active=0.0;details=[]
        paykeys=set()
        for e in pays:
            q=float(e.get('paid') or 0.0);total+=q;paykeys.add(str(e.get('key')))
            if bool(e.get('active')):active+=q
            else:passive+=q
            z=lookup.get((int(e.get('t') or t),str(e.get('key'))),{})
            details.append({'key':str(e.get('key')),'role':e.get('role'),'active':bool(e.get('active')),'paid':q,'price':float(z.get('price') or 0.0),'repairAllocated':float(z.get('repairAllocated') or 0.0)})
        other=0.0;overflow=0.0
        for x in splits:
            q=float(x.get('fillInc') or 0.0);rq=float(x.get('repairAllocated') or 0.0);of=float(x.get('overflowRealized') or 0.0)
            if str(x.get('key')) not in paykeys:other+=q
            else:other+=max(0.0,q-rq-of);overflow+=of
        pure=(other<=EPS and overflow<=EPS)
        improve=floor1-floor0
        excess=0.0;needed=total
        if pure and improve>EPS:
            if floor0>=-BUDGET-EPS:
                excess=total;needed=0.0
            elif floor1>-BUDGET+EPS:
                frac=max(0.0,min(1.0,(-BUDGET-floor0)/improve));needed=total*frac;excess=total-needed
        self.r290.append({'t':int(t),'floorBefore':floor0,'floorAfter':floor1,'floorImprove':improve,'totalPaid':total,'passivePaid':passive,'activePaid':active,'pureRepairReceipt':pure,'otherFillQty':other,'overflowQty':overflow,'estimatedQtyNeededToMinus1':needed,'estimatedQtyBeyondMinus1':excess,'scopeSide':self.scopeSide,'generation':int(self.scopeGeneration),'details':details})
    def run_audit(self,w):
        r=super().run_r264(w);r['r290Events']=self.r290;return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r290_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[]
        for m in mids:
            s=PortfolioBudgetAudit(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_audit(co[m]['winner'])
            finally:s.close()
            ev=r['r290Events'];pure=[x for x in ev if x['pureRepairReceipt']];beyond=sum(x['estimatedQtyBeyondMinus1'] for x in pure);tot=sum(x['totalPaid'] for x in pure)
            already=sum(x['totalPaid'] for x in pure if x['floorBefore']>=-BUDGET-EPS)
            x={'marketId':m,'winnerPostHocOnly':co[m]['winner'],'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),'fills':int(r['fillEvents']),'repairPaymentReceipts':len(ev),'pureRepairReceipts':len(pure),'pureRepairPaidQty':tot,'estimatedRepairQtyBeyondMinus1':beyond,'repairQtyPaidWhileAlreadyWithinMinus1':already,'beyondFraction':beyond/tot if tot>EPS else 0.0,'activePaidQty':sum(z['activePaid'] for z in ev),'passivePaidQty':sum(z['passivePaid'] for z in ev),'correct':bool(r.get('r264CorrectnessPass')),'events':ev}
            rows.append(x);print(json.dumps({k:x[k] for k in x if k!='events'},ensure_ascii=False),flush=True)
        pureqty=sum(x['pureRepairPaidQty'] for x in rows);beyond=sum(x['estimatedRepairQtyBeyondMinus1'] for x in rows)
        summary={'markets':len(rows),'repairPaymentReceipts':sum(x['repairPaymentReceipts'] for x in rows),'pureRepairPaidQty':pureqty,'estimatedRepairQtyBeyondMinus1':beyond,'beyondFraction':beyond/pureqty if pureqty>EPS else 0.0,'repairQtyPaidWhileAlreadyWithinMinus1':sum(x['repairQtyPaidWhileAlreadyWithinMinus1'] for x in rows),'activePaidQty':sum(x['activePaidQty'] for x in rows),'passivePaidQty':sum(x['passivePaidQty'] for x in rows),'marketsWithBeyondMinus1':sum(x['estimatedRepairQtyBeyondMinus1']>EPS for x in rows),'correctnessPass':all(x['correct'] for x in rows)}
        out={'version':'MS4_R2_90_PORTFOLIO_DOWNSIDE_BUDGET_REPAIR_SHADOW_V1','researchOnly':True,'behaviorChange':False,'budgetInterpretation':'one existing venue-min risk unit diagnostic; not a promoted runtime threshold','budgetFloor':-BUDGET,'summary':summary,'rows':rows,'boundary':['exact R264 behavior','actual R257 payments only','Passive and Active reported separately','-1 is diagnostic from one existing risk unit and user payoff target, not runtime gate','counterfactual excess estimate only on pure Repair receipts','no winner used in attribution','no behavior change']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
