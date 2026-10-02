from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_lane_g_multi_action_exact_fork_v1b as ma
ACTIONS=('KEEP_REPAIR','ORDINARY_REEXPAND')
EPS=1e-9

def delta_from_trigger(r):
    tr=(r.get('trigger') or {}).get('payoff') or (r.get('trigger') or {}).get('state',{}).get('payoff')
    ev=r.get('firstStructuralEvent') or {}; ep=ev.get('payoff')
    if not tr or not ep:return {'dBest':None,'dFloor':None,'dGap':None}
    return {'dBest':float(ep['best'])-float(tr['best']),'dFloor':float(ep['floor'])-float(tr['floor']),'dGap':float(ep['gap'])-float(tr['gap'])}

def classify(r):
    ev=r.get('firstStructuralEvent') or {}; reasons=ev.get('reasons') or []; bk=str(r.get('branchKey') or ''); sk=str(((r.get('trigger') or {}).get('state') or {}).get('liveRepairKeys',[None])[0])
    for x in reasons:
        if x.get('type')=='CONFIRMED_INVOLVED_FILL' and str(x.get('key'))==bk and bk:return 'REEXPAND_FIRST_FILL'
    for x in reasons:
        if x.get('type')=='CONFIRMED_INVOLVED_FILL' and str(x.get('key'))==sk:return 'REPAIR_FIRST_FILL'
    for x in reasons:
        if x.get('type')=='INVOLVED_QUEUE_TERMINAL' and str(x.get('key'))==bk and bk:return 'REEXPAND_TERMINAL_FIRST'
    for x in reasons:
        if x.get('type')=='INVOLVED_QUEUE_TERMINAL' and str(x.get('key'))==sk:return 'REPAIR_TERMINAL_FIRST'
    return 'OTHER_STRUCTURAL'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--spec',required=True);ap.add_argument('--market-ids',default='');ap.add_argument('--output',required=True);a=ap.parse_args()
    sd=json.loads(Path(a.spec).read_text(encoding='utf-8'));specs={int(k):v for k,v in sd['markets'].items()}
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()] if a.market_ids else sorted(specs)
    mids=[m for m in mids if m in specs]
    for m in mids:ma.FROZEN[m]=specs[m]
    tmp=Path(tempfile.mkdtemp(prefix='lane_g_keep_ord_h100_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];vectors=[];parity={};marketSummary=[]
        for m in mids:
            rr=[]
            for action in ACTIONS:
                s=ma.MultiActionExactForkSim(tmp/f'{m}.json.xz',m,action,1,4)
                try:r=s.run_exact(co[m]['winner'])
                finally:s.close()
                r['deltaFromFrozenTrigger']=delta_from_trigger(r);r['structuralClassification']=classify(r);rr.append(r);rows.append(r)
                ev=r.get('firstStructuralEvent') or {}; ob=ev.get('obligation') or {}; reasons=ev.get('reasons') or []
                vec={'marketId':m,'action':action,**r['deltaFromFrozenTrigger'],'classification':r['structuralClassification'],'eventT':ev.get('t'),'responsibilityRepaidAtEvent':float(ob.get('repaidQty') or 0.0),'responsibilityOutstandingAtEvent':float(ob.get('outstanding') or 0.0),'branchConfirmedFill':any(x.get('type')=='CONFIRMED_INVOLVED_FILL' and str(x.get('key'))==str(r.get('branchKey')) and r.get('branchKey') for x in reasons),'repairConfirmedFill':any(x.get('type')=='CONFIRMED_INVOLVED_FILL' and str(x.get('key'))==str(specs[m]['siblingKey']) for x in reasons),'activeFillQtyDelta':float(ev.get('activeFillQtyDelta') or 0.0),'scopeGenerationAtEvent':ev.get('scopeGeneration'),'scopeSideAtEvent':ev.get('scopeSide'),'correct':bool(r['correct'])}
                vectors.append(vec);print(json.dumps(vec,ensure_ascii=False),flush=True)
            ks=[x for x in rr if x['action']=='KEEP_REPAIR'][0];osr=[x for x in rr if x['action']=='ORDINARY_REEXPAND'][0]
            parity[str(m)]=(ks.get('trigger') or {}).get('state')==(osr.get('trigger') or {}).get('state')
            kv=ks['deltaFromFrozenTrigger'];ov=osr['deltaFromFrozenTrigger'];causal=any(abs(float((ov.get(k) or 0)-(kv.get(k) or 0)))>1e-8 for k in ['dBest','dFloor','dGap']) or osr['structuralClassification']!=ks['structuralClassification']
            kt=ks['terminal'];ot=osr['terminal']
            marketSummary.append({'marketId':m,'triggerParity':parity[str(m)],'keepClass':ks['structuralClassification'],'reexpandClass':osr['structuralClassification'],'localCausalDistinct':causal,'localDeltaReexpandMinusKeep':{k:(float(ov[k])-float(kv[k]) if ov[k] is not None and kv[k] is not None else None) for k in ['dBest','dFloor','dGap']},'terminalSecondaryReexpandMinusKeep':{'pnl':float(ot['pnlPostHocOnly'])-float(kt['pnlPostHocOnly']),'best':float(ot['best'])-float(kt['best']),'floor':float(ot['floor'])-float(kt['floor']),'fills':int(ot['fills'])-int(kt['fills']),'submits':int(ot['submits'])-int(kt['submits'])}})
        gates={'allTriggered':all(x['triggered'] for x in rows),'allResolvedByStructuralEvent':all(x['resolved'] for x in rows),'triggerStateParity':all(parity.values()),'correctnessPass':all(x['correct'] for x in rows)}
        hist={}
        for x in vectors:
            if x['action']=='ORDINARY_REEXPAND':hist[x['classification']]=hist.get(x['classification'],0)+1
        out={'version':'LANE_G_KEEP_VS_ORDINARY_REEXPAND_H100_EXACT_FORK_V1_RESULT_20260907','researchOnly':True,'runtimeAuthority':False,'sourceSpec':a.spec,'markets':mids,'rows':rows,'actionVectors':vectors,'marketSummary':marketSummary,'reexpandStructuralHistogram':hist,'gates':gates,'boundary':['preregistered first actual R263 submit state per distinct market','KEEP vs ORDINARY only','identical strict-past trigger state','all local deltas computed from each branch identical frozen trigger','structural-event horizon only','terminal PnL secondary post-hoc only','manager frozen only until first structural event','same realistic HFT tape/latency/queue','max4 <=180s unchanged','consumed H100 only','no Target/winner future branch input','no fresh/no dream fill/no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':gates,'hist':hist,'marketSummary':marketSummary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
