from __future__ import annotations
import argparse,sys,joblib,json
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
import train_target_eth_expand_quality_responsibility_alignment_v2 as v2

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--model-out',required=True);ap.add_argument('--meta-out',required=True);a=ap.parse_args();rows,ends,c1,c2=v2.build(a.db);mods={};counts={}
 for sem in v2.SEM:
  z=[r for r in rows if sem in r['labels'] and r['split'] in ('train','validation')]
  X=np.stack([r['x'] for r in z]);y=np.asarray([r['labels'][sem] for r in z],int);mods[sem]=v2.model_fit(X,y);counts[sem]=len(z)
 payload={'version':'TARGET_ETH_EXPAND_QUALITY_RESPONSIBILITY_ALIGNMENT_V2_SHADOW','date':'2026-09-04','actionAuthority':False,'features':v2.FEATURES,'semantics':v2.SEM,'models':mods,'boundary':['ResponsibilityTransition frozen PASS','research shadow only','no runtime admission authority','no threshold tuning','no 8781']};joblib.dump(payload,a.model_out)
 meta={'version':payload['version'],'date':payload['date'],'actionAuthority':False,'features':v2.FEATURES,'semantics':v2.SEM,'trainRows':counts,'source':'TARGET_ETH_EXPAND_QUALITY_RESPONSIBILITY_ALIGNMENT_V2_20260904.json','stableNextCompositeShadow':'KEEP_UNCHANGED'};Path(a.meta_out).write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'trainRows':counts,'modelOut':a.model_out},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
