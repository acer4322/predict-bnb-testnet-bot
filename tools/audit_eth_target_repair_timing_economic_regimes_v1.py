from __future__ import annotations
import argparse,json,math,os,sys,importlib.util
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
    from tools import train_eth_persistent_repair_specialist_v3_parent_child_graph as v3
except ImportError:
    p=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py')
    s=importlib.util.spec_from_file_location('v3timing',p);v3=importlib.util.module_from_spec(s);s.loader.exec_module(v3)

SEED=20260901

def delay_sec(r):
    z=r.get('time_to_next_repair')
    if z is None:return None
    return float(np.expm1(float(z)*math.log1p(120000.0))/1000.0)

def add_labels(rows):
    by={}
    for r in rows:by.setdefault(int(r['market']),[]).append(r)
    for rr in by.values():
        rr.sort(key=lambda x:int(x['t']))
        for j,r in enumerate(rr):
            ds=delay_sec(r);r['delaySec']=ds
            r['delay_gt10']=None if ds is None else int(ds>10.)
            r['delay_gt30']=None if ds is None else int(ds>30.)
            next_rep=None
            for y in rr[j+1:]:
                if int(y['t'])-int(r['t'])>30000:break
                if int(y['rel'])==1:
                    next_rep=y;break
            if next_rep is None:r['expand_before_repair_30s']=None
            else:
                r['expand_before_repair_30s']=int(any(int(y['rel'])==-1 and int(y['t'])<int(next_rep['t']) for y in rr[j+1:]))
    return rows

def metrics(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,pred))}

def fit_eval(tr,te,label,full):
    tr=[r for r in tr if r.get(label) is not None];te=[r for r in te if r.get(label) is not None]
    Xtr=np.stack([np.concatenate([r['cur'],r['graph']]) if full else r['cur'] for r in tr]); ytr=np.asarray([r[label] for r in tr],int)
    Xte=np.stack([np.concatenate([r['cur'],r['graph']]) if full else r['cur'] for r in te]); yte=np.asarray([r[label] for r in te],int)
    m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.055,max_leaf_nodes=15,l2_regularization=.5,random_state=SEED,class_weight='balanced')
    m.fit(Xtr,ytr);p=m.predict_proba(Xte)[:,1]
    return metrics(yte,p)

def qstats(a):
    a=np.asarray(a,float)
    if not len(a):return {'n':0}
    return {'n':int(len(a)),'mean':float(a.mean()),'median':float(np.median(a)),'q10':float(np.quantile(a,.1)),'q25':float(np.quantile(a,.25)),'q75':float(np.quantile(a,.75)),'q90':float(np.quantile(a,.9))}

def bucket_summary(rows):
    buckets={'LE5':[],'GT5_LE10':[],'GT10_LE30':[],'GT30':[]}
    for r in rows:
        d=r.get('delaySec')
        if d is None:continue
        k='LE5' if d<=5 else 'GT5_LE10' if d<=10 else 'GT10_LE30' if d<=30 else 'GT30'
        buckets[k].append(r)
    out={}
    ci={x:i for i,x in enumerate(v3.CUR_FEATURES)};gi={x:i for i,x in enumerate(v3.GRAPH_FEATURES)}
    for k,rr in buckets.items():
        out[k]={'n':len(rr),'delaySec':qstats([r['delaySec'] for r in rr])}
        if rr:
            out[k]['economics']={
              'pairCoverage':qstats([r['cur'][ci['pair_coverage']] for r in rr]),
              'absnetRatio':qstats([r['cur'][ci['absnet_ratio']] for r in rr]),
              'floorRatio':qstats([r['cur'][ci['floor_ratio']] for r in rr]),
              'bestPnlRatio':qstats([r['cur'][ci['best_pnl_ratio']] for r in rr]),
              'grossLog':qstats([r['cur'][ci['gross_log']] for r in rr]),
              'repairChildFrac':qstats([r['graph'][gi['repair_child_frac']] for r in rr]),
              'expandChildFrac':qstats([r['graph'][gi['expand_child_frac']] for r in rr]),
              'recentRepairDensity':qstats([r['graph'][gi['recent_repair_density']] for r in rr]),
              'recentExpandDensity':qstats([r['graph'][gi['recent_expand_density']] for r in rr]),
              'coexistenceRate':float(np.mean([r['graph'][gi['coexistence_flag']] for r in rr]))
            }
    return out

def simple_separation(rows):
    ci={x:i for i,x in enumerate(v3.CUR_FEATURES)};gi={x:i for i,x in enumerate(v3.GRAPH_FEATURES)}
    features={
      'pairCoverage':lambda r:r['cur'][ci['pair_coverage']], 'absnetRatio':lambda r:r['cur'][ci['absnet_ratio']],
      'floorRatio':lambda r:r['cur'][ci['floor_ratio']], 'bestPnlRatio':lambda r:r['cur'][ci['best_pnl_ratio']],
      'repairChildFrac':lambda r:r['graph'][gi['repair_child_frac']], 'expandChildFrac':lambda r:r['graph'][gi['expand_child_frac']],
      'recentRepairDensity':lambda r:r['graph'][gi['recent_repair_density']], 'recentExpandDensity':lambda r:r['graph'][gi['recent_expand_density']],
      'coexistenceFlag':lambda r:r['graph'][gi['coexistence_flag']]
    }
    out={}
    for label in ['delay_gt10','delay_gt30','expand_before_repair_30s']:
        z=[r for r in rows if r.get(label) is not None]
        a=[r for r in z if r[label]==0];b=[r for r in z if r[label]==1]
        out[label]={'negativeN':len(a),'positiveN':len(b),'features':{}}
        for n,f in features.items():
            out[label]['features'][n]={'negativeMedian':float(np.median([f(r) for r in a])) if a else None,'positiveMedian':float(np.median([f(r) for r in b])) if b else None,'deltaPositiveMinusNegative':float(np.median([f(r) for r in b])-np.median([f(r) for r in a])) if a and b else None}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');a=ap.parse_args()
    rows,cut,nwin,births=v3.build(a.db);rows=add_labels(rows);tr=[r for r in rows if int(r['end'])<cut];te=[r for r in rows if int(r['end'])>=cut]
    tasks={}
    for lab in ['delay_gt10','delay_gt30','expand_before_repair_30s']:
        g=fit_eval(tr,te,lab,False);f=fit_eval(tr,te,lab,True);tasks[lab]={'geometryOnly':g,'geometryPlusParentGraph':f,'aucLiftGraph':None if g['auc'] is None or f['auc'] is None else float(f['auc']-g['auc'])}
    out={'version':'ETH_TARGET_REPAIR_TIMING_ECONOMIC_REGIMES_V1','researchOnly':True,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'parentBirths':births,'rows':len(rows),'trainRows':len(tr),'testRows':len(te),'testDelayBuckets':bucket_summary(te),'testStrictPastSeparations':simple_separation(te),'chronologyModels':tasks,'keepSignalModelRule':bool(any((v['geometryPlusParentGraph']['auc'] or 0)>=.60 for v in tasks.values()) and any((v['aucLiftGraph'] or 0)>=.02 for v in tasks.values())),'boundary':['Target future timing/event labels are supervision/evaluation only','strict-past current geometry + parent graph features only','no winner/future PnL features','BTC architecture-only','no runtime/action change']}
    op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'windows':nwin,'rows':len(rows),'tasks':tasks,'bucketCounts':{k:v['n'] for k,v in out['testDelayBuckets'].items()},'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
