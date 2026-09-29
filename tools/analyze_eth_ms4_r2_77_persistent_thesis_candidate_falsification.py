from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import Counter
EPS=1e-9

def side_majority(xs):
    c=Counter(x for x in xs if x in ('UP','DOWN'))
    if not c:return None
    if c['UP']==c['DOWN']:return None
    return 'UP' if c['UP']>c['DOWN'] else 'DOWN'

def analyze(r):
    winner=str(r.get('winnerPostHocOnly') or '').upper()
    hist=r.get('slotHistory') or []
    split=[x for x in (r.get('splitEvents') or []) if x.get('event')=='ROLE_FILL_SPLIT']
    risk_keys={str(x.get('key')) for x in (r.get('riskTrancheLedger') or [])}
    births=[x for x in hist if x.get('event')=='RESPONSIBILITY_SCOPE_BIRTH']
    first_birth_t=int(births[0].get('t') or 0) if births else None
    probe_sub=[x for x in hist if x.get('event')=='ROLE_SLOT_SUBMIT' and x.get('role')=='PROBE_CORE']
    early_probe_sub=[x for x in probe_sub if first_birth_t is None or int(x.get('t') or 0)<=first_birth_t]
    probe_fill=[x for x in split if x.get('role')=='PROBE_CORE' and float(x.get('fillInc') or 0)>EPS]
    risk_fill=[x for x in split if str(x.get('key')) in risk_keys and float(x.get('fillInc') or 0)>EPS]
    expand_fill=[x for x in split if x.get('role')=='SATELLITE_EXPAND' and float(x.get('fillInc') or 0)>EPS]
    candidates={
      'firstProbeSubmit':str(probe_sub[0].get('side')) if probe_sub else None,
      'majorityPreBirthProbeSubmit':side_majority([str(x.get('side')) for x in early_probe_sub]),
      'lastPreBirthProbeSubmit':str(early_probe_sub[-1].get('side')) if early_probe_sub else None,
      'firstProbeFill':str(probe_fill[0].get('side')) if probe_fill else None,
      'firstScopeBirth':str(births[0].get('scopeSide')) if births else None,
      'firstRiskFill':str(risk_fill[0].get('side')) if risk_fill else None,
      'lastRiskFill':str(risk_fill[-1].get('side')) if risk_fill else None,
      'majorityRiskFill':side_majority([str(x.get('side')) for x in risk_fill]),
      'firstExpandFill':str(expand_fill[0].get('side')) if expand_fill else None,
      'majorityExpandFill':side_majority([str(x.get('side')) for x in expand_fill]),
    }
    return {'marketId':int(r['marketId']),'winner':winner,'pnl':float(r['pnlDiagnosticOnly']),'best':float(r['best']),'floor':float(r['floor']),
            'riskFillCount':len(risk_fill),'probeSubmitSides':[x.get('side') for x in early_probe_sub],
            'probeFillSides':[x.get('side') for x in probe_fill],'riskFillSides':[x.get('side') for x in risk_fill],
            'expandFillSides':[x.get('side') for x in expand_fill],'candidates':candidates}

def score(rows,key,subset=None):
    xs=[r for r in rows if (subset(r) if subset else True) and r['candidates'].get(key) in ('UP','DOWN')]
    if not xs:return {'n':0,'aligned':0,'rate':None}
    a=sum(r['candidates'][key]==r['winner'] for r in xs)
    return {'n':len(xs),'aligned':a,'rate':a/len(xs)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--r264',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.load(open(a.r264,encoding='utf-8'));rows=[]
    for r in d['rows']:
        if r.get('cell')!='R264_EXECUTION_REPRESENTED_PRE_REPAIR_REEXPAND':continue
        rows.append(analyze(r))
    keys=list(rows[0]['candidates']) if rows else []
    scores={}
    for k in keys:
        scores[k]={'full24':score(rows,k),'riskMarkets':score(rows,k,lambda x:x['riskFillCount']>0),
                   'r264Wins':score(rows,k,lambda x:x['pnl']>0),'r264Losses':score(rows,k,lambda x:x['pnl']<=0)}
    # Positive passive-cycle witness markets were preregistered by R273 evidence; this is a diagnostic subset, not fitting authority.
    pos={1945986,1946317,1946475,1946488,1946640,1946656,1946792}
    for k in keys:scores[k]['positiveCycleWitnesses']=score(rows,k,lambda x:x['marketId'] in pos)
    out={'version':'MS4_R2_77_PERSISTENT_THESIS_CANDIDATE_FALSIFICATION_V1','researchOnly':True,'behaviorChange':False,
         'source':a.r264,'scores':scores,'rows':rows,
         'boundary':['winner used only for post-hoc scoring','no candidate promoted from this consumed24 diagnostic','candidate definitions are strict-past action/history identities already present in OUR trace','goal is to falsify scope/responsibility-derived profit-thesis proxies before behavior integration','no runtime change']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    for k,v in scores.items():print(k,json.dumps(v,ensure_ascii=False))
    print(json.dumps({'ok':True,'markets':len(rows)},ensure_ascii=False))
if __name__=='__main__':main()
