from __future__ import annotations
import json,lzma,importlib.util,sys
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_upside_retention_after_durable_base_v1.py'
spec=importlib.util.spec_from_file_location('u_rrd',P);u=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=u;spec.loader.exec_module(u)
OUT=ROOT/'data/research/r4_v0/hourly';TZ=ZoneInfo('Asia/Taipei');FROZEN=u.FROZEN;STRESSES=u.STRESSES

def md(x): return float(median(x)) if x else None

def ratio(a,b):
    if a is None or b is None:return None
    if abs(b)<1e-12:return 1.0 if abs(a)<1e-12 else (999.0 if a>0 else -999.0)
    return float(a/b)

def cap_qty_for_solvency(state,z,q,debt):
    # Natural 1:1 solvency boundary: after current favorable reserve spend,
    # unrepaid rolling debt may not exceed remaining positive floor.
    pre=u.geom(state)
    def ok(qq):
        post=u.geom(u.apply(state,z,qq))
        spend=max(0.,pre['floor']-post['floor'])
        return debt+spend <= max(0.,post['floor']) + 1e-12
    if ok(q): return q
    lo,hi=0.0,q
    for _ in range(50):
        m=(lo+hi)/2
        if ok(m):lo=m
        else:hi=m
    return lo

def replay(ev,mode):
    # mode tranche = frozen relation-aware floor=0 control only.
    # mode debt = same tranche + rolling reserve debt solvency guard.
    s=(0.,0.,0.,0.);states=[];times=[];positive_since=None;durable=False
    debt=0.0;support=0;capped=0;debt_samples=[];repay_events=0
    for z0 in ev:
        z=dict(z0);t=int(z['t']);pre=u.geom(s);q=float(z['sh'])
        postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        if flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT')
            q=u.safe_qty_zero(s,z,q) if rel=='SURPLUS_SIDE' else 0.
        # Durable observability is strict-past relative to this event.
        if not durable and positive_since is not None and pre['floor']>0 and t-positive_since>=15000:
            durable=True;debt=0.0
        post_candidate=u.geom(u.apply(s,z,q))
        spend=max(0.,pre['floor']-post_candidate['floor'])
        favorable=bool(durable and sur!='FLAT' and z['side']==sur and q>1e-12 and pre['floor']>0 and spend>1e-12)
        if favorable:
            support+=1
            if mode=='debt':
                q2=cap_qty_for_solvency(s,z,q,debt)
                if q2 < q-1e-9:
                    q=q2;capped+=1
                    post_candidate=u.geom(u.apply(s,z,q));spend=max(0.,pre['floor']-post_candidate['floor'])
                debt += spend
        # Apply confirmed fill. Only AFTER realization can weak-side floor deepening repay debt.
        old_sur=sur
        s=u.apply(s,z,q);g=u.geom(s)
        if mode=='debt' and durable and old_sur!='FLAT' and z['side']!=old_sur and g['floor']>pre['floor']+1e-12:
            repay=min(debt,g['floor']-pre['floor']);debt-=repay
            if repay>1e-12:repay_events+=1
        debt_samples.append(debt)
        states.append(g);times.append(t)
        if g['floor']>0:
            if positive_since is None:positive_since=t
        else:positive_since=None
    floors=[x['floor'] for x in states];di=u.durable_first_index(times,floors,15);final=u.geom(s)
    if di is None:
        return {**final,'durable15':False,'supportEvents':support,'cappedEvents':capped,'repayEvents':repay_events,'maxDebt':max(debt_samples,default=0.0),'terminalDebt':debt,
                'postPeakUpside':None,'postTerminalUpside':None,'postReserveSpend':None,'postRelapse':None,'positiveDuration':0.0,'maxDrawdown':None}
    post=states[di:]
    spendtot=sum(max(0.,a['floor']-b['floor']) for a,b in zip(post[:-1],post[1:]))
    relapse=any(x['floor']<=0 for x in post[1:])
    posdur=0.0
    for i in range(di,len(states)-1):
        if states[i]['floor']>0:posdur+=(times[i+1]-times[i])/1000.0
    peak=max(x['floor'] for x in post);dd=max((peak-x['floor'] for x in post),default=0.0)
    return {**final,'durable15':True,'supportEvents':support,'cappedEvents':capped,'repayEvents':repay_events,'maxDebt':max(debt_samples,default=0.0),'terminalDebt':debt,
            'postPeakUpside':max(x['upside'] for x in post),'postTerminalUpside':post[-1]['upside'],'postReserveSpend':spendtot,
            'postRelapse':relapse,'positiveDuration':posdur,'maxDrawdown':dd}

def summarize(records,stress):
    rows=[]
    for mid,ev0 in records:
        ev=u.stress_stream(ev0,stress)
        if not ev or not u.has_break(ev):continue
        t=replay(ev,'tranche');d=replay(ev,'debt')
        if t['supportEvents']<=0:continue
        rows.append((mid,t,d))
    both=[x for x in rows if x[1]['durable15'] and x[2]['durable15']]
    tp=md([x[1]['postPeakUpside'] for x in both]);dp=md([x[2]['postPeakUpside'] for x in both]);tt=md([x[1]['postTerminalUpside'] for x in both]);dt=md([x[2]['postTerminalUpside'] for x in both])
    tr=sum(bool(x[1]['postRelapse']) for x in both)/len(both) if both else None;dr=sum(bool(x[2]['postRelapse']) for x in both)/len(both) if both else None
    tf=md([x[1]['floor'] for x in rows]);df=md([x[2]['floor'] for x in rows]);td=md([x[1]['positiveDuration'] for x in both]);dd=md([x[2]['positiveDuration'] for x in both])
    out={'supportHistories':len(rows),'bothDurable15Histories':len(both),'supportEvents':sum(x[1]['supportEvents'] for x in rows),
         'historiesCapped':sum(x[2]['cappedEvents']>0 for x in rows),'cappedEvents':sum(x[2]['cappedEvents'] for x in rows),'repayEvents':sum(x[2]['repayEvents'] for x in rows),
         'medianMaxDebt':md([x[2]['maxDebt'] for x in rows]),'medianTerminalDebt':md([x[2]['terminalDebt'] for x in rows]),
         'trancheMedianFinalFloor':tf,'debtMedianFinalFloor':df,'deltaMedianFinalFloor':None if tf is None or df is None else df-tf,
         'trancheMedianPositiveDurationSec':td,'debtMedianPositiveDurationSec':dd,'deltaMedianPositiveDurationSec':None if td is None or dd is None else dd-td,
         'peakUpsideRetention':ratio(dp,tp),'terminalUpsideRetention':ratio(dt,tt),'trancheRelapseRate':tr,'debtRelapseRate':dr,
         'trancheMedianReserveSpend':md([x[1]['postReserveSpend'] for x in both]),'debtMedianReserveSpend':md([x[2]['postReserveSpend'] for x in both]),
         'trancheMedianMaxDrawdown':md([x[1]['maxDrawdown'] for x in both]),'debtMedianMaxDrawdown':md([x[2]['maxDrawdown'] for x in both])}
    out['pass']=bool(len(rows)>=10 and len(both)>=10 and out['peakUpsideRetention'] is not None and out['peakUpsideRetention']>=0.80 and out['terminalUpsideRetention'] is not None and out['terminalUpsideRetention']>=0.80 and tr is not None and dr is not None and dr<=tr+1e-12 and out['deltaMedianFinalFloor'] is not None and out['deltaMedianFinalFloor']>=-1e-12)
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
    status='KEEP_SIGNAL' if keep else ('INCONCLUSIVE' if results['NONE']['supportHistories']<10 or results['NONE']['historiesCapped']==0 else 'REJECTED')
    now=datetime.now(TZ);rep={'version':'R4_ROLLING_RESERVE_DEBT_GUARD_V1','createdAt':now.isoformat(),
      'candidate':'Keep frozen relation-aware floor=0 tranche. After a strictly observable 15s durable base, accumulate rolling reserve debt from confirmed favorable/surplus-side floor-spending fills. Confirmed weak-side fills repay debt dollar-for-dollar only by their realized floor improvement. Cap the current favorable fill only when resulting unrepaid debt would exceed remaining positive floor; solve quantity to the exact debt==remaining-floor boundary. No sweep.',
      'results':results,'eligibleStressVariants':eligible,'status':status,
      'gate':'Normal + >=2 eligible execution stresses must have >=10 support histories, retain >=80% median post-durable peak and terminal upside, not increase relapse, and not worsen median final floor. No threshold sweep.',
      'guards':{'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'echtgeldStressDimensionsOnly':True,'noEchtgeldTraining':True,'noDreamFill':True,'strictPastDebtState':True,'futureOutcomeRuntimeInput':False,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True},
      'note':'Distinct from rejected per-fill efficiency and 1:1 share-credit gates: this is a path-dependent debt-amortization state using realized floor dollars and sequence history.'}
    path=OUT/f"r4_rolling_reserve_debt_guard_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'eligibleStressVariants':eligible,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
