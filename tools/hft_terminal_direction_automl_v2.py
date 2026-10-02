from __future__ import annotations
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
EPS=1e-9
DATASETS=['hft_native_constrained_rank_unused90_v4.json','hft_native_timegrid_inventory_unused80_v3.json']

def reward(action,winner):
    q=float(action.get('filledShares5s') or 0.0)
    if q<=EPS:return 0.0
    px=action.get('fillPrice')
    try:px=float(px)
    except Exception:px=float(action['price'])
    if not math.isfinite(px):px=float(action['price'])
    return q*((1.0 if str(action.get('side')).upper()==winner else 0.0)-px)

def load_all():
    rows=[]
    seen=set()
    for fn in DATASETS:
        d=json.loads((BASE/fn).read_text(encoding='utf8'))
        for r in d.get('rows') or []:
            key=(int(r['marketId']),int(r['checkpointMs']))
            if key in seen:continue
            seen.add(key);rows.append(r)
    rows.sort(key=lambda r:int(r['checkpointMs']))
    return rows

def eval_policy(rows,threshold,offset):
    mids=sorted({int(r['marketId']) for r in rows}); wm=winners(mids)
    total=0.;filled=pos=neg=acts=0;per={}
    for r in rows:
        mid=int(r['marketId']);w=str(wm[mid]).upper();f=r.get('features') or {};ds=f.get('directionScore')
        try:ds=float(ds)
        except Exception:continue
        if not math.isfinite(ds) or abs(ds)<threshold:continue
        side='UP' if ds>=0 else 'DOWN'
        cand=[a for a in r.get('actions') or [] if not a.get('invalid') and str(a.get('side')).upper()==side and int(a.get('offset'))==offset]
        if not cand:continue
        a=cand[0];acts+=1;rr=reward(a,w);total+=rr;per[mid]=per.get(mid,0.)+rr
        if float(a.get('filledShares5s') or 0)>EPS:filled+=1;pos+=int(rr>EPS);neg+=int(rr<-EPS)
    return {'reward':total,'acts':acts,'filledActs':filled,'positiveActs':pos,'negativeActs':neg,'positiveMarkets':sum(v>EPS for v in per.values()),'negativeMarkets':sum(v<-EPS for v in per.values()),'markets':len(mids)}

def main():
    rows=load_all(); markets=[]
    for r in rows:
        m=int(r['marketId'])
        if m not in markets:markets.append(m)
    dev=markets[:140]; val=markets[140:150]
    dev_rows=[r for r in rows if int(r['marketId']) in set(dev)]; val_rows=[r for r in rows if int(r['marketId']) in set(val)]
    folds=[dev[:70],dev[70:105],dev[105:140]]
    grid=[]
    for off in (0,1,2):
      for th in (0.0,0.05,0.1,0.15,0.2,0.3,0.4,0.5,0.6,0.7):
        fm=[eval_policy([r for r in dev_rows if int(r['marketId']) in set(ids)],th,off) for ids in folds]
        agg=eval_policy(dev_rows,th,off)
        minrew=min(x['reward'] for x in fm);neg=sum(x['negativeActs'] for x in fm);filled=sum(x['filledActs'] for x in fm)
        grid.append({'threshold':th,'offset':off,'folds':fm,'aggregate':agg,'minFoldReward':minrew,'foldNegativeActs':neg,'foldFilledActs':filled})
    # Prefer no negative-reward fold, then positive worst fold, then total reward, then fewer negatives, then more fills.
    grid.sort(key=lambda x:(x['minFoldReward']>0,x['minFoldReward'],x['aggregate']['reward'],-x['foldNegativeActs'],x['foldFilledActs']),reverse=True)
    best=grid[0]
    valm=eval_policy(val_rows,best['threshold'],best['offset'])
    fresh=json.loads((BASE/'hft_native_timegrid_fresh2_terminal_20260823.json').read_text(encoding='utf8'))
    freshm=eval_policy(fresh.get('rows') or [],best['threshold'],best['offset'])
    rep={'version':'HFT_TERMINAL_DIRECTION_AUTOML_V2','researchOnly':True,'dreamFillAllowed':False,'selection':'140 opened development markets only; 3 rolling chronological blocks','searchSpace':{'thresholds':[0.0,0.05,0.1,0.15,0.2,0.3,0.4,0.5,0.6,0.7],'offsets':[0,1,2]},'winner':best,'validation10':valm,'fresh2':freshm,'top10':grid[:10]}
    (BASE/'hft_terminal_direction_automl_v2_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'winner':best,'validation10':valm,'fresh2':freshm,'top5':grid[:5]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
