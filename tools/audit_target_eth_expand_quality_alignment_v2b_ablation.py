from __future__ import annotations
import argparse,json,statistics,sys,os
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve().parent
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
import train_target_eth_expand_quality_responsibility_alignment_v2 as v2

FAMS={
 'PRICE_ONLY':['price'],
 'TIME_PRICE':['seconds_left_norm','event_count_norm','prev_age_log','price'],
 'GEOMETRY_NO_PRICE':['pair_coverage','absnet_ratio','floor_ratio','best_ratio','gross_log','overflow_gap_ratio','route_taker','delta_floor_ratio','delta_best_ratio','recent_repair_frac','recent_expand_frac','recent_taker_frac','has_prior_same_side_expand','post_dominant_side_up','prior_same_side_expand_age_log','repair_block_qty_gross','repair_block_notional_cost','repair_block_count_norm','anchor_repair_share_of_block'],
 'FULL':list(v2.FEATURES),
}
SEM='ANCHOR_ONLY'

def ids(names):return [v2.FEATURES.index(x) for x in names]
def arr(z,ix):return np.stack([r['x'][ix] for r in z]),np.asarray([r['labels'][SEM] for r in z],int)

def eval_fam(rows,ends,names):
 ix=ids(names);tr=[r for r in rows if SEM in r['labels'] and r['split']=='train'];va=[r for r in rows if SEM in r['labels'] and r['split']=='validation'];te=[r for r in rows if SEM in r['labels'] and r['split']=='test'];X,y=arr(tr,ix);m=v2.model_fit(X,y);out={}
 for n,z in [('validation',va),('test',te)]:
  Xt,yt=arr(z,ix);out[n]=v2.score(yt,m.predict_proba(Xt)[:,1])
 rolls=[];rz=[r for r in rows if SEM in r['labels']]
 for k,(a,b) in enumerate([(.5,.6),(.6,.7),(.7,.8),(.8,1.)],1):
  c1=ends[min(len(ends)-1,int(len(ends)*a))];c2=ends[min(len(ends)-1,int(len(ends)*b)-1)] if b<1 else ends[-1];rtr=[r for r in rz if int(r['end'] or 0)<c1];rte=[r for r in rz if int(r['end'] or 0)>=c1 and int(r['end'] or 0)<=c2]
  X,y=arr(rtr,ix);Xt,yt=arr(rte,ix);mm=v2.model_fit(X,y);rolls.append({'fold':k,**v2.score(yt,mm.predict_proba(Xt)[:,1])})
 aucs=[r['auc'] for r in rolls];out['rolling']=rolls;out['rollingMedianAuc']=float(statistics.median(aucs));out['rollingMinAuc']=min(aucs);return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows,ends,c1,c2=v2.build(a.db);res={k:eval_fam(rows,ends,v) for k,v in FAMS.items()};full=res['FULL']['test']['auc'];tp=res['TIME_PRICE']['test']['auc'];gnp=res['GEOMETRY_NO_PRICE']['test']['auc'];lift=full-tp
 if lift<.02 and gnp<.60:decision='MOSTLY_PRICE_TIME_GEOMETRY_PREFER_DETERMINISTIC_LIVE_PAIR_ECONOMICS_RESEARCH'
 elif lift>=.03 and gnp>=.60:decision='MATERIAL_MANAGEMENT_CONTEXT_KEEP_LEARNED_QUALITY_HEAD_RESEARCH'
 else:decision='MIXED_KEEP_SHADOW_ONLY'
 out={'version':'TARGET_ETH_EXPAND_QUALITY_ALIGNMENT_V2B_ABLATION','date':'2026-09-04','researchOnly':True,'actionAuthority':False,'sourceDb':os.path.abspath(a.db),'label':SEM,'featureFamilies':FAMS,'results':res,'diagnostics':{'fullMinusTimePriceTestAuc':lift,'geometryNoPriceTestAuc':gnp},'decision':decision,'stableNextCompositeShadow':'KEEP_UNCHANGED','boundary':['same V2 rows/label','same frozen HGB','strict-past features only','no feature selection after score','no runtime promotion','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'summary':{k:{'testAuc':v['test']['auc'],'rollingMedian':v['rollingMedianAuc'],'rollingMin':v['rollingMinAuc']} for k,v in res.items()},'fullMinusTimePrice':lift,'geometryNoPrice':gnp},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
