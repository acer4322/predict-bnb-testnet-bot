from __future__ import annotations
import json, sqlite3, joblib, numpy as np
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
sys.path.insert(0,str((ROOT/'tools').resolve()))
import replay_r3_formation_dream_sync_v0 as core
import train_r3_r21_context_adapter_v2 as ctx
import train_r3_r21_context_sequence_adapter_v3 as seqctx
import train_r3_r21_context_sequence_adapter_v3_2 as coopvec
CFG=dict(core.CFG)
COOP=joblib.load(R/'r3_r21_context_sequence_adapter_hgb_v3_2.joblib')['model']
SEQ_ORDER=['stall_then_late_fill','cancel_partial_then_ack','unknown_then_fill','out_of_order_after_terminal','target_revision_then_fill','partial_then_cancel_ack']
def build_snaps(c,mid):
    rows=ctx.seq.build_market(c,mid)
    if not rows:return []
    out=[]
    for m,t,x,y in rows:
        f=dict(zip(ctx.seq.FEATURES,x)); X=np.array([[float(f[k]) for k in ctx.EFEATURES]],float)
        pb=float(ctx.ARB.predict_proba(X)[0,1]); pc=float(ctx.CROSS.predict_proba(X)[0,1]); current=2 if pc>=.35 else 1 if pb>=.48 else 0
        out.append({'mid':m,'t':t,'f':f,'pb':pb,'pc':pc,'current':current,'teacher':ctx.seq.STATES[y]})
    return out
def tcore(s):return 'CROSSING_PROTECTION' if s=='CROSS_SAFE' else s
def desired(state,cp,last_change):
    pb,pc,t=cp['pb'],cp['pc'],cp['t']; can=(t-last_change)>=CFG['minDwellMs']; ns=state
    if can and pc>=CFG['crossOn']:ns='CROSSING_PROTECTION'
    elif state=='CROSSING_PROTECTION':
        if can and pc<CFG['crossOn']*.7:ns='BUILD_WEAK_SIDE' if pb>=CFG['buildOn'] else 'ALLOW_ASYMMETRY'
    elif state=='BUILD_WEAK_SIDE':
        if can and pb<CFG['buildOff']:ns='ALLOW_ASYMMETRY'
    else:
        if can and pb>=CFG['buildOn']:ns='BUILD_WEAK_SIDE'
    return ns
def armA(cps):
    state='ALLOW_ASYMMETRY';last=cps[0]['t'];pred=[]
    for cp in cps:
        ns=desired(state,cp,last)
        if ns!=state:state=ns;last=cp['t']
        pred.append(state)
    return pred
def armB(cps,pat):
    state='ALLOW_ASYMMETRY';last=cps[0]['t'];pred=[];pm=1;po=0;pt=1;names=seqctx.SEQS[pat]
    for i,cp in enumerate(cps):
        want=desired(state,cp,last);nm=names[i%len(names)];sc=next(s for s in ctx.SC if s[0]==nm);p=ctx.packet(cp,sc);mode=int(COOP.predict(np.asarray([coopvec.vec32(cp,p,pm,po,pt,i%len(names))],float))[0])
        if want!=state and mode in (1,3):state=want;last=cp['t']
        pred.append(state);pm=mode;po=p['ownershipState'];pt=p['terminalCertainty']
    return pred
def metr(t,p):
    n=len(t);sync=sum(a==b for a,b in zip(t,p))/n;tch=[(i,t[i]) for i in range(1,n) if t[i]!=t[i-1]];pch=[(i,p[i]) for i in range(1,n) if p[i]!=p[i-1]];ts=sum(1 for i,s in tch if any(abs(j-i)<=2 and ps==s for j,ps in pch))/max(1,len(tch));return sync,ts,len(pch)
def main():
    c=sqlite3.connect(DB);mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-300:];cand=[]
    for mid in mids:
        if core.build_market(c,mid):cand.append(mid)
    cand=cand[-20:];rows=[]
    for idx,mid in enumerate(cand):
        cps=build_snaps(c,mid)
        if not cps:continue
        teacher=[tcore(x['teacher']) for x in cps];sw=sum(1 for a,b in zip(teacher,teacher[1:]) if {a,b}=={'ALLOW_ASYMMETRY','BUILD_WEAK_SIDE'})
        if sw<2 or 'CROSSING_PROTECTION' not in teacher:continue
        A=armA(cps);pat=SEQ_ORDER[idx%len(SEQ_ORDER)];B=armB(cps,pat);am=metr(teacher,A);bm=metr(teacher,B);rows.append({'marketId':mid,'contextPattern':pat,'n':len(cps),'A_checkpointSync':am[0],'B_checkpointSync':bm[0],'A_transitionSync2cp':am[1],'B_transitionSync2cp':bm[1],'A_switches':am[2],'B_switches':bm[2]})
    c.close()
    def avg(k):return float(np.mean([r[k] for r in rows])) if rows else None
    ma,mb=avg('A_checkpointSync'),avg('B_checkpointSync');ta,tb=avg('A_transitionSync2cp'),avg('B_transitionSync2cp')
    rep={'version':'R3_R21_COOPERATION_FORMATION_AB_V1','mode':'dream-fill descriptive A/B; no pass threshold','markets':len(rows),'A':'original R3 Formation hysteresis','B':'same R3 + trained R2.1 contextual sequence cooperation adapter V3.2','metrics':{'A_meanCheckpointSync':ma,'B_meanCheckpointSync':mb,'deltaCheckpointSync':None if ma is None else mb-ma,'A_meanTransitionSync2cp':ta,'B_meanTransitionSync2cp':tb,'deltaTransitionSync2cp':None if ta is None else tb-ta,'A_meanSwitches':avg('A_switches'),'B_meanSwitches':avg('B_switches')},'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True},'rows':rows}
    (R/'r3_r21_cooperation_formation_ab_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep['metrics'],indent=2))
if __name__=='__main__':main()
