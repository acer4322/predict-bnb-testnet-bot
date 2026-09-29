from __future__ import annotations
import argparse,json,math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PROV=ROOT/'data/research/r4_v0/p0_provenance_v1'
EPS=1e-9
CELLS=['R247_CONTROL','R264_CONTROL','GPT6_SYNTHESIS_CANDIDATE']

def load_rows(p):
    d=json.loads(Path(p).read_text(encoding='utf-8'))
    rows=d.get('rows') or d.get('markets')
    if not isinstance(rows,list): raise ValueError('input must contain rows[]')
    return d,rows

def native_correct(r):
    if float(r.get('unauthorizedOverflowQty',0) or 0)>EPS:return False
    if float(r.get('repairQuotaExcessMax',0) or 0)>EPS:return False
    flags=[r.get('candidateCorrectnessPass'),r.get('correctnessPass'),r.get('r264CorrectnessPass'),r.get('r247ServiceCorrectnessPass')]
    if not any(x is True for x in flags): return False
    inv=r.get('candidateInvariantViolations')
    if isinstance(inv,dict):
        for v in inv.values():
            if v is None:return False
            try:
                f=float(v)
                if not math.isfinite(f) or f>EPS:return False
            except Exception:return False
    return True

def slim(r):
    return {k:r.get(k) for k in ['pnlDiagnosticOnly','floor','best','fillEvents','filledQty','submits','unauthorizedOverflowQty','repairQuotaExcessMax']}

def agg(rs):
    return {
      'markets':len(rs),
      'wins':sum(float(r['pnlDiagnosticOnly'])>0 for r in rs),
      'winRate':sum(float(r['pnlDiagnosticOnly'])>0 for r in rs)/len(rs) if rs else 0,
      'totalPnl':sum(float(r['pnlDiagnosticOnly']) for r in rs),
      'avgPnl':sum(float(r['pnlDiagnosticOnly']) for r in rs)/len(rs) if rs else 0,
      'aggregateFloor':sum(float(r['floor']) for r in rs),
      'aggregateBest':sum(float(r['best']) for r in rs),
      'fillEvents':sum(int(r['fillEvents']) for r in rs),
      'filledQty':sum(float(r.get('filledQty',0) or 0) for r in rs),
      'submits':sum(int(r['submits']) for r in rs),
      'worstPnl':min(float(r['pnlDiagnosticOnly']) for r in rs) if rs else None,
      'worstFloor':min(float(r['floor']) for r in rs) if rs else None,
    }

def canonical_maps():
    r247=json.loads((PROV/'MS4_R247_FULL24_CONSUMED_RESULT_20260906.json').read_text(encoding='utf-8'))
    a={int(r['marketId']):r for r in r247['rows'] if r.get('cell')=='MS4_R247_BOUNDED_CORE_SERVICE_FAVORABLE_RECYCLE'}
    r264=json.loads((PROV/'MS4_R264_FULL24_CONSUMED_RESULT_20260906.json').read_text(encoding='utf-8'))
    b={int(r['marketId']):r for r in r264['rows'] if r.get('cell')=='R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND'}
    return a,b

def same_metrics(x,y):
    dif={}
    for k in ['pnlDiagnosticOnly','floor','best','filledQty']:
        if k in x and k in y:
            d=abs(float(x[k])-float(y[k]));
            if d>1e-8:dif[k]=d
    for k in ['fillEvents','submits']:
        if int(x.get(k,-999))!=int(y.get(k,-998)):dif[k]=(x.get(k),y.get(k))
    return dif

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--candidate',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d,rows=load_rows(a.candidate)
    r247c,r264c=canonical_maps(); mids=sorted(r247c)
    seen={}
    duplicates=[]
    for r in rows:
        key=(int(r['marketId']),str(r['cell']))
        if key in seen:duplicates.append(key)
        seen[key]=r
    missing=[(m,c) for m in mids for c in CELLS if (m,c) not in seen]
    parity=[]
    for m in mids:
        if (m,'R247_CONTROL') in seen:
            dif=same_metrics(seen[(m,'R247_CONTROL')],r247c[m])
            if dif:parity.append({'marketId':m,'cell':'R247_CONTROL','diff':dif})
        if (m,'R264_CONTROL') in seen:
            dif=same_metrics(seen[(m,'R264_CONTROL')],r264c[m])
            if dif:parity.append({'marketId':m,'cell':'R264_CONTROL','diff':dif})
    cand=[seen[(m,'GPT6_SYNTHESIS_CANDIDATE')] for m in mids if (m,'GPT6_SYNTHESIS_CANDIDATE') in seen]
    a247=agg([r247c[m] for m in mids]); a264=agg([r264c[m] for m in mids]); ac=agg(cand) if cand else agg([])
    corr=bool(cand) and all(native_correct(r) for r in cand)
    r247wins=[m for m in mids if float(r247c[m]['pnlDiagnosticOnly'])>0]
    collateral=sum(float(seen[(m,'GPT6_SYNTHESIS_CANDIDATE')]['pnlDiagnosticOnly'])-float(r247c[m]['pnlDiagnosticOnly']) for m in r247wins if (m,'GPT6_SYNTHESIS_CANDIDATE') in seen)
    per=[]
    for m in mids:
        if (m,'GPT6_SYNTHESIS_CANDIDATE') not in seen:continue
        c=seen[(m,'GPT6_SYNTHESIS_CANDIDATE')]
        per.append({'marketId':m,'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
                    'pnlDeltaVsR247':float(c['pnlDiagnosticOnly'])-float(r247c[m]['pnlDiagnosticOnly']),
                    'floorDeltaVsR247':float(c['floor'])-float(r247c[m]['floor']),
                    'pnlDeltaVsR264':float(c['pnlDiagnosticOnly'])-float(r264c[m]['pnlDiagnosticOnly']),
                    'floorDeltaVsR264':float(c['floor'])-float(r264c[m]['floor']),
                    'fillDeltaVsR264':int(c['fillEvents'])-int(r264c[m]['fillEvents'])})
    gates={
      'all72RowsComplete':len(missing)==0 and len(duplicates)==0,
      'controlParityPass':len(parity)==0,
      'candidateCorrectnessPass':corr,
      'pnlAtLeastR247':len(cand)==24 and ac['totalPnl']+EPS>=a247['totalPnl'],
      'floorAtLeastR247':len(cand)==24 and ac['aggregateFloor']+EPS>=a247['aggregateFloor'],
      'winsAtLeastR264':len(cand)==24 and ac['wins']>=a264['wins'],
      'fillRetentionVsR26490pct':len(cand)==24 and ac['fillEvents']+EPS>=0.90*a264['fillEvents'],
    }
    gates['machineChallengePass']=all(gates.values())
    out={'version':'GPT6_TARGET_OUR_SYNTHESIS_CHALLENGE_V1_SCORE','source':str(a.candidate),'aggregate':{'R247':a247,'R264':a264,'CANDIDATE':ac},
         'candidateVsAnchors':{'pnlDeltaVsR247':ac['totalPnl']-a247['totalPnl'] if cand else None,'floorDeltaVsR247':ac['aggregateFloor']-a247['aggregateFloor'] if cand else None,
                               'winsDeltaVsR264':ac['wins']-a264['wins'] if cand else None,'fillRetentionVsR264':ac['fillEvents']/a264['fillEvents'] if cand else None,
                               'R247OriginalWinnerPnlCollateral':collateral if cand else None},
         'gates':gates,'missing':missing,'duplicates':duplicates,'controlParityFailures':parity,'perMarket':per,
         'manualReviewRequired':['role-level activity/cadence','winner collateral and loss tails','repeated causal mechanism evidence','mandatory counterexample markets','no future/Target runtime leakage','no hidden authority or pending-fill-as-payment'],
         'interpretation':'machineChallengePass is consumed mechanism evidence only; never graduation or live promotion'}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'gates':gates,'candidateVsAnchors':out['candidateVsAnchors']},ensure_ascii=False))
if __name__=='__main__':main()
