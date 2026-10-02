from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_target_eth_btc_reexpand_economic_transfer_v9 as v9
PB=[('P0',-1e-12,1e-12),('P0_25',1e-12,.25),('P25_50',.25,.5),('P50_75',.5,.75),('P75_100',.75,1.0000001)]

def summ(rows):
    times=np.asarray([float(r['x'][6]) for r in rows]);qs=np.quantile(times,[.25,.5,.75]);TB=[('T1',-1,qs[0]),('T2',qs[0],qs[1]),('T3',qs[1],qs[2]),('T4',qs[2],1e30)]
    cells=[];weighted_num=0.;weighted_den=0.;signs=[]
    for pn,plo,phi in PB:
        for tn,tlo,thi in TB:
            z=[r for r in rows if ((r['x'][0]>=plo and r['x'][0]<=phi) if pn=='P0' else (r['x'][0]>plo and r['x'][0]<=phi)) and float(r['x'][6])>tlo and float(r['x'][6])<=thi]
            a=[r['y'] for r in z if float(r['x'][7])>=.5];b=[r['y'] for r in z if float(r['x'][7])<.5]
            ra=sum(a)/len(a) if a else None;rb=sum(b)/len(b) if b else None;diff=(ra-rb) if ra is not None and rb is not None else None
            if diff is not None:
                w=min(len(a),len(b));weighted_num+=w*abs(diff);weighted_den+=w
                if abs(diff)>1e-9:signs.append(1 if diff>0 else -1)
            cells.append({'progressBin':pn,'timeBin':tn,'nLastExpand':len(a),'nLastRepair':len(b),'reexpandRateLastExpand':ra,'reexpandRateLastRepair':rb,'differenceExpandMinusRepair':diff})
    return {'n':len(rows),'elapsedQuartilesMs':[float(x) for x in qs],'weightedAbsPathEffect':weighted_num/weighted_den if weighted_den else None,'positiveDifferenceFraction':sum(x>0 for x in signs)/len(signs) if signs else None,'cells':cells}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();out={'version':'TARGET_BTC_ETH_REEXPAND_HYSTERESIS_PATH_V17','researchOnly':True,'boundary':['Target-only path-dependence audit','Conditions on coarse repair-progress and elapsed lifecycle bins, then compares next re-expand rate by previous material transition identity','Evidence is behavioral and does not prove internal implementation','No threshold exported to OUR'], 'assets':{}}
    for asset in ('BTC','ETH'):
        rows=v9.build(a.db,asset);ends=sorted(set(r['end'] for r in rows if r['end'] is not None));cut=ends[int(len(ends)*.70)];out['assets'][asset]={'cut':cut,'early':summ([r for r in rows if r['end']<cut]),'late':summ([r for r in rows if r['end']>=cut])}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True)
if __name__=='__main__':main()
