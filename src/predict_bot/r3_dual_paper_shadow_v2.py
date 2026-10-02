from __future__ import annotations
import hashlib, json, math, os, sqlite3, threading, time, lzma, warnings
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import joblib, numpy as np
from threadpoolctl import threadpool_limits

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'data/research/r3_v0'
DREAM_DB=ROOT/'data/strategy_target_compare_v1.db'
HFT_DB=ROOT/'data/hft_forward_paper_v1.db'
OUT_DB=ROOT/'data/r3_dual_paper_shadow_v2.db'
PORT=int(os.environ.get('R3_DUAL_PAPER_PORT','8796'))
VERSION='R3_DUAL_PAPER_SHADOW_V2'
BASE='R3_FORMATION_BASELINE'
SOFT='R3_FORMATION_R21_CONTEXT_V4'
DREAM_SOURCE='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
SOURCE_FINGERPRINT_VERSION='R3_DUAL_SOURCE_FINGERPRINT_V1'
ARB_B=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')
CROSS_B=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')
ARB=ARB_B['model']; CROSS=CROSS_B['model']; FEATS=ARB_B['features']
CTX=joblib.load(R/'r3_r21_context_adapter_hgb_v2.joblib')
CTX_MODEL=CTX['model']; CTX_LABELS={int(k):v for k,v in CTX['labels'].items()} if any(isinstance(k,str) for k in CTX['labels']) else CTX['labels']

def conn(path,ro=False):
    if ro:
        c=sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True,timeout=10); c.execute('pragma query_only=on')
    else:
        c=sqlite3.connect(path,timeout=10,check_same_thread=False); c.execute('pragma journal_mode=WAL'); c.execute('pragma busy_timeout=5000')
    c.row_factory=sqlite3.Row; return c

def stable_fingerprint(payload):
    body=json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()
    return hashlib.sha256(body).hexdigest()

def fee(role,sh,px): return sh*px*.02 if role=='TAKER' else 0.0

def features(events):
    up=down=cost=fees=0.; hist=deque(); prev=None; out=[]
    if not events:return out
    start=events[0][0]; end=events[-1][0]
    for i,(t,role,side,px,sh) in enumerate(events):
        if side=='UP':up+=sh
        else:down+=sh
        cost+=px*sh; fees+=fee(role,sh,px)
        pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; sur='UP' if up>down else 'DOWN' if down>up else 'FLAT'
        hist.append((t,role,side,sh,ss,fl,ups));
        while hist and t-hist[0][0]>15000:hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':float(ss-old[4]),'floor_change_5s':float(fl-old[5]),'upside_change_5s':float(ups-old[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':(t-start)/max(1,end-start),'_surplus_side':sur}
        out.append((t,f)); prev=t
    return out

def ctx_vec(state,pb,pc,f,p):
    current={'ALLOW_ASYMMETRY':0.,'BUILD_WEAK_SIDE':1.,'CROSSING_PROTECTION':2.}[state]; conf=max(pb,pc); unreleased=float(p['ownershipState']!=0)
    vals=[current,pb,pc,abs(pb-.5),abs(pc-.5),float(f['floor']),float(f['floor_change_5s']),float(f['surplus_shares']),float(f['surplus_change_5s']),float(f['upside_change_5s']),float(p['ownershipState']),float(p['terminalCertainty']),float(p['remainingObligationFraction']),float(p['passiveProgressProbability']),float(p['reconcileNeeded']),float(p['cancelPending']),float(p['unknownState']),float(p['eventValidity']),float(p['observationAgeMs'])/10000.,float(p['elapsedSincePriorMs'])/5000.,float(p['hasPriorObservation']),p['passiveProgressProbability']*p['remainingObligationFraction']*conf,unreleased*conf,p['terminalCertainty']*conf,p['reconcileNeeded']*conf,p['eventValidity']*conf,p['remainingObligationFraction']*min(abs(float(f['floor_change_5s'])),100.)/100.,p['passiveProgressProbability']*min(abs(float(f['surplus_change_5s'])),200.)/200.]
    return np.asarray([vals],float)

def cooperation(state,pb,pc,f,p):
    lab=int(CTX_MODEL.predict(ctx_vec(state,pb,pc,f,p))[0]); return CTX_LABELS[lab]

def uncertainty_from_packet(p,mode):
    if mode=='IGNORE_CONTEXT_EVENT': return 0.0
    if mode=='ALLOW_R3_REEVALUATION': return min(.12,.04+.08*(1-float(p['terminalCertainty'])))
    if mode=='RECONCILE_THEN_REEVALUATE': return min(.35,.15+.20*float(p['remainingObligationFraction']))
    # preserve
    return min(1.,.35+.45*float(p['remainingObligationFraction'])+.20*(1-float(p['terminalCertainty'])))

def control_intent(state,f):
    sur=f.get('_surplus_side','FLAT'); weak='DOWN' if sur=='UP' else 'UP' if sur=='DOWN' else None
    if state=='BUILD_WEAK_SIDE': return {'repairMode':'PASSIVE_WEAK_SIDE_BUILD','weakSide':weak,'weakSideMakerScale':1.35,'strongSideMakerScale':0.55,'pauseStrongSide':False}
    if state=='CROSSING_PROTECTION': return {'repairMode':'CROSSING_PROTECTION','weakSide':weak,'weakSideMakerScale':1.50,'strongSideMakerScale':0.15,'pauseStrongSide':True}
    return {'repairMode':'NORMAL_FORMATION','weakSide':weak,'weakSideMakerScale':1.0,'strongSideMakerScale':1.0,'pauseStrongSide':False}

def model_scores(rows):
    if not rows:return []
    X=np.asarray([[float(f[k]) for k in FEATS] for _,f in rows],float)
    build=np.asarray(ARB.predict_proba(X),float)[:,1]
    cross=np.asarray(CROSS.predict_proba(X),float)[:,1]
    return [(float(pb),float(pc)) for pb,pc in zip(build,cross)]

def run_states(rows,packet_fn,soft,scores=None):
    state='ALLOW_ASYMMETRY'; last=rows[0][0] if rows else 0; out=[]
    for i,(t,f) in enumerate(rows):
        if scores is None:
            X=np.asarray([[float(f[k]) for k in FEATS]],float); pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1])
        else:pb,pc=scores[i]
        p=packet_fn(i,t,f)
        mode='ALLOW_R3_REEVALUATION'; unc=0.; bon=.48; boff=.38; con=.35
        if soft:
            mode=cooperation(state,pb,pc,f,p); unc=uncertainty_from_packet(p,mode); bon=.48+.03*unc; boff=.38-.015*unc; con=.35+.0225*unc
        can=(t-last)>=1000; ns=state
        if can and pc>=con:ns='CROSSING_PROTECTION'
        elif state=='CROSSING_PROTECTION':
            if can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
        elif state=='BUILD_WEAK_SIDE':
            if can and pb<boff:ns='ALLOW_ASYMMETRY'
        elif can and pb>=bon:ns='BUILD_WEAK_SIDE'
        changed=ns!=state
        if changed:state=ns; last=t
        out.append((t,state,changed,pb,pc,unc,bon,boff,con,p,mode,control_intent(state,f),f))
    return out

class Runtime:
    def __init__(self):
        if os.environ.get('PREDICT_LIVE_ENABLED','false').lower() not in {'0','false','no','off',''}: raise RuntimeError('R3 dual paper v2 refuses live-enabled environment')
        self.db=conn(OUT_DB); self.lock=threading.RLock(); self.stop=threading.Event(); self.last_error=None; self.last_loop=None
        self.last_tick_stats={'sourceChecks':0,'marketsRecomputed':0,'marketsSkipped':0,'durationMs':None}
        self.total_source_checks=0; self.total_markets_recomputed=0; self.total_markets_skipped=0
        self.db.executescript('''
        create table if not exists r3_shadow_states_v2(strategy_version text,lane text,market_id integer,checkpoint_ms integer,formation_state text,state_changed integer,build_p real,cross_p real,r21_uncertainty real,build_on real,build_off real,cross_on real,cooperation_mode text,floor real,surplus_shares real,context_packet_json text,control_intent_json text,payload_json text,primary key(strategy_version,lane,market_id,checkpoint_ms));
        create table if not exists r3_shadow_market_v2(strategy_version text,lane text,market_id integer,checkpoint_count integer,switch_count integer,safe_cross_observed integer,repair_intent_count integer,context_nontrivial_count integer,last_state text,updated_at_ms integer,primary key(strategy_version,lane,market_id));
        create table if not exists r3_hft_control_runs_v2(strategy_version text,market_id integer,status text,maker_fill_events integer,maker_filled_shares real,taker_fills integer,final_abs_net real,worst_case_floor real,maker_net real,veto_strong integer,weak_substitutions integer,contextual_events integer,summary_json text,completed_at_ms integer,primary key(strategy_version,market_id));
        create table if not exists r3_source_fingerprints_v2(lane text,market_id integer,source_fingerprint text not null,fingerprint_version text not null,processed_at_ms integer not null,primary key(lane,market_id));
        create table if not exists r3_meta_v2(key text primary key,value text);
        '''); self.db.commit()
        row=self.db.execute("select value from r3_meta_v2 where key='hft_control_activation_market'").fetchone()
        if row is None:
            hc=conn(HFT_DB,True); mx=hc.execute("select max(market_id) from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE'").fetchone()[0] or 0; hc.close(); self.hft_control_activation_market=int(mx); self.db.execute("insert into r3_meta_v2(key,value) values('hft_control_activation_market',?)",(str(self.hft_control_activation_market),)); self.db.commit()
        else:self.hft_control_activation_market=int(row[0])
    def _dream_source(self,mid):
        c=conn(DREAM_DB,True)
        fills=[tuple(r) for r in c.execute('select fill_id,filled_at_ms,channel,side,price,shares from our_fills where strategy_version=? and market_id=? order by filled_at_ms,fill_id',(DREAM_SOURCE,mid))]
        orders=[dict(r) for r in c.execute('select order_id,placed_at_ms,status,filled_at_ms,cancelled_at_ms,shares,updated_at_ms from our_orders where strategy_version=? and market_id=? order by placed_at_ms,order_id',(DREAM_SOURCE,mid))]
        c.close()
        fingerprint=stable_fingerprint({'version':SOURCE_FINGERPRINT_VERSION,'lane':'DREAM_FILL_PAPER','marketId':mid,'fills':fills,'orders':orders})
        events=[(int(r[1]),str(r[2]),str(r[3]),float(r[4]),float(r[5])) for r in fills]
        return fingerprint,events,orders
    @staticmethod
    def _dream_packet_from_rows(rows):
        def fn(i,t,f):
            active=[r for r in rows if int(r['placed_at_ms'])<=t and ((r['status']=='ACTIVE') or (r['filled_at_ms'] and int(r['filled_at_ms'])>t) or (r['cancelled_at_ms'] and int(r['cancelled_at_ms'])>t))]
            if active:
                age=t-min(int(r['placed_at_ms']) for r in active); rem=min(1.,len(active)/4.); return {'contextType':'LIVE_NO_FILL_DELAY_NOT_TERMINAL','ownershipState':1,'terminalCertainty':0,'remainingObligationFraction':rem,'passiveProgressProbability':.15,'reconcileNeeded':0,'cancelPending':0,'unknownState':0,'eventValidity':1,'observationAgeMs':float(age),'elapsedSincePriorMs':float(f['age_since_last_ms']),'hasPriorObservation':1.}
            return {'contextType':'NORMAL_FULL_FILL','ownershipState':0,'terminalCertainty':1,'remainingObligationFraction':0.,'passiveProgressProbability':1.,'reconcileNeeded':0,'cancelPending':0,'unknownState':0,'eventValidity':1,'observationAgeMs':0.,'elapsedSincePriorMs':float(f['age_since_last_ms']),'hasPriorObservation':1.}
        return fn
    def _hft_source(self,mid):
        c=conn(HFT_DB,True)
        fills=[tuple(r) for r in c.execute("select fill_seq,fill_ms,channel,side,price,shares,hft_status from hft_forward_fills_v1 where strategy_key='R2' and market_id=? order by fill_ms,fill_seq",(mid,))]
        run=c.execute("select report_archive_path,completed_at_ms,attempt_count,decision_count,maker_fill_events,taker_fills from hft_forward_runs_v1 where strategy_key='R2' and market_id=? and status='COMPLETE'",(mid,)).fetchone(); c.close()
        report_path=Path(run[0]) if run and run[0] else None
        try:
            report_stat=report_path.stat() if report_path else None
            file_meta={'path':str(report_path),'size':report_stat.st_size,'mtimeNs':report_stat.st_mtime_ns} if report_stat else None
        except OSError:file_meta={'path':str(report_path),'missing':True} if report_path else None
        fingerprint=stable_fingerprint({'version':SOURCE_FINGERPRINT_VERSION,'lane':'HFT_PAPER','marketId':mid,'run':tuple(run) if run else None,'report':file_meta,'fills':fills})
        events=[(int(r[1]),str(r[2]),str(r[3]),float(r[4]),float(r[5])) for r in fills]
        return fingerprint,events,report_path
    @staticmethod
    def _hft_order_states_from_path(p):
        if p is None:return []
        try:
            with lzma.open(p,'rt',encoding='utf-8') as fh: rep=json.load(fh)
            return sorted(rep.get('orderStateRows') or [],key=lambda z:int(z.get('checkpointMs') or 0))
        except Exception:return []
    @staticmethod
    def _hft_packet_from_states(states):
        def fn(i,t,f):
            cand=[s for s in states if int(s.get('checkpointMs') or 0)<=t]
            s=cand[-1] if cand else None
            if not s: return {'contextType':'NORMAL_FULL_FILL','ownershipState':0,'terminalCertainty':1,'remainingObligationFraction':0.,'passiveProgressProbability':1.,'reconcileNeeded':0,'cancelPending':0,'unknownState':0,'eventValidity':1,'observationAgeMs':0.,'elapsedSincePriorMs':float(f['age_since_last_ms']),'hasPriorObservation':1.}
            st=str(s.get('hftStatus') or 'NONE'); rem=float(s.get('remainingQty') or 0.); orig=float(s.get('originalQty') or 0.); rr=max(0.,min(1.,rem/orig if orig>1e-9 else 0.)); prog=max(0.,min(1.,float(s.get('partialFillRatio') or 0.))); age=float(s.get('orderAgeMs') or 0.)
            if st=='PARTIALLY_FILLED': typ='LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER'; owner=1; term=0; rec=0
            elif st=='NEW': typ='LIVE_NO_FILL_DELAY_NOT_TERMINAL'; owner=1; term=0; rec=0
            elif st in {'FILLED','CANCELED','EXPIRED','REJECTED'}: typ='NORMAL_FULL_FILL' if st=='FILLED' else 'CANCEL_ACK_PARTIAL_RECOMPUTE_REMAINDER'; owner=0; term=1; rec=int(st!='FILLED')
            else: typ='ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED'; owner=2; term=0; rec=0
            return {'contextType':typ,'ownershipState':owner,'terminalCertainty':term,'remainingObligationFraction':rr,'passiveProgressProbability':prog,'reconcileNeeded':rec,'cancelPending':0,'unknownState':int(owner==2),'eventValidity':1,'observationAgeMs':age,'elapsedSincePriorMs':float(f['age_since_last_ms']),'hasPriorObservation':1.}
        return fn
    def _fingerprint_matches(self,lane,mid,fingerprint):
        with self.lock:
            row=self.db.execute('select source_fingerprint,fingerprint_version from r3_source_fingerprints_v2 where lane=? and market_id=?',(lane,mid)).fetchone()
            outputs=self.db.execute('select count(distinct strategy_version) from r3_shadow_market_v2 where lane=? and market_id=? and strategy_version in (?,?)',(lane,mid,BASE,SOFT)).fetchone()[0]
        return bool(row and row[0]==fingerprint and row[1]==SOURCE_FINGERPRINT_VERSION and int(outputs)==2)
    def _write(self,lane,mid,events,packet_fn,fingerprint):
        rows=features(events)
        state_rows=[]; market_rows=[]; now_ms=int(time.time()*1000)
        previous_jobs=[getattr(model,'n_jobs',None) for model in (ARB,CROSS)]
        try:
            for model in (ARB,CROSS):
                if hasattr(model,'n_jobs'):model.n_jobs=1
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore',message='X does not have valid feature names, but HistGradientBoostingClassifier was fitted with feature names',category=UserWarning)
                with threadpool_limits(limits=1):
                    scores=model_scores(rows)
                    for ver,soft in [(BASE,False),(SOFT,True)]:
                        pred=run_states(rows,packet_fn,soft,scores)
                        for t,state,ch,pb,pc,unc,bon,boff,con,p,mode,intent,f in pred:
                            payload={'paperOnly':True,'liveOrdersAffected':False,'r21ActionAuthority':False,'r3LiveOrderAuthority':False,'contextSource':'R21_CONTEXT_PACKET_FROM_OWN_EXECUTION_LIFECYCLE','executionEvidence':lane,'contextTypeUsedAsAction':False}
                            state_rows.append((ver,lane,mid,t,state,int(ch),pb,pc,unc,bon,boff,con,mode,f['floor'],f['surplus_shares'],json.dumps(p,separators=(',',':')),json.dumps(intent,separators=(',',':')),json.dumps(payload,separators=(',',':'))))
                        switches=sum(int(x[2]) for x in pred); safe=int(any(x[1]=='CROSSING_PROTECTION' for x in pred)); repairs=sum(x[11]['repairMode']!='NORMAL_FORMATION' for x in pred); ctxn=sum(x[10]!='ALLOW_R3_REEVALUATION' for x in pred); last=pred[-1][1] if pred else None
                        market_rows.append((ver,lane,mid,len(pred),switches,safe,repairs,ctxn,last,now_ms))
        finally:
            for model,n_jobs in zip((ARB,CROSS),previous_jobs):
                if hasattr(model,'n_jobs'):model.n_jobs=n_jobs
        with self.lock:
            try:
                self.db.execute('begin immediate')
                for ver in (BASE,SOFT):self.db.execute('delete from r3_shadow_states_v2 where strategy_version=? and lane=? and market_id=?',(ver,lane,mid))
                self.db.executemany('insert or replace into r3_shadow_states_v2 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',state_rows)
                self.db.executemany('insert or replace into r3_shadow_market_v2 values(?,?,?,?,?,?,?,?,?,?)',market_rows)
                self.db.execute('insert or replace into r3_source_fingerprints_v2 values(?,?,?,?,?)',(lane,mid,fingerprint,SOURCE_FINGERPRINT_VERSION,now_ms))
                self.db.commit()
            except Exception:
                self.db.rollback(); raise
    def _refresh_dream_market(self,mid):
        fingerprint,events,orders=self._dream_source(mid)
        if self._fingerprint_matches('DREAM_FILL_PAPER',mid,fingerprint):return False
        self._write('DREAM_FILL_PAPER',mid,events,self._dream_packet_from_rows(orders),fingerprint); return True
    def _refresh_hft_market(self,mid):
        fingerprint,events,report_path=self._hft_source(mid)
        if self._fingerprint_matches('HFT_PAPER',mid,fingerprint):return False
        states=self._hft_order_states_from_path(report_path)
        self._write('HFT_PAPER',mid,events,self._hft_packet_from_states(states),fingerprint); return True
    def tick(self):
        started=time.monotonic(); checks=recomputed=skipped=0
        dc=conn(DREAM_DB,True); dm=[int(r[0]) for r in dc.execute('select distinct market_id from our_fills where strategy_version=? order by market_id desc limit 12',(DREAM_SOURCE,))]; dc.close()
        for mid in dm:
            checks+=1; changed=self._refresh_dream_market(mid); recomputed+=int(changed); skipped+=int(not changed)
        hc=conn(HFT_DB,True); hm=[int(r[0]) for r in hc.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id desc limit 12")]; hc.close()
        for mid in hm:
            checks+=1; changed=self._refresh_hft_market(mid); recomputed+=int(changed); skipped+=int(not changed)
        # Actual PAPER execution authority begins only for new COMPLETE HFT markets after activation.
        future=[m for m in hm if m>self.hft_control_activation_market]
        if future:
            from tools import hftbacktest_r3_context_control_v0 as ctl
            for mid in sorted(future):
                for ver,soft in [(BASE,False),(SOFT,True)]:
                    with self.lock:
                        exists=self.db.execute('select 1 from r3_hft_control_runs_v2 where strategy_version=? and market_id=?',(ver,mid)).fetchone()
                    if exists:continue
                    try:
                        rep=ctl.run_market(mid,soft); sr=rep['studentRollout']; port=sr['finalPortfolio']; rc=rep['r3Control']; summ={'marketId':mid,'strategyVersion':ver,'makerFillEvents':sr['makerFillEvents'],'makerFilledShares':sr['makerFilledShares'],'takerFills':sr['takerFills'],'finalAbsNet':port.get('combined_abs_net'),'worstCaseFloor':port.get('worst_case_floor'),'makerNet':port.get('maker_net'),'vetoStrong':rc['vetoStrong'],'weakSubstitutions':rc['weakSubstitutions'],'contextualEvents':rc['contextualEvents'],'execution':'HFTBACKTEST_R3_CONTEXT_CONTROL_V0','paperOnly':True}
                        with self.lock:
                            self.db.execute('insert or replace into r3_hft_control_runs_v2 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ver,mid,'COMPLETE',int(sr['makerFillEvents']),float(sr['makerFilledShares']),int(sr['takerFills']),float(port.get('combined_abs_net') or 0.),float(port.get('worst_case_floor') or 0.),float(port.get('maker_net') or 0.),int(rc['vetoStrong']),int(rc['weakSubstitutions']),int(rc['contextualEvents']),json.dumps(summ,separators=(',',':')),int(time.time()*1000)));self.db.commit()
                    except Exception as e:
                        with self.lock:
                            self.db.execute('insert or replace into r3_hft_control_runs_v2(strategy_version,market_id,status,summary_json,completed_at_ms) values(?,?,?,?,?)',(ver,mid,'ERROR',json.dumps({'error':f'{type(e).__name__}: {e}'},separators=(',',':')),int(time.time()*1000)));self.db.commit()
        stats={'sourceChecks':checks,'marketsRecomputed':recomputed,'marketsSkipped':skipped,'durationMs':round((time.monotonic()-started)*1000,3)}
        with self.lock:
            self.last_tick_stats=stats; self.total_source_checks+=checks; self.total_markets_recomputed+=recomputed; self.total_markets_skipped+=skipped
    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
                with self.lock:self.last_error=None
            except Exception as e:
                with self.lock:self.last_error=f'{type(e).__name__}: {e}'
            with self.lock:self.last_loop=int(time.time()*1000)
            self.stop.wait(5)
    def start(self):threading.Thread(target=self.loop,daemon=True,name='r3-dual-paper-shadow-v2').start()
    def snapshot(self):
        with self.lock:
            lanes=[dict(r) for r in self.db.execute('select strategy_version,lane,count(*) markets,sum(checkpoint_count) checkpoints,sum(switch_count) switches,sum(safe_cross_observed) safe_cross_markets,sum(repair_intent_count) repair_intents,sum(context_nontrivial_count) contextual_checkpoints,max(updated_at_ms) updated_at_ms from r3_shadow_market_v2 group by strategy_version,lane order by lane,strategy_version')]
            latest=[dict(r) for r in self.db.execute('select * from r3_shadow_market_v2 order by updated_at_ms desc,market_id desc limit 12')]
            hft_runs=[dict(r) for r in self.db.execute("select strategy_version,count(*) runs,sum(case when status='COMPLETE' then 1 else 0 end) complete,max(completed_at_ms) updated_at_ms from r3_hft_control_runs_v2 group by strategy_version")]
            return {'ok':self.last_error is None,'status':'ACTIVE' if self.last_error is None else 'DEGRADED','version':VERSION,'paperOnly':True,'liveOrdersAffected':False,'r21ActionAuthority':False,'r3LiveOrderAuthority':False,'r3PaperOrderAuthority':True,'fullContextPacket':True,'makerControlIntentEnabled':True,'makerControlExecution':'HFT_ACTUAL_PAPER_CONTROL_ON_NEW_COMPLETE_MARKETS','hftControlActivationAfterMarket':self.hft_control_activation_market,'database':str(OUT_DB),'lastLoopMs':self.last_loop,'lastError':self.last_error,'incrementalRefreshEnabled':True,'recomputePolicy':'CHANGED_MARKETS_ONLY','inferenceThreadLimit':1,'sourceFingerprintVersion':SOURCE_FINGERPRINT_VERSION,'lastTick':dict(self.last_tick_stats),'totals':{'sourceChecks':self.total_source_checks,'marketsRecomputed':self.total_markets_recomputed,'marketsSkipped':self.total_markets_skipped},'lanes':lanes,'hftControlRuns':hft_runs,'latest':latest}

class H(BaseHTTPRequestHandler):
    runtime=None
    def log_message(self,*a):pass
    def do_GET(self):
        b=json.dumps(self.runtime.snapshot(),default=str,separators=(',',':')).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)

def main():
    r=Runtime(); r.start(); h=type('R3DualPaperHandlerV2',(H,),{'runtime':r}); s=ThreadingHTTPServer(('127.0.0.1',PORT),h); print(f'{VERSION} http://127.0.0.1:{PORT}/state paperOnly=true fullContextPacket=true makerControlIntent=true',flush=True)
    try:s.serve_forever(.5)
    finally:r.stop.set(); s.server_close(); r.db.close()
if __name__=='__main__':main()
