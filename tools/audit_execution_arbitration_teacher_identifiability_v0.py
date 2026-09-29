from __future__ import annotations
import json, math
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
TEACHER=D/'sequential_arbitration_option_teacher_v1.jsonl'
CURR=D/'pair_completion_sequential_curriculum_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
OUT=D/'execution_arbitration_teacher_identifiability_v0_report.json'
ACT={'KEEP_EXECUTING','REPLACE_ROUTE'}
DOM={'KEEP_DOMINATES','REPLACE_DOMINATES'}

def entropy_binary(pos:int,n:int)->float:
    if n<=0:return 0.0
    p=pos/n
    if p<=0 or p>=1:return 0.0
    return -(p*math.log2(p)+(1-p)*math.log2(1-p))

def cdict(c): return {str(k):int(v) for k,v in sorted(c.items(),key=lambda kv:str(kv[0]))}

def main()->int:
    teacher=[json.loads(x) for x in TEACHER.read_text(encoding='utf-8').splitlines() if x.strip()]
    curr=[json.loads(x) for x in CURR.read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
    splits={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
    by=defaultdict(list)
    for r in teacher: by[int(r['marketId'])].append(r)
    transitions=Counter(); adjacent=0; flips=0; wait_to_act=0; same=0
    for mid,rs in by.items():
        rs=sorted(rs,key=lambda r:int(r['delayMs']))
        for a,b in zip(rs,rs[1:]):
            aa=str(a['teacherAction']); bb=str(b['teacherAction']); transitions[f'{aa}->{bb}']+=1; adjacent+=1
            if aa!=bb: flips+=1
            else: same+=1
            if aa=='WAIT_FOR_CLARITY' and bb in ACT: wait_to_act+=1
    delay_stats={}
    for d in sorted({int(r['delayMs']) for r in teacher}):
        z=[r for r in teacher if int(r['delayMs'])==d]; pos=sum(str(r['teacherAction']) in ACT for r in z)
        delay_stats[str(d)]={'n':len(z),'act':pos,'actRate':pos/len(z),'binaryEntropyBits':entropy_binary(pos,len(z)),'actions':cdict(Counter(str(r['teacherAction']) for r in z))}
    phase_stats={}
    for key,fn in {
        'workingChild':lambda r:str(int(float((r.get('features') or {}).get('workingRecoveryExists') or 0))),
        'hasPriorObservation':lambda r:str(int(float((r.get('features') or {}).get('hasPriorObservation') or 0))),
        'sequenceStep':lambda r:str(r.get('sequenceStep')),
    }.items():
        grp=defaultdict(list)
        for r in teacher: grp[fn(r)].append(r)
        phase_stats[key]={}
        for g,z in sorted(grp.items()):
            pos=sum(str(r['teacherAction']) in ACT for r in z)
            phase_stats[key][g]={'n':len(z),'actRate':pos/len(z),'binaryEntropyBits':entropy_binary(pos,len(z)),'actions':cdict(Counter(str(r['teacherAction']) for r in z))}
    step0=[r for r in teacher if int(r['sequenceStep'])==0]
    step0_nondom=[r for r in step0 if str(r.get('currentParetoLabel')) not in DOM]
    step0_future_defined=sum((str(r['teacherAction'])=='WAIT_FOR_CLARITY' and r.get('firstFutureDominance') is not None) or (str(r['teacherAction'])=='RETURN_TO_CONTROLLER' and r.get('firstFutureDominance') is None) for r in step0_nondom)
    wait_rows=[r for r in teacher if str(r['teacherAction'])=='WAIT_FOR_CLARITY']
    return_rows=[r for r in teacher if str(r['teacherAction'])=='RETURN_TO_CONTROLLER']
    split_stats={}
    for name,ids in splits.items():
        z=[r for r in teacher if int(r['marketId']) in ids]; path=Counter(); first_delay=Counter()
        for mid in ids:
            rs=sorted(by.get(mid,[]),key=lambda r:int(r['delayMs']))
            if not rs: continue
            if str(rs[0]['teacherAction'])=='RETURN_TO_CONTROLLER': path['RETURN']+=1
            elif str(rs[0]['teacherAction']) in ACT: path['IMMEDIATE_ACT']+=1
            else: path['DELAYED_ACT']+=1
            fd=rs[0].get('firstFutureDominanceDelayMs'); first_delay[str(fd)]+=1
        pos=sum(str(r['teacherAction']) in ACT for r in z)
        split_stats[name]={'markets':len(ids),'rows':len(z),'actRate':pos/len(z) if z else None,'binaryEntropyBits':entropy_binary(pos,len(z)) if z else None,'pathCounts':cdict(path),'firstDominanceDelayCounts':cdict(first_delay),'actions':cdict(Counter(str(r['teacherAction']) for r in z))}
    # Underlying Pareto labels are themselves produced from 5/10/20s future counterfactual target-error/completion-cost outcomes.
    future_label_fields=['deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s','deltaCompletionCost5s','deltaCompletionCost10s','deltaCompletionCost20s']
    curr_has_future=sum(all(k in r for k in future_label_fields) for r in curr)
    report={
      'version':'EXECUTION_ARBITRATION_TEACHER_IDENTIFIABILITY_AUDIT_V0','researchOnly':True,'candidateFrozen':False,'graduationEligible':False,
      'teacherRows':len(teacher),'markets':len(by),'adjacentPairs':adjacent,'adjacentActionFlipRate':flips/adjacent if adjacent else None,'adjacentSameRate':same/adjacent if adjacent else None,'waitToActAdjacent':wait_to_act,'transitions':cdict(transitions),
      'delayStats':delay_stats,'phaseConditionalEntropy':phase_stats,'splitStability':split_stats,
      'futureDependencyAudit':{
        'step0Rows':len(step0),'step0NonDominantRows':len(step0_nondom),'step0NonDominantLabelExactlyDeterminedByFutureDominanceAvailability':step0_future_defined,'rate':step0_future_defined/len(step0_nondom) if step0_nondom else None,
        'waitRows':len(wait_rows),'waitRowsWithFutureDominance':sum(r.get('firstFutureDominance') is not None for r in wait_rows),'returnRows':len(return_rows),'returnRowsWithNoFutureDominance':sum(r.get('firstFutureDominance') is None for r in return_rows),
        'underlyingCurriculumRows':len(curr),'underlyingRowsContainingAll5_10_20sFutureCounterfactualOutcomeFields':curr_has_future,
        'teacherConstructionFinding':'WAIT vs RETURN is explicitly defined by whether a future dominance appears through the fixed 8s observation horizon. ACT occurs at the first dominance checkpoint. The dominance/Pareto label at each checkpoint is itself computed from future 5/10/20s KEEP-vs-REPLACE counterfactual target-error and completion-cost outcomes.'
      },
      'diagnosis':'ARBITRATION_NEEDED as currently defined is not a purely contemporaneous execution-state label. It is a future-counterfactual teacher/hazard: WAIT/RETURN depends on future clarity through 8s, and ACT depends on a Pareto label constructed from 5/10/20s future counterfactual outcomes. Weak chronological transfer after V0/V1/V2 therefore cannot be interpreted only as missing features.',
      'recommendation':[
        'Do not train another ARBITRATION_NEEDED model against this teacher or tune thresholds on the opened windows.',
        'Redesign the gate teacher as a strict-past observable execution-state transition/termination label, or explicitly rename/model the existing label as a future-clarity hazard with survival/censor semantics.',
        'Keep frozen WAIT-vs-RETURN and REPLACE-vs-KEEP experts separate; do not open Candidate V2 closed-loop/graduation until the gate teacher has a deployable strict-past semantic.'
      ],
      'guards':['Opened canonical 149-market execution curriculum only.','No Candidate V1 formal exam outcomes used.','No winner/PnL/Target runtime input.','No model training or threshold sweep.','2026-08-16 and final75-99 untouched.']
    }
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(OUT),'key':{'adjacentFlipRate':report['adjacentActionFlipRate'],'step0FutureDefinedRate':report['futureDependencyAudit']['rate'],'waitFuture':report['futureDependencyAudit']['waitRowsWithFutureDominance'],'waitRows':len(wait_rows),'returnNoFuture':report['futureDependencyAudit']['returnRowsWithNoFutureDominance'],'returnRows':len(return_rows),'splitStability':split_stats}},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
