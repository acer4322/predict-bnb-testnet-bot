from __future__ import annotations
import argparse,json,statistics
from pathlib import Path
HIST=['historySideUp','historyAgeMs','historyRunLength','recentCleanCount','recentCleanUpRatio','recentRiskUpQty','recentRiskDownQty','recentRiskNet']

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];by={}
    for r in rows:by.setdefault(int(r['marketId']),r)
    side_medians={}
    for side in (0,1):
        vals=[float(r['absNet']) for r in by.values() if int(r['marketSideUp'])==side];side_medians[side]=statistics.median(vals) if vals else 0.0
    groups={}
    for m,r in by.items():
        side=int(r['marketSideUp']);abin=int(float(r['absNet'])>side_medians[side]);groups.setdefault((side,abin),[]).append(m)
    donor={}
    for key,ms in groups.items():
        ms=sorted(ms)
        if len(ms)<=1:
            donor[ms[0]]=ms[0];continue
        for i,m in enumerate(ms):donor[m]=ms[(i+1)%len(ms)]
    hist={m:{k:by[m].get(k) for k in HIST} for m in by};outrows=[]
    for r in rows:
        m=int(r['marketId']);dm=donor[m];z=dict(r)
        for k in HIST:z[k]=hist[dm].get(k)
        z['historyShuffleDonorMarketId']=dm;z['historyShuffleChanged']=dm!=m;outrows.append(z)
    o=dict(d);o['version']='MANAGEMENT_TRAINING_V1_HISTORY_FEATURE_SHUFFLE_CONTROL_V1';o['rows']=outrows;o['historyShuffle']={'method':'deterministic cyclic market-vector shuffle within marketSideUp x above/below-side-median absNet bins','fields':HIST,'sideAbsNetMedians':side_medians,'changedMarkets':sum(donor[m]!=m for m in donor),'markets':len(donor)};o['boundary']=list(d.get('boundary') or [])+['negative control only; action/outcome/portfolio/economic fields unchanged','history feature vector shuffled as a whole across coarse matched markets; no outcome used']
    Path(a.output).write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,**o['historyShuffle']},ensure_ascii=False))
if __name__=='__main__':main()
