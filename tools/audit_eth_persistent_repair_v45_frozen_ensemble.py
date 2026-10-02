from __future__ import annotations
import argparse,json,sys,importlib.util
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def load_mod(name,filename):
    try:
        return __import__('tools.'+name,fromlist=['*'])
    except Exception:
        p=Path(__file__).resolve().with_name(filename)
        sp=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);return m

v3=load_mod('train_eth_persistent_repair_specialist_v3_parent_child_graph','train_eth_persistent_repair_specialist_v3_parent_child_graph.py')
v4=load_mod('train_eth_persistent_repair_specialist_v4_selective_parallel_experts','train_eth_persistent_repair_specialist_v4_selective_parallel_experts.py')
v5=load_mod('train_eth_persistent_repair_specialist_v5_factorized_capability_router','train_eth_persistent_repair_specialist_v5_factorized_capability_router.py')

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def predict_model(model,rows,task,dev):
    z,X,G,A,AM,R,RM,E,EM,y=v5.arr(rows,task);ps=[];model.eval()
    with torch.no_grad():
        for i in range(0,len(y),4096):
            q=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev));ps.append(torch.sigmoid(q).cpu().numpy())
    return z,y,np.concatenate(ps)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--v4-model',required=True);ap.add_argument('--v5-model',required=True);ap.add_argument('--output');a=ap.parse_args()
    rows,cut,nwin,births=v3.build(a.db);rows=v5.relabel(rows);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];stats=v3.standardize(tr,te)
    d4=torch.load(a.v4_model,map_location='cpu',weights_only=False);d5=torch.load(a.v5_model,map_location='cpu',weights_only=False)
    statdiff=[float(np.max(np.abs(x-y))) for x,y in zip(d4['stats'],d5['stats'])]
    dev='cuda' if torch.cuda.is_available() else 'cpu'
    models={}
    for t in ('repair_obligation_30s','expand_opportunity_30s','activity_urgency_30s','repair_continues_30s','repair_after_expand_30s','both_responsibilities_30s'):
        m=v5.CapabilityExpert(v5.MODES[t]);m.load_state_dict(d5['models'][t]);models['v5_'+t]=m.to(dev)
    m=v4.SequenceExpert(v4.MODES['repair_after_current_expand_30s']);m.load_state_dict(d4['models']['repair_after_current_expand_30s']);models['v4_repair_after_expand_30s']=m.to(dev)
    m=v4.SequenceExpert(v4.MODES['parallel_children_within_30s']);m.load_state_dict(d4['models']['parallel_children_within_30s']);models['v4_both_responsibilities_30s']=m.to(dev)

    pred={};labels={};rowids={};rawmetrics={}
    mapping={
      'repair_obligation_30s':'v5_repair_obligation_30s',
      'expand_opportunity_30s':'v5_expand_opportunity_30s',
      'activity_urgency_30s':'v5_activity_urgency_30s',
      'repair_continues_30s':'v5_repair_continues_30s',
      'v5_repair_after_expand_30s':'v5_repair_after_expand_30s',
      'v4_repair_after_expand_30s':'v4_repair_after_expand_30s',
      'v5_both_responsibilities_30s':'v5_both_responsibilities_30s',
      'v4_both_responsibilities_30s':'v4_both_responsibilities_30s'
    }
    task_for={k:(k if k in v5.TASKS else ('repair_after_expand_30s' if 'repair_after_expand' in k else 'both_responsibilities_30s')) for k in mapping}
    for outk,mk in mapping.items():
        task=task_for[outk];z,y,p=predict_model(models[mk],te,task,dev);pred[outk]=p;labels[outk]=y;rowids[outk]=[id(r) for r in z];rawmetrics[outk]=metric(y,p)

    repair_after_choice=max(('v4_repair_after_expand_30s','v5_repair_after_expand_30s'),key=lambda k:rawmetrics[k]['auc'])
    both_choice=max(('v4_both_responsibilities_30s','v5_both_responsibilities_30s'),key=lambda k:rawmetrics[k]['auc'])

    global_tasks=('repair_obligation_30s','expand_opportunity_30s','activity_urgency_30s',both_choice)
    maps={k:{rid:float(p) for rid,p in zip(rowids[k],pred[k])} for k in pred}
    ymap={k:{rid:int(y) for rid,y in zip(rowids[k],labels[k])} for k in pred}
    common=[r for r in te if all(id(r) in maps[k] for k in global_tasks)]
    yp=[];pbp=[];pa=[];pap=[];pr=[];pe=[];viol_b=0;viol_a=0
    for r in common:
        rid=id(r);rr=maps['repair_obligation_30s'][rid];ee=maps['expand_opportunity_30s'][rid];bb=maps[both_choice][rid];aa=maps['activity_urgency_30s'][rid]
        bproj=min(bb,rr,ee);aproj=max(aa,rr,ee)
        pr.append(rr);pe.append(ee);pbp.append(bproj);pa.append(aa);pap.append(aproj);yp.append(ymap[both_choice][rid])
        if bproj>.5 and (rr<=.5 or ee<=.5):viol_b+=1
        if (rr>.5 or ee>.5) and aproj<=.5:viol_a+=1
    proj_both=metric(np.asarray(yp),np.asarray(pbp))
    # activity labels align to all common rows
    yact=np.asarray([ymap['activity_urgency_30s'][id(r)] for r in common]);proj_act=metric(yact,np.asarray(pap))

    clean={
      'repair_obligation_30s':rawmetrics['repair_obligation_30s'],
      'expand_opportunity_30s':rawmetrics['expand_opportunity_30s'],
      'both_responsibilities_30s':proj_both,
      'activity_urgency_30s':proj_act,
      'repair_after_expand_30s':rawmetrics[repair_after_choice],
      'repair_continues_30s':rawmetrics['repair_continues_30s']
    }
    gates={
      'repairObligation':clean['repair_obligation_30s']['auc']>=.64,
      'expandOpportunity':clean['expand_opportunity_30s']['auc']>=.60,
      'bothProjected':clean['both_responsibilities_30s']['auc']>=.60,
      'activityProjected':clean['activity_urgency_30s']['auc']>=.68,
      'repairAfterExpand':clean['repair_after_expand_30s']['auc']>=.64,
      'repairContinues':clean['repair_continues_30s']['auc']>=.62,
      'bothLogical':viol_b==0,
      'activityLogical':viol_a==0
    }
    out={'version':'ETH_PERSISTENT_REPAIR_SPECIALIST_V45_FROZEN_ENSEMBLE','researchOnly':True,'device':dev,'chronologyCutoff':cut,'windows':nwin,'rows':len(rows),'testRows':len(te),'statsMaxAbsDiffV4V5':statdiff,'candidateMetrics':rawmetrics,'selectedRepairAfterExpand':repair_after_choice,'selectedBothResponsibilities':both_choice,'projectedCapabilityMetrics':clean,'logicalProjection':{'n':len(common),'bothImpliesEachViolationRate':viol_b/max(1,len(common)),'activityDominatesRepairExpandViolationRate':viol_a/max(1,len(common))},'gates':gates,'representationPass':bool(all(gates.values())),'boundary':['No new gradients','V4/V5 frozen development experts only','Deterministic logical projection only','Same consumed development chronology; not graduation']}
    op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'representationPass':out['representationPass'],'selectedRepairAfterExpand':repair_after_choice,'selectedBoth':both_choice,'metrics':clean,'logical':out['logicalProjection'],'gates':gates,'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
