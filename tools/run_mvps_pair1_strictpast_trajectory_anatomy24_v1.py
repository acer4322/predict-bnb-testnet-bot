from __future__ import annotations
import argparse, json, math, os, shutil, tempfile, zipfile, importlib.util
from pathlib import Path
from collections import defaultdict

HERE=Path(__file__).resolve().parent

def sibling(name, filename):
    p=HERE/filename
    s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

lad=sibling('pair_ladder_anatomy','run_eth_safety_reintroduction_ladder_1946317.py')
base=lad.base
LadderSim=lad.LadderSim
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986,1946036,1946298,1946317,1946448,1946468,1946475,1946488,1946640,1946653,1946656,1946668,1946683,1946748,1946756,1946760,1946784,1946792,1946872,1946876,1946899]
CHECKPOINTS=[0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80]
GROSS_THRESH=[5,10,20,40]
NET_THRESH=[5,10,20,40]

def side_unmatched(sim,side): return sum(float(a) for a,_ in sim.un[side])
def avg_unmatched(sim,side):
    q=side_unmatched(sim,side)
    return (sum(float(a)*float(p) for a,p in sim.un[side])/q) if q>EPS else None

def snap(sim,t,first,end,qv):
    up=float(sim.inv['UP']); dn=float(sim.inv['DOWN']); gross=up+dn; net=up-dn; absnet=abs(net)
    dom='UP' if net>EPS else 'DOWN' if net<-EPS else None
    repair='DOWN' if dom=='UP' else 'UP' if dom=='DOWN' else None
    dom_avg=avg_unmatched(sim,dom) if dom else None
    dom_un=side_unmatched(sim,dom) if dom else 0.0
    maker_pair=None; active_pair=None; maker_head=None; active_head=None
    maker_safe=None; active_safe=None
    if dom and dom_avg is not None and repair:
        maker_pair=float(dom_avg)+float(qv[repair]['bid']); active_pair=float(dom_avg)+float(qv[repair]['ask'])
        maker_head=1.0-maker_pair; active_head=1.0-active_pair
        maker_safe=maker_pair<=1.0000001; active_safe=active_pair<=1.0000001
    progress=(int(t)-int(first))/max(1,int(end)-int(first))
    direction='UP' if float(qv.get('imb') or 0.0)>=0 else 'DOWN'
    return {
      't':int(t),'progress':max(0.0,min(1.0,float(progress))),'secondsLeft':max(0.0,(int(end)-int(t))/1000.0),
      'bookImbalance':float(qv.get('imb') or 0.0),'selectedDirection':direction,
      'upBid':float(qv['UP']['bid']),'upAsk':float(qv['UP']['ask']),'downBid':float(qv['DOWN']['bid']),'downAsk':float(qv['DOWN']['ask']),
      'upQty':up,'downQty':dn,'grossInventory':gross,'netInventory':net,'absNetInventory':absnet,'buyNotional':float(sim.cost),
      'dominantSide':dom,'dominantUnmatchedQty':dom_un,'dominantUnmatchedAvg':dom_avg,'repairSide':repair,
      'makerRepairPairSum':maker_pair,'activeRepairPairSum':active_pair,'makerRepairHeadroom':maker_head,'activeRepairHeadroom':active_head,
      'makerRepairPairSafe':maker_safe,'activeRepairPairSafe':active_safe,
      'fills':int(sim.fills),'submits':int(sim.submits)
    }

def first_cross(snaps,key,thr):
    for x in snaps:
        if float(x[key])>=thr-EPS:return {'t':x['t'],'progress':x['progress'],'value':x[key]}
    return None

def first_nonpos(snaps,key):
    for x in snaps:
        v=x.get(key)
        if v is not None and float(x.get('dominantUnmatchedQty') or 0.0)>EPS and float(v)<=0.0+1e-10:
            return {'t':x['t'],'progress':x['progress'],'value':float(v),'absNet':x['absNetInventory'],'gross':x['grossInventory'],'selectedDirection':x['selectedDirection']}
    return None

def last_pos(snaps,key):
    out=None
    for x in snaps:
        v=x.get(key)
        if v is not None and float(x.get('dominantUnmatchedQty') or 0.0)>EPS and float(v)>0.0:
            out={'t':x['t'],'progress':x['progress'],'value':float(v),'absNet':x['absNetInventory'],'gross':x['grossInventory'],'selectedDirection':x['selectedDirection']}
    return out

def checkpoint_rows(snaps):
    out=[]
    for cp in CHECKPOINTS:
        x=next((r for r in snaps if r['progress']>=cp-1e-12),snaps[-1] if snaps else None)
        if x is not None: out.append({'checkpoint':cp,**x})
    return out

def reversal_after_net5(snaps):
    eligible=False;prev=None
    for x in snaps:
        if x['absNetInventory']>=5-EPS: eligible=True
        if not eligible: continue
        d=x['selectedDirection']
        if prev is not None and d!=prev:return {'t':x['t'],'progress':x['progress'],'from':prev,'to':d,'absNet':x['absNetInventory']}
        prev=d
    return None

def max_same_run(snaps):
    started=False;last=None;run=0;best=0
    for x in snaps:
        if x['fills']>0:started=True
        if not started:continue
        d=x['selectedDirection']
        if d==last:run+=1
        else:last=d;run=1
        best=max(best,run)
    return best

def group(rows,label):
    if label=='positive':return [r for r in rows if r['pnl']>EPS]
    if label=='negative':return [r for r in rows if r['pnl']<-EPS]
    if label=='largeLoss':return [r for r in rows if r['pnl']<=-50.0]
    if label=='largeWin':return [r for r in rows if r['pnl']>=50.0]
    return []
def med(xs):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    n=len(ys);return ys[n//2] if n%2 else (ys[n//2-1]+ys[n//2])/2

def summarize_groups(rows):
    out={}
    for label in ('positive','negative','largeLoss','largeWin'):
        rs=group(rows,label);g={'markets':len(rs),'marketIds':[r['marketId'] for r in rs]}
        g['medianFirstMakerLossProgress']=med([None if r['events']['firstMakerHeadroomNonPositive'] is None else r['events']['firstMakerHeadroomNonPositive']['progress'] for r in rs])
        g['medianFirstActiveLossProgress']=med([None if r['events']['firstActiveHeadroomNonPositive'] is None else r['events']['firstActiveHeadroomNonPositive']['progress'] for r in rs])
        g['medianLastMakerSafeProgress']=med([None if r['events']['lastMakerHeadroomPositive'] is None else r['events']['lastMakerHeadroomPositive']['progress'] for r in rs])
        g['medianLastActiveSafeProgress']=med([None if r['events']['lastActiveHeadroomPositive'] is None else r['events']['lastActiveHeadroomPositive']['progress'] for r in rs])
        cps={}
        for cp in CHECKPOINTS:
            xs=[next((x for x in r['checkpoints'] if abs(x['checkpoint']-cp)<1e-9),None) for r in rs];xs=[x for x in xs if x]
            cps[str(cp)]={
              'medianAbsNet':med([x['absNetInventory'] for x in xs]),'medianGross':med([x['grossInventory'] for x in xs]),
              'medianMakerHeadroom':med([x['makerRepairHeadroom'] for x in xs]),'medianActiveHeadroom':med([x['activeRepairHeadroom'] for x in xs]),
              'makerSafeRate':(sum(1 for x in xs if x['makerRepairPairSafe'] is True)/len(xs) if xs else None),
              'activeSafeRate':(sum(1 for x in xs if x['activeRepairPairSafe'] is True)/len(xs) if xs else None),
              'medianAbsImbalance':med([abs(x['bookImbalance']) for x in xs]),
              'selectedDirectionWinnerAlignedRate':(sum(1 for x in xs if x['selectedDirection']==next(r['winner'] for r in rs if any(y is x for y in r['checkpoints'])))/len(xs) if False else None)
            }
        g['checkpoints']=cps;out[label]=g
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tmp=Path(tempfile.mkdtemp(prefix='mvps_pair1_anatomy24_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for i,mid in enumerate(MIDS,1):
            sim=LadderSim(tmp/'tapes'/f'{mid}.json.xz',1,True,False,False);snaps=[]
            try:
                updates=sorted(sim.payload['updates'],key=lambda u:(int(u[1]),int(u[0])));first=int(sim.meta['firstReceivedMs']);base.ex.advance_to(sim.bt,first);end=int((sim.payload.get('market') or {}).get('window_end_ms') or sim.meta['lastReceivedMs'])
                for u in updates:
                    t=int(u[1]);base.ex.advance_to(sim.bt,t);sim.process(t);sim.cancel_expired(t);sim._refresh_slots(t);base.apply(sim.book,u);qv=base.quotes(sim.book)
                    if qv:
                        snaps.append(snap(sim,t,first,end,qv));sim._open_free_slots(t,qv,end)
                end2=int(sim.meta['lastReceivedMs']);base.ex.advance_to(sim.bt,end2);sim.process(end2);sim._refresh_slots(end2)
                winner=str(co[mid]['winner']).upper();pnl=float(sim.inv.get(winner,0.0)-sim.cost);floor=float(min(sim.inv.values())-sim.cost);best=float(max(sim.inv.values())-sim.cost)
                cps=checkpoint_rows(snaps)
                # post-hoc outcome alignment fields added only after replay
                for x in cps:
                    x['selectedDirectionWinnerAlignedPostHoc']=x['selectedDirection']==winner
                    dom=x['dominantSide'];x['dominantSideWinnerAlignedPostHoc']=(dom==winner if dom else None)
                ev={'firstFill':({'t':int(sim.fillHist[0][0])} if sim.fillHist else None),
                    'firstMakerHeadroomNonPositive':first_nonpos(snaps,'makerRepairHeadroom'),'firstActiveHeadroomNonPositive':first_nonpos(snaps,'activeRepairHeadroom'),
                    'lastMakerHeadroomPositive':last_pos(snaps,'makerRepairHeadroom'),'lastActiveHeadroomPositive':last_pos(snaps,'activeRepairHeadroom'),
                    'firstSelectedDirectionReversalAfterAbsNet5':reversal_after_net5(snaps),'maxConsecutiveSameSelectedDirectionRunAfterFirstFill':max_same_run(snaps),
                    'grossCross':{str(th):first_cross(snaps,'grossInventory',th) for th in GROSS_THRESH},'absNetCross':{str(th):first_cross(snaps,'absNetInventory',th) for th in NET_THRESH}}
                row={'marketId':mid,'winner':winner,'pnl':pnl,'floor':floor,'best':best,'fills':int(sim.fills),'submits':int(sim.submits),'checkpoints':cps,'events':ev}
                rows.append(row);print(json.dumps({'idx':i,'marketId':mid,'winner':winner,'pnl':pnl,'firstMakerLoss':ev['firstMakerHeadroomNonPositive'],'firstActiveLoss':ev['firstActiveHeadroomNonPositive'],'absNet20':ev['absNetCross']['20']},ensure_ascii=False),flush=True)
            finally:sim.close()
        groups=summarize_groups(rows)
        # explicit aligned-rate postprocess
        for label in ('positive','negative','largeLoss','largeWin'):
            rs=group(rows,label)
            for cp in CHECKPOINTS:
                xs=[]
                for r in rs:
                    x=next((x for x in r['checkpoints'] if abs(x['checkpoint']-cp)<1e-9),None)
                    if x:xs.append(x)
                groups[label]['checkpoints'][str(cp)]['selectedDirectionWinnerAlignedRatePostHoc']=sum(1 for x in xs if x['selectedDirectionWinnerAlignedPostHoc'])/len(xs) if xs else None
                vals=[x['dominantSideWinnerAlignedPostHoc'] for x in xs if x['dominantSideWinnerAlignedPostHoc'] is not None]
                groups[label]['checkpoints'][str(cp)]['dominantSideWinnerAlignedRatePostHoc']=sum(1 for v in vals if v)/len(vals) if vals else None
        out={'version':'MVPS_PAIR1_STRICTPAST_WINNER_LOSER_TRAJECTORY_ANATOMY24_V1_20260908','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'groupSummary':groups,'boundary':['strict-past snapshots before Pair1 open decision','winner/final PnL added only post-hoc after replay','no selector/no threshold authority/no treatment','realistic HFT/no dream fill/no8781','consumed-development diagnostic']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'groups':groups},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
