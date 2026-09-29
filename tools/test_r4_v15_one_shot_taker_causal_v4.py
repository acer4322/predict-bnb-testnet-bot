from __future__ import annotations
import argparse,json,math,sqlite3,sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import run_r4_complexity_pruning_m1_completion_fastpath_v9 as lite
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

MODE='LITE_NO_M1_COMPLETION_FASTPATH'

def bind(data:Path):
    lite.v5.base.STRATEGY_DB=data/'strategy_target_compare_v1.db'
    lite.v5.base.mod.BOOK_DB=data/'wallet_maker_book_inference.db'
    lite.v5.base.ex.BOOK_DB=data/'wallet_maker_book_inference.db'
    lite.v5.base.tape_v1.ARCHIVE_DIR=data/'execution_tape_v1/markets'

def settlement(db:Path,mid:int)->str:
    c=sqlite3.connect(db);r=c.execute("select winner from target_market_results where market_id=? and asset='BTC'",(int(mid),)).fetchone();c.close()
    if not r or str(r[0]) not in {'UP','DOWN'}:raise RuntimeError(f'no settlement {mid}')
    return str(r[0])

def run_arm(mid:int,sched:dict[str,Any]|None,arm:str):
    base=lite.v5.base
    real_new=base.new_controller
    real_run=base.run_market
    interventions=[]
    def inject_new(a):
        c=real_new(a);orig_step=c._step;state={'attempted':False}
        target_side=str((sched or {}).get('objectiveKey','|')).split('|')[-1] if sched else None
        start_ms=int((sched or {}).get('startMs') or 0)
        window_end_ms=(start_ms+int(round(float((sched or {}).get('startSecondsLeft') or 0.0)*1000.0))) if sched else 0
        def step(s):
            out=orig_step(s)
            if not sched or state['attempted']:
                return out
            sampled=int(s.get('sampledAtMs') or 0)
            cur=int(getattr(a.bt,'current_timestamp',0)//1_000_000)
            now=max(sampled,cur)+1
            if now < start_ms:
                return out
            # One-shot means the preregistered episode can be missed by a changed control trajectory.
            if now > int((sched or {}).get('endMs') or start_ms)+3000:
                state['attempted']=True
                interventions.append({'marketId':mid,'arm':arm,'atMs':now,'status':'MISSED_PREREGISTERED_EPISODE_WINDOW'})
                return out
            sec=(window_end_ms-now)/1000.0
            up=float(c.inventory.maker_up+c.inventory.taker_up);dn=float(c.inventory.maker_down+c.inventory.taker_down)
            gap=abs(up-dn);weak='DOWN' if up>dn+1e-9 else 'UP' if dn>up+1e-9 else None
            port=c.inventory.features(int(now));floor=float(port.get('worst_case_floor') or 0.0)
            active_weak=[o for o in c.orders.values() if str(getattr(o,'side',''))==str(weak)] if weak else []
            checks={
                'managementPhase':bool(60.0<=sec<180.0),
                'completionHorizon':bool(sec>=130.642),
                'nonPositiveFloor':bool(floor<=1e-9),
                'largeDeficit':bool(gap>36.0+1e-9),
                'sameWeakSide':bool(weak==target_side),
                'activeWeakMakerCarrier':bool(len(active_weak)>=1),
                'takerCooldownClear':bool(now-int(getattr(c,'last_taker_ms',-10**18))>=1000),
            }
            if not all(checks.values()):
                # Keep waiting inside the frozen episode because carrier state may arrive shortly after episode onset.
                interventions.append({'marketId':mid,'arm':arm,'atMs':now,'status':'RECHECK_BLOCK','checks':checks,'secondsLeft':sec,'weakSide':weak,'targetSide':target_side,'floor':floor,'absGap':gap,'activeWeakMakerCarriers':len(active_weak)})
                return out
            bf=base.mod.outcome_book(c.book.book,None) or {}
            ask=bf.get('up_ask') if target_side=='UP' else bf.get('down_ask')
            if ask is None or not math.isfinite(float(ask)):
                state['attempted']=True
                interventions.append({'marketId':mid,'arm':arm,'atMs':now,'status':'BLOCK_NO_ASK','checks':checks,'secondsLeft':sec,'weakSide':weak,'floor':floor,'absGap':gap})
                return out
            state['attempted']=True
            snap=dict(s);snap['secondsLeft']=sec
            pre={'up':up,'down':dn,'floor':floor,'absGap':gap,'combinedNet':up-dn,'activeWeakMakerCarriers':len(active_weak)}
            ok=bool(c._record_taker(target_side,float(ask),int(now),f'R4_V15_CAUSAL:{mid}:{now}',snap,{},math.nan,math.nan,math.nan,'REPAIR_EFFECT'))
            postp=c.inventory.features(int(max(now,int(getattr(a.bt,'current_timestamp',0)//1_000_000))))
            interventions.append({'marketId':mid,'arm':arm,'atMs':now,'status':'FILLED_OR_ACCEPTED' if ok else 'ATTEMPT_NOT_CONFIRMED','checks':checks,'secondsLeft':sec,'side':target_side,'ask':float(ask),'pre':pre,'postFloor':float(postp.get('worst_case_floor') or 0.0),'postAbsNet':float(postp.get('combined_abs_net') or 0.0),'lastTakerMs':int(getattr(c,'last_taker_ms',-10**18))})
            return out
        c._step=step
        return c
    def rawq_run(*args,**kwargs):
        kwargs['taker_sizing_mode']='r3_rawq'
        return real_run(*args,**kwargs)
    base.new_controller=inject_new if sched else real_new
    base.run_market=rawq_run
    ev=[]
    try:
        rep=lite.run_overlay(int(mid),MODE,ev)
    finally:
        base.run_market=real_run;base.new_controller=real_new
    return rep,interventions,ev

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--candidates',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    data=Path(args.data_root).resolve();bind(data);cand=json.loads(Path(args.candidates).read_text());scheds={int(r['marketId']):r for r in cand['eligibleEpisodes']};rows=[]
    for mid,s in scheds.items():
        rec={'marketId':mid,'schedule':s}
        try:
            ctrl,ctrl_int,ctrl_ev=run_arm(mid,None,'CONTROL')
            act,act_int,act_ev=run_arm(mid,s,'ONE_SHOT_V15')
            w=settlement(data/'target_wallet_official_v1.db',mid);A=score(ctrl,w);B=score(act,w)
            rec.update({'winner':w,'control':A,'oneShot':B,'deltaPnl':B['pnlUsdt']-A['pnlUsdt'],'deltaFloor':B['finalFloor']-A['finalFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'controlStats':ctrl.get('mgmtStats') or {},'oneShotStats':act.get('mgmtStats') or {},'interventions':act_int,'controlInterventions':ctrl_int,'controlMgmtEvents':ctrl_ev,'oneShotMgmtEvents':act_ev,'controlTakerAttempts':ctrl.get('takerAttempts') or [],'oneShotTakerAttempts':act.get('takerAttempts') or []})
        except Exception as ex:rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec);print(json.dumps({'marketId':mid,'deltaPnl':rec.get('deltaPnl'),'deltaFloor':rec.get('deltaFloor'),'deltaAbsNet':rec.get('deltaAbsNet'),'intervention':[(x.get('status'),x.get('atMs')) for x in rec.get('interventions',[]) if x.get('status')!='RECHECK_BLOCK'],'error':rec.get('error')},ensure_ascii=False),flush=True)
    good=[r for r in rows if 'error' not in r]
    attempts=[x for r in good for x in r.get('interventions',[]) if x.get('status') not in {'RECHECK_BLOCK','MISSED_PREREGISTERED_EPISODE_WINDOW'}]
    agg={'markets':len(good),'errors':len(rows)-len(good),'totalDeltaPnl':sum(r['deltaPnl'] for r in good),'meanDeltaFloor':sum(r['deltaFloor'] for r in good)/len(good) if good else None,'meanDeltaAbsNet':sum(r['deltaAbsNet'] for r in good)/len(good) if good else None,'improvements':sum(r['deltaPnl']>1e-9 for r in good),'degradations':sum(r['deltaPnl']<-1e-9 for r in good),'ties':sum(abs(r['deltaPnl'])<=1e-9 for r in good),'worstDeltaPnl':min([r['deltaPnl'] for r in good],default=None),'bestDeltaPnl':max([r['deltaPnl'] for r in good],default=None),'interventionAttempts':len(attempts),'confirmedInterventions':sum(x.get('status')=='FILLED_OR_ACCEPTED' for x in attempts),'missedEpisodeWindows':sum(any(x.get('status')=='MISSED_PREREGISTERED_EPISODE_WINDOW' for x in r.get('interventions',[])) for r in good)}
    out={'version':'R4_V15_ONE_SHOT_TAKER_CAUSAL_V4','researchOnly':True,'actionAuthority':False,'takerSizing':'r3_rawq in BOTH paired arms','rows':rows,'aggregate':agg,'boundary':'Candidates frozen without winner/PnL. Intervention uses current strict-past safety rechecks and realistic HFT taker confirmation; settlement only terminal scoring.'};Path(args.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(agg,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
