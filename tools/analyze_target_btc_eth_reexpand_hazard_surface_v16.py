from __future__ import annotations
import argparse,json,statistics
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_eth_btc_reexpand_economic_transfer_v9 as v9

PBINS=[('P0',-1e-12,1e-12),('P0_25',1e-12,.25),('P25_50',.25,.5),('P50_75',.5,.75),('P75_100',.75,1.0000001)]

def summarize(rows):
    if not rows:return {'n':0}
    times=np.asarray([float(r['x'][6]) for r in rows]);q=np.quantile(times,[.25,.5,.75]);
    tq=[('T1',-1,float(q[0])),('T2',float(q[0]),float(q[1])),('T3',float(q[1]),float(q[2])),('T4',float(q[2]),1e30)]
    mat={}; prog_time_rho={}
    for pn,plo,phi in PBINS:
        rates=[];cells=[]
        for tn,tlo,thi in tq:
            z=[r for r in rows if ((r['x'][0]<=phi and r['x'][0]>=plo) if pn=='P0' else (r['x'][0]>plo and r['x'][0]<=phi)) and float(r['x'][6])>tlo and float(r['x'][6])<=thi]
            rate=sum(r['y'] for r in z)/len(z) if z else None;rates.append(rate);cells.append({'timeBin':tn,'n':len(z),'reexpandRate':rate})
        valid=[(i,r) for i,r in enumerate(rates) if r is not None]
        rho=float(spearmanr([i for i,_ in valid],[r for _,r in valid]).statistic) if len(valid)>=3 else None
        mat[pn]=cells;prog_time_rho[pn]=rho
    time_prog_rho={}
    for ti,(tn,_,_) in enumerate(tq):
        rates=[]
        for pn in [x[0] for x in PBINS]:rates.append(mat[pn][ti]['reexpandRate'])
        valid=[(i,r) for i,r in enumerate(rates) if r is not None]
        time_prog_rho[tn]=float(spearmanr([i for i,_ in valid],[r for _,r in valid]).statistic) if len(valid)>=3 else None
    return {'n':len(rows),'elapsedQuartilesMs':[float(x) for x in q],'matrix':mat,'rhoTimeWithinProgress':prog_time_rho,'rhoProgressWithinTime':time_prog_rho}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    out={'version':'TARGET_BTC_ETH_REEXPAND_HAZARD_SURFACE_V16','researchOnly':True,'boundary':['Target-only Maker responsibility hazard surface','No fixed threshold or Target classifier exported to OUR','Elapsed bins are within-split quartiles, so analysis tests ordering/shape rather than copied absolute times','Repair progress and elapsed lifecycle state jointly condition next Maker responsibility being re-expand vs repair'],'assets':{}}
    for asset in ('BTC','ETH'):
        rows=v9.build(a.db,asset);ends=sorted(set(r['end'] for r in rows if r['end'] is not None));cut=ends[int(len(ends)*.70)];early=[r for r in rows if r['end']<cut];late=[r for r in rows if r['end']>=cut]
        out['assets'][asset]={'cut':cut,'early':summarize(early),'late':summarize(late)}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True)
if __name__=='__main__':main()
