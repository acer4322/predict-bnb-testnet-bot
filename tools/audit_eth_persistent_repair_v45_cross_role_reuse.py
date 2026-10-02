from __future__ import annotations
import argparse,json,sys,importlib.util
from pathlib import Path
import numpy as np
import torch
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def lm(name,file):
    try:return __import__('tools.'+name,fromlist=['*'])
    except Exception:
        p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
v3=lm('train_eth_persistent_repair_specialist_v3_parent_child_graph','train_eth_persistent_repair_specialist_v3_parent_child_graph.py')
v4=lm('train_eth_persistent_repair_specialist_v4_selective_parallel_experts','train_eth_persistent_repair_specialist_v4_selective_parallel_experts.py')
v5=lm('train_eth_persistent_repair_specialist_v5_factorized_capability_router','train_eth_persistent_repair_specialist_v5_factorized_capability_router.py')
def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);q=(p>=.5).astype(int);return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,q))}
def pred(model,rows,task,dev):
 z,X,G,A,AM,R,RM,E,EM,y=v5.arr(rows,task);ps=[];model.eval()
 with torch.no_grad():
  for i in range(0,len(y),4096):ps.append(torch.sigmoid(model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev))).cpu().numpy())
 return z,y,np.concatenate(ps)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--v4-model',required=True);ap.add_argument('--v5-model',required=True);ap.add_argument('--output');a=ap.parse_args()
 rows,cut,nwin,births=v3.build(a.db);rows=v5.relabel(rows);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];v3.standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu';d4=torch.load(a.v4_model,map_location='cpu',weights_only=False);d5=torch.load(a.v5_model,map_location='cpu',weights_only=False)
 # Frozen generic repair-obligation expert.
 gen=v5.CapabilityExpert(v5.MODES['repair_obligation_30s']);gen.load_state_dict(d5['models']['repair_obligation_30s']);gen=gen.to(dev)
 zg,yg,pg=pred(gen,te,'repair_obligation_30s',dev);gmap={id(r):float(p) for r,p in zip(zg,pg)}
 # Dedicated frozen candidates.
 ded5=v5.CapabilityExpert(v5.MODES['repair_after_expand_30s']);ded5.load_state_dict(d5['models']['repair_after_expand_30s']);ded5=ded5.to(dev)
 ded4=v4.SequenceExpert(v4.MODES['repair_after_current_expand_30s']);ded4.load_state_dict(d4['models']['repair_after_current_expand_30s']);ded4=ded4.to(dev)
 cont5=v5.CapabilityExpert(v5.MODES['repair_continues_30s']);cont5.load_state_dict(d5['models']['repair_continues_30s']);cont5=cont5.to(dev)
 za,ya,p5=pred(ded5,te,'repair_after_expand_30s',dev);_,_,p4=pred(ded4,te,'repair_after_expand_30s',dev);pg_a=np.asarray([gmap[id(r)] for r in za])
 zc,yc,pc=pred(cont5,te,'repair_continues_30s',dev);pg_c=np.asarray([gmap[id(r)] for r in zc])
 out={'version':'ETH_PERSISTENT_REPAIR_V45_CROSS_ROLE_REUSE_AUDIT','researchOnly':True,'device':dev,'currentExpand':{'genericRepairObligation':metric(ya,pg_a),'v4Dedicated':metric(ya,p4),'v5Dedicated':metric(ya,p5)},'currentRepair':{'genericRepairObligation':metric(yc,pg_c),'v5DedicatedContinuation':metric(yc,pc)},'interpretationRule':'If generic repair-obligation is competitive or stronger across both role-conditioned subsets, prefer one role-invariant repair obligation lane over separate after-expand/continuation brains. No gradients or threshold tuning.'}
 op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
