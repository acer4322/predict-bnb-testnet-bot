from __future__ import annotations
import json, argparse
from pathlib import Path
EPS=1e-9

def apply_buy(worst,best,p,q):
    # repair side is currently the weak outcome: buying q raises weak payoff by (1-p)q
    # and lowers the opposite/best payoff by p*q.
    a=worst+(1.0-p)*q
    b=best-p*q
    return a,b,min(a,b),max(a,b)

def row_for_snap(market,snap,carrier_price=None,carrier_qty=0.0):
    w=float(snap['floor']); b=float(snap['bestPayoff']); p=float(snap['repairAsk'])
    gap=max(0.0,b-w)
    legal=1.0/p if p>EPS else float('inf')
    deficit=max(0.0,-w)
    weak_only_need=deficit/(1.0-p) if p<1.0-EPS else float('inf')
    q_min=min(legal,gap) if gap>EPS else 0.0
    q_def=min(weak_only_need,gap) if gap>EPS else 0.0
    q_equal=gap
    a1,b1,f1,u1=apply_buy(w,b,p,q_min)
    ad,bd,fd,ud=apply_buy(w,b,p,q_def)
    ae,be,fe,ue=apply_buy(w,b,p,q_equal)
    nonloss_lo=weak_only_need
    nonloss_hi=b/p if p>EPS else float('inf')
    nonloss_feasible=nonloss_lo<=nonloss_hi+1e-12
    passive_after_min=None
    if carrier_price is not None and 0<carrier_price<1 and carrier_qty>0:
        # Active fill atomically consumes payoff responsibility. Retained passive capacity
        # is clipped to the remaining weak-side deficit and never allowed past remaining gap.
        remaining_def=max(0.0,-a1)
        remaining_gap=max(0.0,b1-a1)
        passive_need=remaining_def/(1.0-carrier_price) if remaining_def>EPS else 0.0
        passive_keep=min(carrier_qty,remaining_gap,passive_need)
        pa,pb,pf,pu=apply_buy(a1,b1,carrier_price,passive_keep)
        passive_after_min={
            'originalPassiveQty':carrier_qty,'passivePrice':carrier_price,
            'keepQtyAfterActiveMinSlice':passive_keep,
            'resizeQty':max(0.0,carrier_qty-passive_keep),
            'postActiveThenPassiveWorst':pf,'postActiveThenPassiveBest':pu,
            'postActiveThenPassiveWeakOutcome':pa,'postActiveThenPassiveOtherOutcome':pb
        }
    return {
        'marketId':market,'offsetMs':snap['offsetMs'],'t':snap['t'],'repairAsk':p,
        'startingWorst':w,'startingBest':b,'startingGap':gap,'deficit':deficit,
        'venueLegalMinQty':legal,'weakOnlyDeficitQtyRaw':weak_only_need,
        'nonLossIntervalQty':[nonloss_lo,nonloss_hi],'nonLossFeasibleAtAsk':nonloss_feasible,
        'MIN_SLICE':{'qty':q_min,'weakOutcome':a1,'otherOutcome':b1,'worst':f1,'best':u1},
        'DEFICIT_BOUNDED_CLIPPED_TO_GAP':{'qty':q_def,'weakOutcome':ad,'otherOutcome':bd,'worst':fd,'best':ud},
        'FLOOR_MAXIMIZING_EQUALIZE':{'qty':q_equal,'weakOutcome':ae,'otherOutcome':be,'worst':fe,'best':ue},
        'sharedBudgetAfterMinSlice':passive_after_min
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--failure',required=True);ap.add_argument('--control',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    fail=json.load(open(a.failure,encoding='utf-8'))['rows'][0]['episodes'][0]
    ctrl=json.load(open(a.control,encoding='utf-8'))['rows'][0]['episodes'][0]
    # V32 confirmation point is t+3s; also retain later points to measure rescue-price decay.
    frows=[]
    for s in fail['snaps']:
        if int(s['offsetMs']) in (3000,5000,10000):
            frows.append(row_for_snap(1840896,s,carrier_price=float(s.get('repairCarrierBestPrice') or fail['repairCeiling']),carrier_qty=float(s.get('repairCarrierQty') or 0)))
    crows=[]
    for s in ctrl['snaps']:
        if int(s['offsetMs']) in (3000,5000):
            crows.append(row_for_snap(1842999,s,carrier_price=float(s.get('repairCarrierBestPrice') or ctrl['repairCeiling']),carrier_qty=float(s.get('repairCarrierQty') or 0)))
    f3=next(x for x in frows if x['offsetMs']==3000); c3=next(x for x in crows if x['offsetMs']==3000)
    out={
      'version':'ETH_REPAIR_V33_BOUNDED_ACTIVE_PAYOFF_GEOMETRY_V1','researchOnly':True,
      'behaviorChange':False,
      'failureCase1840896':frows,'nonStalledControl1842999':crows,
      'findings':{
        'deficitBoundedSemanticIssue':'Weak-side deficit/(1-p) is not a valid full-repair quantity once it exceeds the current payoff gap; crossing balance can make the other outcome become the new negative floor.',
        'failureAt3sNonLossFeasible':f3['nonLossFeasibleAtAsk'],
        'failureAt3sBestPossibleWorst':f3['FLOOR_MAXIMIZING_EQUALIZE']['worst'],
        'failureAt3sMinSliceWorst':f3['MIN_SLICE']['worst'],
        'controlAt3sWouldBeUnnecessary':True,
        'controlReason':'1842999 is not V32-stalled and naturally reaches payoff recovery ~2s later; any Taker at t+3s spends directional surplus unnecessarily.'
      },
      'decision':'REJECT_NAIVE_DEFICIT_BOUNDED_AS_V33_SIZING; KEEP_MIN_SLICE_FOR_FIRST_ACTIVE_SMOKE; ADD_EXACT_TWO_OUTCOME_PAYOFF_BOUND / FLOOR_MAXIMIZING_DIAGNOSTIC.',
      'next':'Implement V33 active child only on V32-stalled state. First smoke uses MIN_SLICE and atomically resizes passive reservation by shared payoff responsibility. In parallel audit trigger latency because rescue floor deteriorates as ask escapes after stall.',
      'boundary':['strict-past consumed HFT snapshots only','no winner/PnL selection','no dream fill','no 8781','no behavior change in this audit','binary payoff transform accounts for both outcomes, not absNet balancing']
    }
    p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'failure3s':f3,'control3s':c3,'decision':out['decision']},ensure_ascii=False))
if __name__=='__main__':main()
