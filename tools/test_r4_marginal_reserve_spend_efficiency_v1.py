from __future__ import annotations
import json,lzma,importlib.util,sys
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_upside_retention_after_durable_base_v1.py'
spec=importlib.util.spec_from_file_location('u_mrse',P);u=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=u;spec.loader.exec_module(u)
OUT=ROOT/'data/research/r4_v0/hourly';TZ=ZoneInfo('Asia/Taipei');FROZEN=u.FROZEN;STRESSES=u.STRESSES

def md(x): return float(median(x)) if x else None

def ratio(a,b):
    if a is None or b is None:return None
    if abs(b)<1e-12:return 1.0 if abs(a)<1e-12 else (999.0 if a>0 else -999.0)
    return float(a/b)

def replay(ev,mode):
    # mode tranche = frozen relation-aware floor=0 control only
    # mode efficiency = same tranche + post-durable favorable reserve-spend must have immediate dUpside/reserveSpend >= 1.0
    s=(0.,0.,0.,0.);states=[];times=[];support=0;blocked=0;positive_since=None;durable=False
    eff_values=[]
    for z0 in ev:
        z=dict(z0);t=int(z['t']);pre=u.geom(s);q=float(z['sh']);q0=q
        postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        if flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT')
            q=u.safe_qty_zero(s,z,q) if rel=='SURPLUS_SIDE' else 0.
        # strict-past durable observability before current event
        if not durable and positive_since is not None and pre['floor']>0 and t-positive_since>=15000:
            durable=True
        post_candidate=u.geom(u.apply(s,z,q))
        reserve2=max(0.,pre['floor']-post_candidate['floor'])
        dups=max(0.,post_candidate['upside']-pre['upside'])
        favorable_spend=bool(durable and sur!='FLAT' and z['side']==sur and q>1e-12 and pre['floor']>0 and reserve2>1e-12)
        if favorable_spend:
            support+=1
            eff=dups/reserve2 if reserve2>1e-12 else 999.0
            eff_values.append(float(eff))
            if mode=='efficiency' and eff < 1.0-1e-12:
                q=0.0;blocked+=1
        s=u.apply(s,z,q);g=u.geom(s);states.append(g);times.append(t)
        if g['floor']>0:
            if positive_since is None:positive_since=t
        else:positive_since=None
    floors=[x['floor'] for x in states];di=u.durable_first_index(times,floors,15);final=u.geom(s)
    if di is None:
        return {**final,'durable15':False,'supportEvents':support,'blockedEvents':blocked,'efficiencies':eff_values,
                'postPeakUpside':None,'postTerminalUpside':None,'postReserveSpend':None,'postRelapse':None,'positiveDuration':0.0,'maxDrawdown':None}
    post=states[di:]
    spend=sum(max(0.,a['floor']-b['floor']) for a,b in zip(post[:-1],post[1:]))
    relapse=any(x['floor']<=0 for x in post[1:])
    posdur=0.0
    for i in range(di,len(states)-1):
        if states[i]['floor']>0:posdur+=(times[i+1]-times[i])/1000.0
    peak=max(x['floor'] for x in post);dd=max((peak-x['floor'] for x in post),default=0.0)
    return {**final,'durable15':True,'supportEvents':support,'blockedEvents':blocked,'efficiencies':eff_values,
            'postPeakUpside':max(x['upside'] for x in post),'postTerminalUpside':post[-1]['upside'],'postReserveSpend':spend,
            'postRelapse':relapse,'positiveDuration':posdur,'maxDrawdown':dd}

def summarize(records,stress):
    rows=[]
    for mid,ev0 in records:
        ev=u.stress_stream(ev0,stress)
        if not ev or not u.has_break(ev):continue
        t=replay(ev,'tranche');e=replay(ev,'efficiency')
        if t['supportEvents']<=0:continue
        rows.append((mid,t,e))
    both=[x for x in rows if x[1]['durable15'] and x[2]['durable15']]
    tp=md([x[1]['postPeakUpside'] for x in both]);ep=md([x[2]['postPeakUpside'] for x in both]);tt=md([x[1]['postTerminalUpside'] for x in both]);et=md([x[2]['postTerminalUpside'] for x in both])
    tr=sum(bool(x[1]['postRelapse']) for x in both)/len(both) if both else None;er=sum(bool(x[2]['postRelapse']) for x in both)/len(both) if both else None
    tff=md([x[1]['floor'] for x in rows]);eff=md([x[2]['floor'] for x in rows]);td=md([x[1]['positiveDuration'] for x in both]);ed=md([x[2]['positiveDuration'] for x in both])
    all_eff=[v for _,t,_ in rows for v in t['efficiencies']]
    out={'supportHistories':len(rows),'bothDurable15Histories':len(both),'supportEvents':sum(x[1]['supportEvents'] for x in rows),'historiesBlocked':sum(x[2]['blockedEvents']>0 for x in rows),'blockedEvents':sum(x[2]['blockedEvents'] for x in rows),
         'medianObservedEfficiency':md(all_eff),'fractionEfficiencyBelow1':(sum(v<1.0 for v in all_eff)/len(all_eff) if all_eff else None),
         'trancheMedianFinalFloor':tff,'effMedianFinalFloor':eff,'deltaMedianFinalFloor':None if tff is None or eff is None else eff-tff,
         'trancheMedianPositiveDurationSec':td,'effMedianPositiveDurationSec':ed,'deltaMedianPositiveDurationSec':None if td is None or ed is None else ed-td,
         'peakUpsideRetention':ratio(ep,tp),'terminalUpsideRetention':ratio(et,tt),'trancheRelapseRate':tr,'effRelapseRate':er,
         'trancheMedianReserveSpend':md([x[1]['postReserveSpend'] for x in both]),'effMedianReserveSpend':md([x[2]['postReserveSpend'] for x in both]),
         'trancheMedianMaxDrawdown':md([x[1]['maxDrawdown'] for x in both]),'effMedianMaxDrawdown':md([x[2]['maxDrawdown'] for x in both])}
    out['pass']=bool(len(rows)>=10 and len(both)>=10 and out['peakUpsideRetention'] is not None and out['peakUpsideRetention']>=0.80 and out['terminalUpsideRetention'] is not None and out['terminalUpsideRetention']>=0.80 and tr is not None and er is not None and er<=tr+1e-12 and out['deltaMedianFinalFloor'] is not None and out['deltaMedianFinalFloor']>=-1e-12)
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
    now=datetime.now(TZ);rep={'version':'R4_MARGINAL_RESERVE_SPEND_EFFICIENCY_V1','createdAt':now.isoformat(),'candidate':'Keep frozen relation-aware floor=0 tranche. After a 15s durable base is strictly observable, favorable/surplus-side fills that spend positive floor reserve are allowed only when immediate strict-past-computable marginal favorable-upside gain divided by reserve spent is >=1.0. Economic boundary 1.0 means at least one unit of favorable upside per unit of downside reserve spent.','results':results,'eligibleStressVariants':eligible,'status':status,'gate':'Normal + >=2 eligible execution stresses must have >=10 support histories, retain >=80% median post-durable peak and terminal upside, not increase relapse, and not worsen median final floor. No threshold sweep.','guards':{'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'echtgeldStressDimensionsOnly':True,'noEchtgeldTraining':True,'noDreamFill':True,'strictPastEconomicsOnly':True,'futureOutcomeRuntimeInput':False,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True},'note':'Distinct from rejected 1:1 share-credit cap: this uses dollar economic efficiency of each reserve-spending action, not realized weak-side share quantity credit.'}
    path=OUT/f"r4_marginal_reserve_spend_efficiency_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'eligibleStressVariants':eligible,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
