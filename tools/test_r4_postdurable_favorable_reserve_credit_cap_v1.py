from __future__ import annotations
import json,lzma,importlib.util,sys
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_upside_retention_after_durable_base_v1.py'
spec=importlib.util.spec_from_file_location('u_postdur',P);u=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=u;spec.loader.exec_module(u)
OUT=ROOT/'data/research/r4_v0/hourly';TZ=ZoneInfo('Asia/Taipei');FROZEN=u.FROZEN;STRESSES=u.STRESSES

def md(x): return float(median(x)) if x else None

def ratio(a,b):
    if a is None or b is None:return None
    if abs(b)<1e-12:return 1.0 if abs(a)<1e-12 else (999.0 if a>0 else -999.0)
    return float(a/b)

def replay(ev,mode):
    # mode tranche = frozen relation-aware floor=0 control only
    # mode credit = same tranche + post-durable 1:1 realized weak-side credit cap on ALL favorable-side reserve-spend fills
    s=(0.,0.,0.,0.);states=[];times=[];mods=0;credit=0.;used=0.;support=0;credit_capped=0
    positive_since=None;durable=False;durable_at=None
    for z0 in ev:
        z=dict(z0);t=int(z['t']);pre=u.geom(s);q=float(z['sh']);q0=q
        # frozen relation-aware tranche first
        postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        weak='DOWN' if sur=='UP' else 'UP' if sur=='DOWN' else None
        if flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT')
            q=u.safe_qty_zero(s,z,q) if rel=='SURPLUS_SIDE' else 0.
            mods+=int(q<q0-1e-9)
        # strict-past durable observability: current positive spell has already lasted >=15s before this event
        if not durable and positive_since is not None and pre['floor']>0 and t-positive_since>=15000:
            durable=True;durable_at=t;credit=0.;used=0.
        # earned credit comes only from previously confirmed weak-side Maker fills after durable is observable
        avail=max(0.,credit-used)
        post_candidate=u.geom(u.apply(s,z,q))
        favorable_spend=bool(durable and sur!='FLAT' and z['side']==sur and q>1e-12 and pre['floor']>0 and post_candidate['floor']<pre['floor']-1e-12)
        if favorable_spend:
            support+=1
            if mode=='credit':
                q_before=q;q=min(q,avail);used+=q
                if q<q_before-1e-9:credit_capped+=1
        s=u.apply(s,z,q);g=u.geom(s);states.append(g);times.append(t)
        # update credit only after applying this event, so current fill cannot fund itself
        if mode=='credit' and durable and weak and z['role']=='MAKER' and z['side']==weak and q>1e-12:
            credit+=q
        # update positive spell after realized state
        if g['floor']>0:
            if positive_since is None: positive_since=t
        else:
            positive_since=None
    floors=[x['floor'] for x in states];di=u.durable_first_index(times,floors,15)
    final=u.geom(s)
    if di is None:
        return {**final,'durable15':False,'supportEvents':support,'creditCappedEvents':credit_capped,'creditEarned':credit,'creditUsed':used,
                'postPeakUpside':None,'postTerminalUpside':None,'postReserveSpend':None,'postRelapse':None,'positiveDuration':0.0,'maxDrawdown':None}
    post=states[di:];posttimes=times[di:]
    spend=sum(max(0.,a['floor']-b['floor']) for a,b in zip(post[:-1],post[1:]))
    relapse=any(x['floor']<=0 for x in post[1:])
    posdur=0.0
    for i in range(di,len(states)-1):
        if states[i]['floor']>0: posdur+=(times[i+1]-times[i])/1000.0
    peak=max(x['floor'] for x in post);dd=max((peak-x['floor'] for x in post),default=0.0)
    return {**final,'durable15':True,'supportEvents':support,'creditCappedEvents':credit_capped,'creditEarned':credit,'creditUsed':used,
            'postPeakUpside':max(x['upside'] for x in post),'postTerminalUpside':post[-1]['upside'],'postReserveSpend':spend,'postRelapse':relapse,
            'positiveDuration':posdur,'maxDrawdown':dd}

def summarize(records,stress):
    rows=[]
    for mid,ev0 in records:
        ev=u.stress_stream(ev0,stress)
        if not ev or not u.has_break(ev):continue
        t=replay(ev,'tranche');c=replay(ev,'credit')
        if t['supportEvents']<=0:continue
        rows.append((mid,t,c))
    both=[x for x in rows if x[1]['durable15'] and x[2]['durable15']]
    tp=md([x[1]['postPeakUpside'] for x in both]);cp=md([x[2]['postPeakUpside'] for x in both]);tt=md([x[1]['postTerminalUpside'] for x in both]);ct=md([x[2]['postTerminalUpside'] for x in both])
    tr=sum(bool(x[1]['postRelapse']) for x in both)/len(both) if both else None;cr=sum(bool(x[2]['postRelapse']) for x in both)/len(both) if both else None
    tff=md([x[1]['floor'] for x in rows]);cff=md([x[2]['floor'] for x in rows]);td=md([x[1]['positiveDuration'] for x in both]);cd=md([x[2]['positiveDuration'] for x in both])
    out={'supportHistories':len(rows),'bothDurable15Histories':len(both),'supportEvents':sum(x[1]['supportEvents'] for x in rows),'historiesCreditCapped':sum(x[2]['creditCappedEvents']>0 for x in rows),'creditCappedEvents':sum(x[2]['creditCappedEvents'] for x in rows),
         'trancheMedianFinalFloor':tff,'creditMedianFinalFloor':cff,'deltaMedianFinalFloor':None if tff is None or cff is None else cff-tff,
         'trancheMedianPositiveDurationSec':td,'creditMedianPositiveDurationSec':cd,'deltaMedianPositiveDurationSec':None if td is None or cd is None else cd-td,
         'peakUpsideRetention':ratio(cp,tp),'terminalUpsideRetention':ratio(ct,tt),'trancheRelapseRate':tr,'creditRelapseRate':cr,
         'trancheMedianReserveSpend':md([x[1]['postReserveSpend'] for x in both]),'creditMedianReserveSpend':md([x[2]['postReserveSpend'] for x in both]),
         'trancheMedianMaxDrawdown':md([x[1]['maxDrawdown'] for x in both]),'creditMedianMaxDrawdown':md([x[2]['maxDrawdown'] for x in both])}
    # preregistered, untuned: enough support, preserve >=80% peak+terminal upside, no higher relapse, and non-worse median final floor.
    out['pass']=bool(len(rows)>=10 and len(both)>=10 and out['peakUpsideRetention'] is not None and out['peakUpsideRetention']>=0.80 and out['terminalUpsideRetention'] is not None and out['terminalUpsideRetention']>=0.80 and tr is not None and cr is not None and cr<=tr+1e-12 and out['deltaMedianFinalFloor'] is not None and out['deltaMedianFinalFloor']>=-1e-12)
    return out

def main():
    audit=json.loads((OUT/'r4_hft_base_break_support_audit_v1_20260826_171833.json').read_text(encoding='utf-8'));records=[]
    for h in audit.get('supportedHistories',[]):
        p=ROOT/str(h['file'])
        try:
            with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        except Exception:continue
        mid=int(d.get('marketId') or 0)
        if mid in FROZEN:continue
        ev=u.events(d)
        if ev:records.append((mid,ev))
    results={s:summarize(records,s) for s in STRESSES};eligible=[s for s in STRESSES[1:] if results[s]['supportHistories']>=10 and results[s]['bothDurable15Histories']>=10]
    keep=bool(results['NONE']['pass'] and len(eligible)>=2 and sum(results[s]['pass'] for s in eligible)>=2)
    status='KEEP_SIGNAL' if keep else ('INCONCLUSIVE' if results['NONE']['supportHistories']<10 else 'REJECTED')
    now=datetime.now(TZ);rep={'version':'R4_POSTDURABLE_FAVORABLE_RESERVE_CREDIT_CAP_V1','createdAt':now.isoformat(),'candidate':'Keep frozen relation-aware floor=0 tranche. Once a 15s positive base is strictly observable, every favorable/surplus-side fill that would spend floor reserve is additionally limited by unused 1:1 strict-past confirmed weak-side Maker-share credit earned after durable observability. Current fill cannot fund itself.','results':results,'eligibleStressVariants':eligible,'status':status,'gate':'Normal + >=2 eligible execution stresses must have >=10 support histories, retain >=80% median post-durable peak and terminal upside, not increase relapse, and not worsen median final floor. No threshold sweep.','guards':{'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'echtgeldStressDimensionsOnly':True,'noEchtgeldTraining':True,'noDreamFill':True,'strictPastCreditOnly':True,'futureOutcomeRuntimeInput':False,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True},'note':'This broadens the prior zero-support credit test from MPQ BASE_BREAK-only opportunities to all post-durable favorable-side reserve-spend opportunities while freezing the same 1:1 credit semantics and floor=0 tranche.'}
    path=OUT/f"r4_postdurable_favorable_reserve_credit_cap_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
