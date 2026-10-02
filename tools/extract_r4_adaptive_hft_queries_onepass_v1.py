from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import torch

ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import audit_r4_target_sequence_v13_wholemarket_shadow as sh
from tools.worker_r4_adaptive_hft_transfer_v1 import load_adaptive

EPS=1e-9

def action_label(actions,t,purpose):
    hi=int(t)+5000
    return int(any(int(a['atMs'])>int(t) and int(a['atMs'])<=hi and str(a.get('purpose'))==purpose for a in actions))

def future_query(qs,i):
    target=int(qs[i]['atMs'])+5000
    for j in range(i+1,len(qs)):
        tj=int(qs[j]['atMs'])
        if tj>=target:
            return qs[j] if tj<=target+1500 else None
    return None

def rows_from_market(m):
    if m.get('error'): return []
    acts=m.get('actualActions') or []; qs=m.get('queries') or []; out=[]
    for i,q in enumerate(qs):
        raw=q['raw']; gap=max(float(raw.get('abs_gap') or 0.),18.); f=future_query(qs,i)
        r={'marketId':int(m['marketId']),'atMs':int(q['atMs']),'phase':q['phase'],'secondsLeft':float(q['secondsLeft']),
           'repairBase':float(q['pRoleTaker']),'addBase':float(q['pActionTimePurposeAdd']),
           'repairAction5s':action_label(acts,q['atMs'],'REPAIR'),'addAction5s':action_label(acts,q['atMs'],'ADD'),
           'floor':float(raw.get('floor') or 0.),'absGap':float(raw.get('abs_gap') or 0.),'upside':float(raw.get('upside') or 0.)}
        if f is not None:
            fr=f['raw']; fd=float(fr.get('floor') or 0.)-r['floor']; gd=r['absGap']-float(fr.get('abs_gap') or 0.); ud=float(fr.get('upside') or 0.)-r['upside']
            r['repairGain5s']=max(0.,fd)/gap+max(0.,gd)/gap
            r['repairEconomicEligible']=bool(r['floor']<0 or r['absGap']>EPS)
            r['safeUpside5s']=int(ud>EPS and float(fr.get('floor') or 0.)>=r['floor']-EPS)
            r['floorDelta5s']=fd; r['absGapDelta5s']=-gd; r['upsideDelta5s']=ud
        else:
            r['repairGain5s']=None; r['repairEconomicEligible']=False; r['safeUpside5s']=None
        out.append(r)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle-dir',required=True); ap.add_argument('--checkpoint',required=True); ap.add_argument('--ids-json',required=True); ap.add_argument('--out',required=True); args=ap.parse_args()
    bundle=Path(args.bundle_dir); ids=list(map(int,json.loads(Path(args.ids_json).read_text(encoding='utf-8')))); wm=json.loads((bundle/'window_end_map.json').read_text(encoding='utf-8'))
    device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu'); model=load_adaptive(Path(args.checkpoint),bundle,device)
    rows=[]; markets=[]
    for mid in ids:
        try:
            m=sh.run_market(mid,int(wm[str(mid)]),model,device,bundle); rr=rows_from_market(m); rows.extend(rr); markets.append({'marketId':mid,'queries':len(rr),'error':m.get('error')})
            print(json.dumps(markets[-1]),flush=True)
        except Exception as e:
            markets.append({'marketId':mid,'queries':0,'error':f'{type(e).__name__}:{e}'}); print(json.dumps(markets[-1]),flush=True)
    rep={'version':'R4_ADAPTIVE_HFT_QUERY_ONEPASS_V1','researchOnly':True,'actionAuthority':False,'device':str(device),'markets':markets,'queryRows':rows,'guards':['realistic HFT/no dream fill','single replay per market','strict-past model inputs','no action mutation']}
    Path(args.out).parent.mkdir(parents=True,exist_ok=True); Path(args.out).write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'markets':len(markets),'queries':len(rows),'errors':sum(bool(x.get('error')) for x in markets)}),flush=True)
if __name__=='__main__': main()
