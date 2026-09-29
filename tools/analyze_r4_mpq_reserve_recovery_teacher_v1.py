from __future__ import annotations
import json, importlib.util, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'analyze_r4_marginal_pair_quality_horizon_v1.py'
spec=importlib.util.spec_from_file_location('r4_mpq_h_v1_rr',P); h=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=h; spec.loader.exec_module(h)
m=h.m
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_MPQ_RESERVE_RECOVERY_TEACHER_AUDIT_V1'
H=[5000,15000,30000,60000]

def apply(state,z): return h.apply(state,z)
def geom(state): return h.g(state)

def main():
    meta,ev=m.load(m.N_MARKETS)
    recs=[]
    for mid,wend,w in meta:
        events=ev.get(mid,[]); state=(0.,0.,0.,0.)
        for i,z in enumerate(events):
            pre=geom(state); post_state=apply(state,z); post=geom(post_state)
            reserve=max(0.,pre['floor']-post['floor'])
            flag=pre['floor']>0 and reserve>1e-12 and post['edge']<0
            if flag:
                surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
                relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
                s=post_state; t0=int(z['t']); first_rec=None; peak60=post['floor']; min60=post['floor']
                rec_by={q:False for q in H}
                for j in range(i+1,len(events)):
                    zz=events[j]; s=apply(s,zz); gg=geom(s); dt=int(zz['t'])-t0
                    if dt<=60000:
                        peak60=max(peak60,gg['floor']); min60=min(min60,gg['floor'])
                    if first_rec is None and gg['floor']>0:
                        first_rec=dt
                    for q in H:
                        if dt<=q and gg['floor']>0: rec_by[q]=True
                b=h.path_from(state,events,i,True,None); cf=h.path_from(state,events,i,False,None)
                pnl_b=(b['up'] if w=='UP' else b['down'])-b['cost']
                pnl_cf=(cf['up'] if w=='UP' else cf['down'])-cf['cost']
                keep_pnl_adv=pnl_b-pnl_cf
                recs.append({
                    'marketId':mid,'windowEndMs':wend,'eventIndex':i,'role':z['role'],'side':z['side'],'relation':relation,
                    'price':z['px'],'shares':z['sh'],'preFloor':pre['floor'],'postFloor':post['floor'],'reserveSpent':reserve,
                    'preEdge':pre['edge'],'postEdge':post['edge'],'preCoverage':pre['coverage'],'preAbsNet':pre['absnet'],
                    'firstPositiveRecoveryMs':first_rec,'recovered5s':rec_by[5000],'recovered15s':rec_by[15000],
                    'recovered30s':rec_by[30000],'recovered60s':rec_by[60000],'peakFloor60s':peak60,'minFloor60s':min60,
                    'keepPnlAdvantageFinal':keep_pnl_adv,'keepPnlBetter':keep_pnl_adv>1e-9,
                    'recover60AndKeepPnlBetter':bool(rec_by[60000] and keep_pnl_adv>1e-9),
                })
            state=post_state
    def cnt(key): return sum(bool(r[key]) for r in recs)
    rr=[r['firstPositiveRecoveryMs']/1000 for r in recs if r['firstPositiveRecoveryMs'] is not None]
    byrel={}
    for rel in sorted(set(r['relation'] for r in recs)):
        a=[r for r in recs if r['relation']==rel]
        byrel[rel]={
            'n':len(a),'recover5s':sum(r['recovered5s'] for r in a),'recover15s':sum(r['recovered15s'] for r in a),
            'recover30s':sum(r['recovered30s'] for r in a),'recover60s':sum(r['recovered60s'] for r in a),
            'keepPnlBetter':sum(r['keepPnlBetter'] for r in a),'recover60AndKeepPnlBetter':sum(r['recover60AndKeepPnlBetter'] for r in a),
        }
    report={
        'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),
        'cohort':{'ordinaryMarkets':len(meta),'mpqFlaggedEvents':len(recs),'sealed20260816':True},
        'recovery':{'recover5s':cnt('recovered5s'),'recover15s':cnt('recovered15s'),'recover30s':cnt('recovered30s'),'recover60s':cnt('recovered60s'),
                    'everRecovered':sum(r['firstPositiveRecoveryMs'] is not None for r in recs),'medianFirstRecoverySec':median(rr) if rr else None},
        'economics':{'keepPnlBetter':cnt('keepPnlBetter'),'recover60AndKeepPnlBetter':cnt('recover60AndKeepPnlBetter')},
        'byRelation':byrel,'records':recs,
        'interpretation':'DESCRIPTIVE_TEACHER_LABEL_AUDIT_ONLY_NO_RUNTIME_AUTHORITY',
        'guards':{'winnerUsedOnlyForOfflinePnlLabel':True,'noThresholdTuning':True,'noEchtgeldTraining':True}
    }
    out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_mpq_reserve_recovery_teacher_audit_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'cohort':report['cohort'],'recovery':report['recovery'],'economics':report['economics'],'byRelation':byrel},ensure_ascii=False))

if __name__=='__main__': main()
