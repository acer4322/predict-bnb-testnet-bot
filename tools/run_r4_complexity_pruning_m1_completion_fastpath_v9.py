from __future__ import annotations
import argparse,json,sqlite3,sys,warnings
from pathlib import Path
import numpy as np,pandas as pd
warnings.filterwarnings('ignore', message='X does not have valid feature names')
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_completion_horizon_admission_v5 as v5
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
MODES={'LITE_CORE','LITE_NO_SMALL_DEFICIT','LITE_NO_HORIZON','LITE_NO_POSITIVE_FLOOR','LITE_NO_M1','LITE_NO_M1_EXEC_CONFIRM','LITE_NO_M1_COMPLETION_FASTPATH'}

def run_overlay(mid:int, mode:str, events:list[dict]):
    if mode not in MODES: raise ValueError(mode)
    use_transition=False
    use_path=False
    use_mgmt_latch=True
    guard_small=(mode!='LITE_NO_SMALL_DEFICIT')
    guard_horizon=(mode!='LITE_NO_HORIZON')
    guard_floor=(mode not in {'LITE_NO_POSITIVE_FLOOR','LITE_NO_M1','LITE_NO_M1_EXEC_CONFIRM','LITE_NO_M1_COMPLETION_FASTPATH'})
    use_m1=(mode not in {'LITE_NO_M1','LITE_NO_M1_EXEC_CONFIRM','LITE_NO_M1_COMPLETION_FASTPATH'})
    exec_confirm=(mode in {'LITE_NO_M1_EXEC_CONFIRM','LITE_NO_M1_COMPLETION_FASTPATH'})
    completion_fastpath=(mode=='LITE_NO_M1_COMPLETION_FASTPATH')
    orig_new=v5.base.new_controller
    rng=np.random.default_rng(930000+int(mid))
    stats={'mode':mode,'riskCandidates':0,'positiveFloorPreserves':0,'unownedAdmissionPreserves':0,'insufficientHorizonPreserves':0,'managementObjectiveOpens':0,'managementObjectiveContinues':0,'managementObjectiveCompletes':0,'pathEntries':0,'pathPreserves':0,'crossOverrides':0,'completionFastpathAdmissions':0}
    def custom_new(a):
        c=orig_new(a); orig_add=c._add_order
        state={'name':'ALLOW_ASYMMETRY','last':-10**18,'eval_t':None,'intent':None,'path_latch':False,'mgmt_latch':False,'confirm_since':None}
        parents=[]
        def eval_state(now:int):
            if state['eval_t']==now and state['intent'] is not None:return state['intent']
            f=v5.r3ctl.current_features(c,now)
            if f is None:
                it={'state':'ALLOW_ASYMMETRY','repairMode':'NORMAL_FORMATION','weakSide':None,'weakSideMakerScale':1.,'strongSideMakerScale':1.,'pauseStrongSide':False,'pb':0.,'pc':0.,'unc':0.,'contextType':'NO_INVENTORY','mgmtReason':'','pFormationPath':0.}
                state['eval_t']=now;state['intent']=it;return it
            X=np.asarray([[float(f[k]) for k in v5.r3.FEATS]],float)
            pb=float(v5.r3.ARB.predict_proba(X)[0,1]);pc=float(v5.r3.CROSS.predict_proba(X)[0,1])
            packet=v5.r3ctl.packet_from_active(c,a,now,f); coop=v5.r3.cooperation(state['name'],pb,pc,f,packet);unc=v5.r3.uncertainty_from_packet(packet,coop)
            bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
            if coop=='PRESERVE_CURRENT_REGIME' and pc>=.35:con=.35
            can=(now-state['last'])>=1000; ns=state['name']
            if can and pc>=con:ns='CROSSING_PROTECTION'
            elif ns=='CROSSING_PROTECTION':
                if can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
            elif ns=='BUILD_WEAK_SIDE':
                if can and pb<boff:ns='ALLOW_ASYMMETRY'
            elif can and pb>=bon:ns='BUILD_WEAK_SIDE'
            mgmt_reason=''; ppath=0.; risk=0.; trans=0.; mf=None; pf=None
            try:
                mf,weakm=v5.mgmt_features(c,a,now,f)
                
                if use_m1:
                    pm=v5.M1.predict_proba(np.asarray([[mf[k] for k in v5.FULL]],float))[0]
                    cm={str(x):float(pm[i]) for i,x in enumerate(v5.CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.)
                else:
                    risk=1.0
                if use_transition:
                    pt=v5.TM.predict_proba(np.asarray([[mf[k] for k in v5.TFEATS]],float))[0];trans=float(pt[1] if len(pt)>1 else pt[0])
                else: trans=1.0
                pf,weakp=v5.path_features(c,now,parents)
                phase_ok=bool(60<=float(mf['seconds_left'])<180 and (weakp!='FLAT' or weakm is not None))
                raw_unowned_pre=max(0.,float(mf.get('abs_gap',0.))-float(mf.get('weak_unresolved_shares',0.) or 0.))
                base_admission=bool(phase_ok and risk>=.5 and (trans>=.5 if use_transition else True))
                if exec_confirm and not state['mgmt_latch']:
                    preeligible=bool(base_admission and raw_unowned_pre>36.+1e-9 and float(mf.get('seconds_left',0.))-60.>=70.642 and float(pf['pre_floor'])<=0.)
                    if preeligible:
                        weak_unresolved=float(mf.get('weak_unresolved_shares',0.) or 0.)
                        weak_progress=float(mf.get('weak_progress_ratio',0.) or 0.)
                        weak_owners=float(mf.get('weak_active_owners',0.) or 0.)
                        existing_completion_fast=bool(completion_fastpath and weak_owners==1.0 and weak_progress>0.0 and 0.0<weak_unresolved<18.0)
                        if existing_completion_fast:
                            admission=True; stats['completionFastpathAdmissions']+=1
                        else:
                            if state['confirm_since'] is None: state['confirm_since']=now
                            admission=bool(now-int(state['confirm_since'])>=2200)
                    else:
                        state['confirm_since']=None;admission=False
                else:
                    admission=base_admission
                candidate=bool(phase_ok and ((state['mgmt_latch'] if use_mgmt_latch else False) or admission))
                if (not phase_ok) and state['mgmt_latch']:
                    state['mgmt_latch']=False; state['confirm_since']=None; stats['managementObjectiveCompletes']+=1
                if candidate:
                    stats['riskCandidates']+=1
                    unowned=max(0.,float(mf.get('abs_gap',0.))-float(mf.get('weak_unresolved_shares',0.) or 0.))
                    raw_gap=float(mf.get('abs_gap',0.)); floor_now=float(pf['pre_floor'])
                    if use_mgmt_latch and state['mgmt_latch'] and (floor_now>0 or raw_gap<=36.+1e-9):
                        state['mgmt_latch']=False;state['path_latch']=False;state['confirm_since']=None;mgmt_reason='MANAGEMENT_OBJECTIVE_COMPLETE';stats['managementObjectiveCompletes']+=1
                    elif use_mgmt_latch and state['mgmt_latch']:
                        stats['managementObjectiveContinues']+=1
                    elif guard_small and unowned<=36.+1e-9:
                        state['path_latch']=False;mgmt_reason='UNOWNED_DEFICIT_NO_OPEN_LE_2_PARENT_LOTS';stats['unownedAdmissionPreserves']+=1
                    elif guard_horizon and float(mf.get('seconds_left',0.))-60.<70.642:
                        state['path_latch']=False;mgmt_reason='INSUFFICIENT_COMPLETION_HORIZON';stats['insufficientHorizonPreserves']+=1
                    elif guard_floor and floor_now>0:
                        state['path_latch']=False;mgmt_reason='POSITIVE_FLOOR';stats['positiveFloorPreserves']+=1
                    else:
                        if use_path:
                            sp=v5.scale_pf(pf);ppath=float(v5.PM.predict_proba(pd.DataFrame([[sp[k] for k in v5.PFEATS]],columns=v5.PFEATS))[0,1])
                            if (not state['path_latch']) and ppath>=.5:state['path_latch']=True;stats['pathEntries']+=1
                            if state['path_latch']:mgmt_reason='FORMATION_PATH_LATCH';stats['pathPreserves']+=1
                        if not mgmt_reason and use_mgmt_latch and not state['mgmt_latch']:
                            state['mgmt_latch']=True;stats['managementObjectiveOpens']+=1
                    if not mgmt_reason:
                        ns='CROSSING_PROTECTION';stats['crossOverrides']+=1
                    events.append({'mode':mode,'marketId':mid,'atMs':now,'risk':risk,'transition':trans,'pFormationPath':ppath,'floor':float(pf['pre_floor']),'surplus':float(pf['pre_surplus']),'mgmtReason':mgmt_reason,'forcedCross':not bool(mgmt_reason),'mgmtObjectiveLatched':bool(state['mgmt_latch']),'unownedWeakDeficit':unowned})
            except Exception as ex:
                events.append({'mode':mode,'marketId':mid,'atMs':now,'error':f'{type(ex).__name__}:{ex}'})
            if ns!=state['name']:state['name']=ns;state['last']=now
            ci=v5.r3.control_intent(ns,f);it={'state':ns,**ci,'pb':pb,'pc':pc,'unc':unc,'contextType':packet['contextType'],'cooperationMode':coop,'buildOn':bon,'crossOn':con,'mgmtReason':mgmt_reason,'pFormationPath':ppath}
            state['eval_t']=now;state['intent']=it;return it
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            it=eval_state(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
                made=False
                if weak:made=orig_add(weak,now,snapshot_ns,decision_id,'R3_CROSSING_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
                if made:parents.append({'t':now,'side':weak,'role':'MAKER','shares':18.0})
                return bool(made)
            if it['state']=='BUILD_WEAK_SIDE' and strong and side==strong and rng.random()>float(it['strongSideMakerScale']):
                made=False
                if weak:made=orig_add(weak,now,snapshot_ns,decision_id,'R3_BUILD_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
                if made:parents.append({'t':now,'side':weak,'role':'MAKER','shares':18.0})
                return bool(made)
            made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
            if made:parents.append({'t':now,'side':side,'role':'MAKER','shares':18.0})
            return made
        c._add_order=add;return c
    v5.base.new_controller=custom_new
    try:rep=v5.base.run_market(int(mid),entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
    finally:v5.base.new_controller=orig_new
    rep['mgmtStats']=stats;return rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',required=True,choices=sorted(MODES));ap.add_argument('--ids-json',required=True);ap.add_argument('--tag',required=True);ap.add_argument('--data-root',default=None);ap.add_argument('--output-dir',default=None);ap.add_argument('--baseline-cache-json',default=None);args=ap.parse_args()
    data_root=Path(args.data_root).resolve() if args.data_root else ROOT/'data'
    if args.data_root:
        v5.base.STRATEGY_DB=data_root/'strategy_target_compare_v1.db';v5.base.mod.BOOK_DB=data_root/'wallet_maker_book_inference.db';v5.base.ex.BOOK_DB=data_root/'wallet_maker_book_inference.db';v5.base.tape_v1.ARCHIVE_DIR=data_root/'execution_tape_v1/markets'
    settle_db=data_root/'target_wallet_official_v1.db'
    def get_settlement(mid):
        c=sqlite3.connect(settle_db);r=c.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();c.close()
        if not r or str(r[0]) not in {'UP','DOWN'}: raise RuntimeError(f'no settlement {mid}')
        return str(r[0]),int(r[1])
    ids_path=Path(args.ids_json); ids_path=ids_path if ids_path.is_absolute() else ROOT/ids_path
    ids=[int(x) for x in json.loads(ids_path.read_text())];rows=[];events=[]
    baseline_cache={}
    if args.baseline_cache_json:
        bp=Path(args.baseline_cache_json);bp=bp if bp.is_absolute() else ROOT/bp;baseline_cache=json.loads(bp.read_text())
    for mid in ids:
        rec={'marketId':mid,'mode':args.mode}
        try:
            cand=run_overlay(mid,args.mode,events)
            winner,resolved=get_settlement(mid)
            if str(mid) in baseline_cache: A=baseline_cache[str(mid)]
            else: A=score(v5.r3ctl.run_market(mid,True),winner)
            B=score(cand,winner)
            rec.update({'resolvedAtMs':resolved,'R3':A,'candidate':B,'deltaPnl':B['pnlUsdt']-A['pnlUsdt'],'deltaFloor':B['finalFloor']-A['finalFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'mgmtStats':cand.get('mgmtStats') or {}})
        except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec);print(json.dumps({'marketId':mid,'mode':args.mode,'deltaPnl':rec.get('deltaPnl'),'deltaFloor':rec.get('deltaFloor'),'error':rec.get('error')},ensure_ascii=False),flush=True)
    good=[r for r in rows if 'error' not in r]
    agg={'mode':args.mode,'markets':len(good),'errors':len(rows)-len(good),'totalCandidatePnl':float(sum(r['candidate']['pnlUsdt'] for r in good)),'totalR3Pnl':float(sum(r['R3']['pnlUsdt'] for r in good)),'totalDeltaPnl':float(sum(r['deltaPnl'] for r in good)),'meanDeltaFloor':float(np.mean([r['deltaFloor'] for r in good])) if good else None,'meanDeltaAbsNet':float(np.mean([r['deltaAbsNet'] for r in good])) if good else None,'improvements':sum(r['deltaPnl']>1e-9 for r in good),'degradations':sum(r['deltaPnl']<-1e-9 for r in good),'ties':sum(abs(r['deltaPnl'])<=1e-9 for r in good),'worstDeltaPnl':min([r['deltaPnl'] for r in good],default=None),'bestDeltaPnl':max([r['deltaPnl'] for r in good],default=None),'crossOverrides':sum(int((r.get('mgmtStats') or {}).get('crossOverrides',0)) for r in good)}
    outdir=Path(args.output_dir).resolve() if args.output_dir else P;outdir.mkdir(parents=True,exist_ok=True);out=outdir/f'r4_complexity_pruning_guard_ablation_v2_{args.tag}_{args.mode.lower()}.json';out.write_text(json.dumps({'version':'R4_COMPLEXITY_PRUNING_M1_ABLATION_V4','researchOnly':True,'developmentOnly':True,'mode':args.mode,'rows':rows,'aggregate':agg,'events':events},indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'artifact':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()

