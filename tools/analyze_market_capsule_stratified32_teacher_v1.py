from __future__ import annotations
import argparse,json,math,statistics
from pathlib import Path
from collections import defaultdict
EPS=1e-9

def f(x,d=0.0):
    try:
        z=float(x);return z if math.isfinite(z) else d
    except:return d

def h(row,ms):
    return next((x for x in row.get('horizons',[]) if int(x.get('horizonMs') or 0)==ms),{})
def agg(rows):
    if not rows:return {'n':0}
    def mean(k):return sum(f(r.get(k)) for r in rows)/len(rows)
    filled=[r for r in rows if f(r.get('fill60'))>EPS]; filled5=[r for r in rows if f(r.get('fill5'))>EPS]
    return {'n':len(rows),'seams':len({r['seamId'] for r in rows}),
      'fill5Rate':sum(f(r['fill5'])>EPS for r in rows)/len(rows),'fill60Rate':sum(f(r['fill60'])>EPS for r in rows)/len(rows),
      'meanDeltaFloor60':mean('deltaFloor60'),'meanDeltaUpside60':mean('deltaUpside60'),'meanMarkout5':mean('markout5'),'meanMarkout60':mean('markout60'),
      'meanDeltaFloor60Filled':(sum(f(r['deltaFloor60']) for r in filled)/len(filled)) if filled else None,'meanDeltaUpside60Filled':(sum(f(r['deltaUpside60']) for r in filled)/len(filled)) if filled else None,
      'positiveFloor60Share':sum(f(r['deltaFloor60'])>EPS for r in rows)/len(rows),'positiveUpside60Share':sum(f(r['deltaUpside60'])>EPS for r in rows)/len(rows),
      'positiveMarkout5AmongFilled':(sum(f(r['markout5'])>EPS for r in filled5)/len(filled5)) if filled5 else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--seams',required=True);ap.add_argument('--fork',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    sd=json.loads(Path(a.seams).read_text(encoding='utf-8'));fd=json.loads(Path(a.fork).read_text(encoding='utf-8'))
    sm={str(r['seam_id']):r for r in sd['rows']}; rows=[]
    for r in fd['rows']:
        ac=r.get('action') or {}
        if ac.get('kind')=='WAIT' or 'error' in r:continue
        s=sm[str(r['seamId'])];up=f(s.get('pre_up_shares'));dn=f(s.get('pre_down_shares'));side=str(ac.get('side'))
        if abs(up-dn)<=EPS:role='NEUTRAL'
        else:
            repair='UP' if up<dn else 'DOWN'; role='REPAIR' if side==repair else 'EXPAND'
        h5=h(r,5000);h60=h(r,60000)
        rows.append({'marketId':int(r['marketId']),'seamId':str(r['seamId']),'category':str(s.get('sampling_category')),'role':role,'kind':str(ac.get('kind')),'side':side,'baseQty':f(ac.get('baseQty')),'qty':f(ac.get('qty')),
          'preUp':up,'preDown':dn,'preFloor':f(s.get('pre_floor')),'preUpside':f(s.get('pre_upside')),'preAbsGap':f(s.get('pre_abs_share_gap')),
          'fill5':f(h5.get('filledQty')),'fill60':f(h60.get('filledQty')),'deltaFloor60':f(h60.get('deltaFloor')),'deltaUpside60':f(h60.get('deltaUpside')),'markout5':f(h5.get('incrementalMarkoutUsdt')),'markout60':f(h60.get('incrementalMarkoutUsdt'))})
    by=defaultdict(list)
    for r in rows:by[(r['category'],r['role'],r['kind'])].append(r)
    overall=defaultdict(list)
    for r in rows:overall[(r['role'],r['kind'])].append(r)
    # Fair same-seam baseQty=2 passive vs active comparison.
    pairs=[]
    for sid,s in sm.items():
        sr=[r for r in rows if r['seamId']==sid and abs(r['baseQty']-2.0)<=EPS]
        for role in ('REPAIR','EXPAND'):
            p=next((r for r in sr if r['role']==role and r['kind']=='PASSIVE'),None);ac=next((r for r in sr if r['role']==role and r['kind']=='ACTIVE'),None)
            if p and ac:
                pairs.append({'marketId':p['marketId'],'seamId':sid,'category':p['category'],'role':role,
                  'passiveFill5':p['fill5'],'activeFill5':ac['fill5'],'passiveFill60':p['fill60'],'activeFill60':ac['fill60'],
                  'passiveMarkout5':p['markout5'],'activeMarkout5':ac['markout5'],'passiveFloor60':p['deltaFloor60'],'activeFloor60':ac['deltaFloor60'],'passiveUpside60':p['deltaUpside60'],'activeUpside60':ac['deltaUpside60']})
    pairagg={}
    for role in ('REPAIR','EXPAND'):
        rr=[x for x in pairs if x['role']==role]
        pairagg[role]={'n':len(rr),'activeFill5Wins':sum((x['activeFill5']>EPS and x['passiveFill5']<=EPS) for x in rr),'passiveFill5Wins':sum((x['passiveFill5']>EPS and x['activeFill5']<=EPS) for x in rr),'bothFill5':sum((x['passiveFill5']>EPS and x['activeFill5']>EPS) for x in rr),'neitherFill5':sum((x['passiveFill5']<=EPS and x['activeFill5']<=EPS) for x in rr),
          'activeFill60Rate':sum(x['activeFill60']>EPS for x in rr)/len(rr) if rr else None,'passiveFill60Rate':sum(x['passiveFill60']>EPS for x in rr)/len(rr) if rr else None,
          'meanActiveMarkout5':sum(x['activeMarkout5'] for x in rr)/len(rr) if rr else None,'meanPassiveMarkout5':sum(x['passiveMarkout5'] for x in rr)/len(rr) if rr else None}
    # Economic sign consistency only among filled actions.
    econ={}
    for role in ('REPAIR','EXPAND'):
        rr=[r for r in rows if r['role']==role and r['fill60']>EPS]
        econ[role]={'filledN':len(rr),'expectedEconomicSignShare':(sum((r['deltaFloor60']>EPS and r['deltaUpside60']< -EPS) if role=='REPAIR' else (r['deltaFloor60']< -EPS and r['deltaUpside60']>EPS) for r in rr)/len(rr)) if rr else None,
          'meanDeltaFloor60':sum(r['deltaFloor60'] for r in rr)/len(rr) if rr else None,'meanDeltaUpside60':sum(r['deltaUpside60'] for r in rr)/len(rr) if rr else None}
    out={'version':'MARKET_CAPSULE_STRATIFIED32_ECONOMIC_EXECUTION_TEACHER_SLICE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'sourceFork':a.fork,'rows':rows,'coverage':{'actions':len(rows),'seams':len({r['seamId'] for r in rows}),'markets':len({r['marketId'] for r in rows})},
      'overall':{f'{k[0]}_{k[1]}':agg(v) for k,v in overall.items()},'byCategory':{f'{k[0]}|{k[1]}|{k[2]}':agg(v) for k,v in by.items()},'pairedBaseQty2':pairagg,'economicSignConsistency':econ,'pairRows':pairs,
      'boundary':['role derived only from pre inventory weak/strong side; Target current action not used','exact-balance states NEUTRAL','execution comparison passive2 vs active2 on same seam','60s fork metrics are causal probe labels not graduation','no winner input/no dream fill']}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'coverage':out['coverage'],'overall':out['overall'],'pairedBaseQty2':pairagg,'economicSignConsistency':econ},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
