from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'
REPORT=OUT/'target_joint_conversion_objective_v0_report.json'
CSV=OUT/'target_joint_conversion_objective_v0_rows.csv'
H=[0,5,15,30,60,120]
EPS=1e-9


def ro():
    c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True,timeout=30)
    c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def sign(side): return 1.0 if side=='UP' else -1.0

def apply(st,e):
    q=float(e['shares']); qt=str(e['quote_type']).upper(); side=str(e['side']).upper(); role=str(e['role']).upper(); px=float(e['price'])
    s=1.0 if qt=='BID' else -1.0
    st[role.lower()+'_'+side.lower()] += s*q
    st['cash'] += (-px*q if qt=='BID' else px*q)

def metrics(st,winner):
    up=st['maker_up']+st['taker_up']; dn=st['maker_down']+st['taker_down']; net=up-dn
    maker_net=st['maker_up']-st['maker_down']; taker_net=st['taker_up']-st['taker_down']
    gross=abs(up)+abs(dn); cov=(2*min(max(up,0),max(dn,0))/gross) if gross>EPS and up>=0 and dn>=0 else None
    floor=st['cash']+min(up,dn); ceil=st['cash']+max(up,dn)
    ws=sign(winner)
    return {'up':up,'down':dn,'net':net,'makerNet':maker_net,'takerNet':taker_net,'winnerSignedNet':ws*net,'winnerSignedMaker':ws*maker_net,'winnerSignedTaker':ws*taker_net,'gross':gross,'pairedCoverage':cov,'worstCaseFloor':floor,'bestCasePnl':ceil}

def snap(events,t,winner):
    st={'maker_up':0.0,'maker_down':0.0,'taker_up':0.0,'taker_down':0.0,'cash':0.0}
    for e in events:
        if int(e['event_ms'])>t: break
        apply(st,e)
    return metrics(st,winner)

def rate(xs): return sum(bool(x) for x in xs)/len(xs) if xs else None

def mean(xs):
    z=[float(x) for x in xs if x is not None and math.isfinite(float(x))]; return sum(z)/len(z) if z else None

def main():
    c=ro()
    try:
        results={int(r['market_id']):dict(r) for r in c.execute("select market_id,winner,resolved_at_ms,net_pnl_usdt,maker_net_pnl_usdt,taker_net_pnl_usdt from target_market_results where asset='BTC' and winner in ('UP','DOWN')")}
        ev=defaultdict(list)
        for r in c.execute("select market_id,event_ms,role,side,quote_type,price,shares from wallet_shadow_target_events where asset='BTC' and role in ('MAKER','TAKER') and side in ('UP','DOWN') and quote_type in ('BID','ASK') and shares>0 order by market_id,event_ms,id"):
            ev[int(r['market_id'])].append(dict(r))
    finally: c.close()
    rows=[]
    for mid,es in ev.items():
        rr=results.get(mid)
        if not rr: continue
        dt=datetime.fromtimestamp(int(rr['resolved_at_ms'])/1000,ZoneInfo('Asia/Taipei'))
        if dt.date().isoformat()=='2026-08-16': continue
        tak=[e for e in es if str(e['role']).upper()=='TAKER']
        if not tak: continue
        t0=int(tak[0]['event_ms']); winner=str(rr['winner'])
        # state immediately before first Taker
        pre=snap(es,t0-1,winner)
        if abs(pre['makerNet'])<=EPS: continue
        pre_loser=pre['winnerSignedMaker']<0
        pre_winner=pre['winnerSignedMaker']>0
        if not (pre_loser or pre_winner): continue
        base=snap(es,t0,winner)
        row={'marketId':mid,'winner':winner,'anchorMs':t0,'preClass':'LOSER' if pre_loser else 'WINNER','preMakerWinnerSigned':pre['winnerSignedMaker'],'preCombinedWinnerSigned':pre['winnerSignedNet'],'anchorFloor':base['worstCaseFloor'],'anchorCoverage':base['pairedCoverage'],'anchorWinnerSigned':base['winnerSignedNet'],'pnl':float(rr['net_pnl_usdt'])}
        for h in H:
            m=snap(es,t0+h*1000,winner)
            row[f'align_{h}s']=m['winnerSignedNet']; row[f'makerAlign_{h}s']=m['winnerSignedMaker']; row[f'takerAlign_{h}s']=m['winnerSignedTaker']; row[f'floor_{h}s']=m['worstCaseFloor']; row[f'cov_{h}s']=m['pairedCoverage']
            row[f'dAlign_{h}s']=m['winnerSignedNet']-base['winnerSignedNet']; row[f'dFloor_{h}s']=m['worstCaseFloor']-base['worstCaseFloor']
        rows.append(row)
    import csv
    OUT.mkdir(parents=True,exist_ok=True)
    with CSV.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()) if rows else []); w.writeheader(); w.writerows(rows)
    def seg(name):
        z=[r for r in rows if r['preClass']==name]
        o={'markets':len(z),'meanPnl':mean([r['pnl'] for r in z])}
        for h in H:
            da=[r[f'dAlign_{h}s'] for r in z]; df=[r[f'dFloor_{h}s'] for r in z]
            o[f'h{h}']={'meanAlignmentDelta':mean(da),'alignmentImprovedRate':rate([x>EPS for x in da]),'meanFloorDelta':mean(df),'floorNonWorseRate':rate([x>=-EPS for x in df]),'jointAlignUpFloorNonWorseRate':rate([(a>EPS and b>=-EPS) for a,b in zip(da,df)]),'meanCoverage':mean([r[f'cov_{h}s'] for r in z]),'meanMakerAlignment':mean([r[f'makerAlign_{h}s'] for r in z]),'meanTakerAlignment':mean([r[f'takerAlign_{h}s'] for r in z])}
        return o
    report={'version':'TARGET_JOINT_CONVERSION_OBJECTIVE_V0','researchOnly':True,'winnerRuntimeInput':False,'purpose':'Diagnostic only: quantify whether Target post-first-Taker conversion improves eventual-winner alignment while preserving worst-case floor/pair structure. Winner is used only as an offline outcome label.','coverage':{'rows':len(rows),'loser':sum(r['preClass']=='LOSER' for r in rows),'winner':sum(r['preClass']=='WINNER' for r in rows)},'segments':{'PRE_LOSER':seg('LOSER'),'PRE_WINNER':seg('WINNER')},'rowsCsv':str(CSV.relative_to(ROOT))}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
