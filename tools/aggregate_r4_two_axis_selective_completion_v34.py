from __future__ import annotations
import glob,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_two_axis_selective_completion_v34_summary.json'

def load(prefix):
    out={}
    errs=[]
    for p in glob.glob(str(RET/(prefix+'*'))):
        rp=Path(p)/'result.json'
        if not rp.exists(): continue
        d=json.loads(rp.read_text(encoding='utf-8'))
        errs += d.get('errors',[])
        for r in d.get('rows',[]): out[int(r['marketId'])]=r
    return out,errs

def pnl(r,w):
    f=r['final']; return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])

cand,ce=load('r4-v34-cand-')
base,be=load('r4-v34-base-')
ids=sorted(set(cand)&set(base))
q=','.join('?'*len(ids)) if ids else 'NULL'
db=sqlite3.connect(str(ROOT/'data/target_wallet_official_v1.db'))
wins={int(a):str(b) for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids) if b in ('UP','DOWN')} if ids else {}
db.close()
rows=[]
for m in ids:
    if m not in wins: continue
    bp=pnl(base[m],wins[m]); cp=pnl(cand[m],wins[m])
    rows.append({'marketId':m,'winner':wins[m],'baselinePnl':bp,'candidatePnl':cp,'deltaPnl':cp-bp,'baselineWin':bp>0,'candidateWin':cp>0,'baselineMakerFilledShares':base[m].get('makerFilledShares',0),'candidateMakerFilledShares':cand[m].get('makerFilledShares',0),'baselineCounts':base[m].get('counts',{}),'candidateCounts':cand[m].get('counts',{})})

def agg(rows):
    n=len(rows); bw=sum(r['baselineWin'] for r in rows); cw=sum(r['candidateWin'] for r in rows)
    bm=sum(float(r['baselineMakerFilledShares']) for r in rows); cm=sum(float(r['candidateMakerFilledShares']) for r in rows)
    return {'n':n,'baselineWins':bw,'candidateWins':cw,'baselineWinRate':bw/n if n else None,'candidateWinRate':cw/n if n else None,'winRateDelta':(cw-bw)/n if n else None,'baselinePnl':sum(r['baselinePnl'] for r in rows),'candidatePnl':sum(r['candidatePnl'] for r in rows),'deltaPnl':sum(r['deltaPnl'] for r in rows),'lossToWin':sum((not r['baselineWin']) and r['candidateWin'] for r in rows),'winToLoss':sum(r['baselineWin'] and (not r['candidateWin']) for r in rows),'winnerPreserved':sum(r['baselineWin'] and r['candidateWin'] for r in rows),'bothLoss':sum((not r['baselineWin']) and (not r['candidateWin']) for r in rows),'baselineMakerFilledShares':bm,'candidateMakerFilledShares':cm,'makerFillRatio':cm/bm if bm else None,'duplicateResponsibilityViolations':sum(int(r['candidateCounts'].get('authoritySubmitClamps',0)) for r in rows)}
summary={'version':'R4_TWO_AXIS_SELECTIVE_COMPLETION_V34_SUMMARY','researchOnly':True,'causalDevelopmentEvidence':True,'cohortUse':'consumed A/B/C only; not promotion evidence','aggregate':agg(rows),'rows':rows,'candidateErrors':ce,'baselineErrors':be}
OUT.write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
print(json.dumps({'artifact':str(OUT),'aggregate':summary['aggregate'],'candidateErrors':len(ce),'baselineErrors':len(be)},ensure_ascii=False))
