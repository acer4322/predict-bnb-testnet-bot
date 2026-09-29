from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.responsibility_generation_epoch import ResponsibilityGenerationEpochState

EPS=1e-9

def executable(parent_debt, attached_debt, ask, ceiling, floor_before, attached_floor_after):
    legal=1.0/ask
    if parent_debt + EPS < legal or ask > ceiling + EPS:
        return False, None
    extra=max(0.0,parent_debt-attached_debt)
    conservative=attached_floor_after-extra*ask
    return conservative + EPS >= floor_before, conservative

def main():
    s=ResponsibilityGenerationEpochState(parent_id=1,parent_side='DOWN')
    s.attach_existing_parent_responsibility(added_debt=1.6666666666666667,parent_fill_now=4.5016702956573456,churn_now=0,paid_total_now=0.0)
    parent_debt=2.1649963710093214; ceiling=0.6124560577773237; floor=-0.8390312285187402; attached_after=0.04430210481459351
    cases=[]
    for ask,expect in [(0.47,True),(0.45,False),(0.62,False)]:
        ok,lb=executable(parent_debt,s.attached_debt,ask,ceiling,floor,attached_after)
        cases.append({'ask':ask,'ok':ok,'expected':expect,'lowerBound':lb})
    obs=s.observe(parent_fill_now=4.5016702956573456,churn_now=0,paid_total_now=0.0)
    invariants=(s.parent_id==1 and s.attached_debt<parent_debt and obs['epoch']==1 and not obs['paymentProgress'])
    passed=all(x['ok']==x['expected'] for x in cases) and invariants
    print({'passed':passed,'cases':cases,'invariants':invariants,'obs':obs})
    raise SystemExit(0 if passed else 1)
if __name__=='__main__': main()
