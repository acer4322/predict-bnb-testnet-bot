from __future__ import annotations
import json,lzma,importlib.util,sys
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_upside_retention_after_durable_base_v1.py'
spec=importlib.util.spec_from_file_location('u',P);u=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=u;spec.loader.exec_module(u)
OUT=ROOT/'data/research/r4_v0/hourly';TZ=ZoneInfo('Asia/Taipei')
STRESSES=u.STRESSES;FROZEN=u.FROZEN

def md(x):return float(median(x)) if x else None

def durable15_now(times,floors,i):
    if i<1 or floors[i]<=0:return False
    t0=times[i];deadline=t0+15000
    j=i
    while j+1<len(times) and times[j+1]<deadline:
        if floors[j]<=0:return False
        j+=1
    return times[-1]>=deadline and floors[j]>0

def replay(ev,mode):
    # mode: baseline | tranche | credit
    s=(0.,0.,0.,0.);states=[];times=[];mods=0
    durable=False;durable_idx=None;credit=0.;credit_used=0.;postweak=0.
    for z in ev:
        pre=u.geom(s);q=float(z['sh']);postfull=u.geom(u.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor'])
        flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
        sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
        weak='DOWN' if sur=='UP' else 'UP' if sur=='DOWN' else None
        if mode!='baseline' and flag:
            rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT');q0=q
            if rel=='SURPLUS_SIDE':
                baseq=u.safe_qty_zero(s,z,q)
                q=baseq
                if mode=='credit' and durable:
                    extra=max(0.,q0-baseq);avail=max(0.,credit-credit_used);add=min(extra,avail);q+=add;credit_used+=add
            else:q=0.
            mods+=int(q<q0-1e-9)
        s=u.apply(s,z,q);states.append(u.geom(s));times.append(int(z['t']))
        # Detect first 15s durable base using only realized history to date; this becomes knowable once 15s has elapsed.
        if not durable and len(times)>=2:
            for k in range(len(times)):
                t0=times[k]
                if times[-1]-t0<15000 or states[k]['floor']<=0:continue
                ok=True
                for j in range(k,len(times)):
                    if times[j]-t0>15000:break
                    if states[j]['floor']<=0:ok=False;break
                if ok:
                    durable=True;durable_idx=k;credit=0.;credit_used=0.;break
        if mode=='credit' and durable and weak and z['role']=='MAKER' and z['side']==weak and q>0:
            credit+=q;postweak+=q
    floors=[x['floor'] for x in states];di=u.durable_first_index(times,floors,15)
    if di is None:return {**u.geom(s),'durable15':False,'modifiedEvents':mods,'postPeakUpside':None,'postTerminalUpside':None,'postRelapse':None,'postReserveSpend':None,'creditEarned':credit,'creditUsed':credit_used,'postWeakMakerShares':postweak}
    post=states[di:];spend=sum(max(0.,a['floor']-b['floor']) for a,b in zip(post[:-1],post[1:]))
    return {**u.geom(s),'durable15':True,'modifiedEvents':mods,'postPeakUpside':max(x['upside'] for x in post),'postTerminalUpside':post[-1]['upside'],'postRelapse':any(x['floor']<=0 for x in post[1:]),'postReserveSpend':spend,'creditEarned':credit,'creditUsed':credit_used,'postWeakMakerShares':postweak}

def summarize(records,stress):
    rows=[]
    for mid,ev0 in records:
        ev=u.stress_stream(ev0,stress)
        if not ev or not u.has_break(ev):continue
        b=replay(ev,'baseline');t=replay(ev,'tranche');c=replay(ev,'credit')
        if t['modifiedEvents']<=0:continue
        rows.append((mid,b,t,c))
    both=[x for x in rows if x[1]['durable15'] and x[2]['durable15'] and x[3]['durable15']]
    def rr(idx,key):return md([x[idx][key] for x in both])
    br=sum(x[1]['postRelapse'] for x in both)/len(both) if both else None
    tr=sum(x[2]['postRelapse'] for x in both)/len(both) if both else None
    cr=sum(x[3]['postRelapse'] for x in both)/len(both) if both else None
    tp,cp=rr(2,'postPeakUpside'),rr(3,'postPeakUpside');tt,ct=rr(2,'postTerminalUpside'),rr(3,'postTerminalUpside')
    out={'activeHistories':len(rows),'tripleDurable15Histories':len(both),'baselinePeakUpside':rr(1,'postPeakUpside'),'tranchePeakUpside':tp,'creditPeakUpside':cp,'creditPeakVsTranche':u.ratio(cp,tp),'baselineTerminalUpside':rr(1,'postTerminalUpside'),'trancheTerminalUpside':tt,'creditTerminalUpside':ct,'creditTerminalVsTranche':u.ratio(ct,tt),'baselineRelapse':br,'trancheRelapse':tr,'creditRelapse':cr,'trancheReserveSpend':rr(2,'postReserveSpend'),'creditReserveSpend':rr(3,'postReserveSpend'),'medianCreditEarned':rr(3,'creditEarned'),'medianCreditUsed':rr(3,'creditUsed')}
    # preregistered: >=10 support; improve either peak or terminal upside vs frozen tranche, without increasing relapse or reserve spend median.
    out['pass']=bool(len(both)>=10 and ((out['creditPeakVsTranche'] or 0)>1.0 or (out['creditTerminalVsTranche'] or 0)>1.0) and cr is not None and tr is not None and cr<=tr+1e-12 and out['creditReserveSpend'] is not None and out['trancheReserveSpend'] is not None and out['creditReserveSpend']<=out['trancheReserveSpend']+1e-12)
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
    results={s:summarize(records,s) for s in STRESSES};eligible=[s for s in STRESSES[1:] if results[s]['tripleDurable15Histories']>=10]
    keep=bool(results['NONE']['pass'] and len(eligible)>=2 and sum(results[s]['pass'] for s in eligible)>=2)
    status='KEEP_SIGNAL' if keep else 'REJECTED'
    now=datetime.now(TZ);rep={'version':'R4_LOCK_BASE_REEXPAND_CREDIT_V1','createdAt':now.isoformat(),'candidate':'After first 15s durable base is observable, surplus-side BASE_BREAK quantity may exceed the frozen floor=0 tranche only by strict-past confirmed weak-side Maker shares accrued after durable formation; credit is 1 share per realized weak-side Maker share and is consumed once. WEAK_SIDE_CROSS remains hard-protected.','results':results,'eligibleStressVariants':eligible,'status':status,'gate':'Normal and >=2 eligible stress variants must improve peak or terminal upside versus frozen tranche without increasing relapse or median post-base reserve spend. No threshold sweep.','guards':{'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'echtgeldUsedForStressDimensionsOnly':True,'noEchtgeldTraining':True,'noDreamFill':True,'strictPastCreditOnly':True,'futureOutcomeRuntimeInput':False,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True}}
    path=OUT/f"r4_lock_base_reexpand_credit_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
