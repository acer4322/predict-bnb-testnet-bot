from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np,joblib
from sklearn.metrics import roc_auc_score,average_precision_score,brier_score_loss
import importlib.util
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('teach',HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py');teach=importlib.util.module_from_spec(spec);spec.loader.exec_module(teach)
FEATURES=list(teach.FEATURES)

def met(rr,model):
 if not rr:return {'n':0}
 X=np.asarray([[np.nan if r.get(k) is None else float(r.get(k)) for k in FEATURES] for r in rr],np.float32);y=np.asarray([int(r['label_repair_taker_1s']) for r in rr],int);p=model.predict_proba(X)[:,1];ix=np.argsort(-p);k=max(1,int(math.ceil(len(y)*.1)))
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)),'meanP':float(np.mean(p)),'top10pctPositiveRate':float(y[ix[:k]].mean()),'top10pctRecall':float(y[ix[:k]].sum()/y.sum()) if y.sum()>0 else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=teach.build(a.db);parts,sp=teach.split(rows);art=joblib.load(a.model);m=art['model'];out={'version':'TARGET_ETH_ZERO_TAKER_HISTORY_REPAIR_ESCALATION_AUDIT_V1','researchOnly':True,'split':sp,'allRows':len(rows),'slice':{},'boundary':['Frozen 1s teacher only; no refit.','Strict-past taker_gross==0 slice matches pre-first-active-intervention domain.','No threshold sweep or OUR outcome use.']}
 for name in ('train','validation','test'):
  rr=[r for r in parts[name] if abs(float(r.get('taker_gross') or 0.0))<=1e-9]
  out['slice'][name]=met(rr,m)
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
