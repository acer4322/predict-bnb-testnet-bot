from __future__ import annotations
import argparse, json
from pathlib import Path
from collections import Counter, defaultdict


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--recurrence', required=True)
    ap.add_argument('--out', required=True)
    args=ap.parse_args()
    r=load(args.recurrence)

    ctx=r.get('contextClassCounts',{})
    improve=r.get('robustImprovingContexts',[])
    worsen=r.get('robustWorseningContexts',[])
    reversals=r.get('directionReversals',[])

    caps={
      'REPAIR_DEMAND': {'robustImprovement':0,'robustWorsening':0,'reversal':0,'evidence':[]},
      'STATE_SHAPING_OPPORTUNITY': {'robustImprovement':0,'robustWorsening':0,'reversal':0,'evidence':[]},
      'ROLE_MAKER_TAKER': {'robustImprovement':0,'robustWorsening':0,'reversal':0,'evidence':[]},
      'TIMING_HAZARD': {'robustImprovement':0,'robustWorsening':0,'reversal':0,'evidence':[]},
    }
    def cap_for(x):
        h=x.get('head')
        lab=x.get('target_label')
        if h=='PURPOSE' and lab=='REPAIR': return 'REPAIR_DEMAND'
        if h=='PURPOSE' and lab=='ADD': return 'STATE_SHAPING_OPPORTUNITY'
        if h=='ROLE': return 'ROLE_MAKER_TAKER'
        if h in ('ANY_ACTION_1S','TAKER_ACTION_1S','ADD_ACTION_1S'): return 'TIMING_HAZARD'
        return None
    for cls,rows in [('robustImprovement',improve),('robustWorsening',worsen),('reversal',reversals)]:
        for x in rows:
            c=cap_for(x)
            if not c: continue
            caps[c][cls]+=1
            caps[c]['evidence'].append({
              'class':cls,'head':x.get('head'),'phase':x.get('phase'),'targetLabel':x.get('target_label'),
              'floorState':x.get('floor_state'),'ownershipContext':x.get('ownership_context'),
              'meanDelta4':x.get('meanDelta4'),'worstDelta4':x.get('worstDelta4')
            })

    for name,c in caps.items():
        if c['reversal']>=2:
            c['status']='REGIME_OR_CONTEXT_DEPENDENT'
            c['supervisorAction']='DO_NOT_APPLY_GLOBAL_DIRECTIONAL_UPDATE; REQUIRE_CONTEXT_SPECIALIZATION_OR_ARBITRATION'
        elif c['robustWorsening']>c['robustImprovement'] and c['robustWorsening']>0:
            c['status']='CAPABILITY_DEFICIT'
            c['supervisorAction']='BLOCK_GLOBAL_PROMOTION_THAT_DEGRADES_THIS_CAPABILITY; SPAWN_SPECIALIST_OR_INDEPENDENT_OBJECTIVE'
        elif c['robustImprovement']>0 and c['robustWorsening']==0:
            c['status']='REPEATED_KEEP_SIGNAL'
            c['supervisorAction']='PRESERVE_WITH_ANTI_FORGETTING_GUARD'
        elif sum(c[k] for k in ['robustImprovement','robustWorsening','reversal'])==0:
            c['status']='INSUFFICIENT_CROSS_STREAM_EVIDENCE'
            c['supervisorAction']='KEEP_FROZEN_AND_COLLECT_MORE_FRESH_EPISODES'
        else:
            c['status']='MIXED'
            c['supervisorAction']='NO_GLOBAL_PROMOTION; COLLECT_MORE_CONTEXTUAL_EVIDENCE'

    # Explicitly detect the observed binary-purpose tradeoff.
    purpose_tradeoff=(caps['REPAIR_DEMAND']['robustImprovement']>0 and caps['STATE_SHAPING_OPPORTUNITY']['robustWorsening']>0)
    recommendations=[]
    if purpose_tradeoff:
        recommendations.append({
          'priority':1,
          'action':'DECOMPOSE_BINARY_PURPOSE',
          'detail':'Replace global REPAIR-vs-ADD competition with independent Repair Demand and State-Shaping Opportunity evidence, then arbitrate with Objective Ledger/lifecycle/floor/phase.'
        })
    if caps['ROLE_MAKER_TAKER']['status']=='REGIME_OR_CONTEXT_DEPENDENT':
        recommendations.append({
          'priority':2,
          'action':'CONTEXTUALIZE_ROLE',
          'detail':'Do not push Maker/Taker globally. Condition or specialize by phase, floor, ownership, objective state, and regime evidence.'
        })
    recommendations.append({
      'priority':3,
      'action':'KEEP_TARGET_AUXILIARY',
      'detail':'Use Target only as post-episode teacher/reference. Champion promotion is decided by our capability registry, safety, lifecycle/execution invariants, and fresh realistic-HFT evaluation.'
    })

    out={
      'version':'R4_ADAPTIVE_CYCLE_SUPERVISOR_STATE_V1',
      'researchOnly':True,'actionAuthority':False,
      'sourceRecurrence':str(args.recurrence),
      'objective':'OUR_OWN_TARGET_INSPIRED_ADAPTIVE_SYSTEM',
      'targetRole':'AUXILIARY_POST_EPISODE_TEACHER_NOT_FINAL_IMITATION_OBJECTIVE',
      'crossStreamContextCounts':ctx,
      'capabilityRegistry':caps,
      'binaryPurposeTradeoffDetected':purpose_tradeoff,
      'recommendations':sorted(recommendations,key=lambda z:z['priority']),
      'promotionPolicy':[
        'Challenger only; never mutate active Champion during training.',
        'Behavior ledger required after every accepted update batch.',
        'Repeated cross-stream worsening blocks global promotion for the affected capability.',
        'Stream-direction reversal is evidence for context/regime dependence, not evidence to tune a global bias.',
        'Formal market-disjoint realistic-HFT validation required before any action-authority discussion.'
      ]
    }
    p=Path(args.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(p),'binaryPurposeTradeoffDetected':purpose_tradeoff,'capabilityStatus':{k:v['status'] for k,v in caps.items()},'recommendations':recommendations},indent=2))

if __name__=='__main__': main()
