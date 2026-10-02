from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/V32F_TARGET_STRIKE_TIME_PASSIVE_REACHABILITY_V1.json'
TBINS=[0,30,60,120,180,240,301]
TLABS=['0-30','30-60','60-120','120-180','180-240','240-300']
DBINS=[0,2,5,10,20,1e9]
DLABS=['0-2','2-5','5-10','10-20','20+']
CBINS=[0,.2,.4,.6,.8,1.000001]
CLABS=['0-.2','.2-.4','.4-.6','.6-.8','.8-1']

def rate(x,c):
    return None if len(x)==0 else float(x[c].mean())

def main():
    d=pd.read_csv(SRC,low_memory=False)
    d=d[d.spot_minus_strike_bps.notna() & d.seconds_left.notna() & d.predict_up_mid.notna()].copy()
    d['time_bin']=pd.cut(d.seconds_left,TBINS,labels=TLABS,right=False,include_lowest=True)
    d['dist_bin']=pd.cut(d.spot_minus_strike_bps.abs(),DBINS,labels=DLABS,right=False,include_lowest=True)
    d['conf_bin']=pd.cut(d.predict_up_mid,CBINS,labels=CLABS,right=False,include_lowest=True)
    rows=[]
    for t in TLABS:
      for q in DLABS:
        z=d[(d.time_bin==t)&(d.dist_bin==q)]
        if len(z)>=100:
          rows.append({'timeBin':t,'distanceBin':q,'n':int(len(z)),'markets':int(z.market_id.nunique()),
                       'placement1s':rate(z,'label_next_inferred_placement_any_1s'),
                       'placement5s':rate(z,'label_next_inferred_placement_any_5s'),
                       'meanPredictUpMid':float(z.predict_up_mid.mean())})
    cond=[]
    for t in TLABS:
      for q in DLABS:
       for c in CLABS:
        z=d[(d.time_bin==t)&(d.dist_bin==q)&(d.conf_bin==c)]
        if len(z)>=80:
          cond.append({'timeBin':t,'distanceBin':q,'predictBin':c,'n':int(len(z)),
                       'placement1s':rate(z,'label_next_inferred_placement_any_1s'),
                       'placement5s':rate(z,'label_next_inferred_placement_any_5s')})
    # Compare near-vs-far inside each time bucket and early-vs-late inside each distance bucket.
    contrasts=[]
    for t in TLABS:
      a=d[(d.time_bin==t)&(d.dist_bin=='0-2')]; b=d[(d.time_bin==t)&(d.dist_bin.isin(['5-10','10-20']))]
      if len(a)>=100 and len(b)>=100:
        contrasts.append({'axis':'distanceWithinTime','timeBin':t,'nearN':len(a),'farN':len(b),
                          'near1s':rate(a,'label_next_inferred_placement_any_1s'),'far1s':rate(b,'label_next_inferred_placement_any_1s'),
                          'near5s':rate(a,'label_next_inferred_placement_any_5s'),'far5s':rate(b,'label_next_inferred_placement_any_5s')})
    for q in DLABS:
      early=d[(d.dist_bin==q)&(d.time_bin.isin(['180-240','240-300']))]
      late=d[(d.dist_bin==q)&(d.time_bin.isin(['0-30','30-60']))]
      if len(early)>=100 and len(late)>=100:
        contrasts.append({'axis':'timeWithinDistance','distanceBin':q,'earlyN':len(early),'lateN':len(late),
                          'early1s':rate(early,'label_next_inferred_placement_any_1s'),'late1s':rate(late,'label_next_inferred_placement_any_1s'),
                          'early5s':rate(early,'label_next_inferred_placement_any_5s'),'late5s':rate(late,'label_next_inferred_placement_any_5s')})
    rep={'version':'V32F_TARGET_STRIKE_TIME_PASSIVE_REACHABILITY_V1','researchOnly':True,'actionAuthority':False,
         'source':'target_maker_direct_hazard_v1.csv','rows':int(len(d)),'markets':int(d.market_id.nunique()),
         'fixedBins':{'timeSeconds':TLABS,'absStrikeBps':DLABS,'predictUpMid':CLABS},
         'jointHeatmap':rows,'predictConditioned':cond,'contrasts':contrasts,
         'boundary':['strict-past public state','no winner/PnL','describes passive placement reachability, not Repair-only or Taker authority','BTC numeric bins not transferable to ETH']}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'rows':rep['rows'],'markets':rep['markets'],'contrasts':contrasts,'output':str(OUT)},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
