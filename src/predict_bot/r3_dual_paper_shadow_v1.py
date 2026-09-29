from __future__ import annotations
import json, math, os, sqlite3, threading, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import joblib, numpy as np

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'data/research/r3_v0'
DREAM_DB=ROOT/'data/strategy_target_compare_v1.db'
HFT_DB=ROOT/'data/hft_forward_paper_v1.db'
OUT_DB=ROOT/'data/r3_dual_paper_shadow_v1.db'
PORT=int(os.environ.get('R3_DUAL_PAPER_PORT','8795'))
VERSION='R3_DUAL_PAPER_SHADOW_V1'
BASE='R3_FORMATION_BASELINE'
SOFT='R3_FORMATION_R21_SOFT_V4'
DREAM_SOURCE='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['model']
CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')['model']
FEATS=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['features']

def conn(path,ro=False):
    if ro:
        c=sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True,timeout=10)
        c.execute('pragma query_only=on')
    else:
        c=sqlite3.connect(path,timeout=10,check_same_thread=False); c.execute('pragma journal_mode=WAL'); c.execute('pragma busy_timeout=5000')
    c.row_factory=sqlite3.Row; return c

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
        hist.append((t,role,side,sh,ss,fl,ups))
        while hist and t-hist[0][0]>15000:hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':float(ss-old[4]),'floor_change_5s':float(fl-old[5]),'upside_change_5s':float(ups-old[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':(t-start)/max(1,end-start)}
        out.append((t,f)); prev=t
    return out

def run_states(rows,soft=False,context_uncertainty=None):
    state='ALLOW_ASYMMETRY'; last=rows[0][0] if rows else 0; out=[]
    for i,(t,f) in enumerate(rows):
        X=np.asarray([[float(f[k]) for k in FEATS]],float); pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1]); unc=0.0 if not soft else float(context_uncertainty(i,t,f) if context_uncertainty else 0.0)
        unc=max(0.,min(1.,unc)); bon=.48+.02*unc; boff=.38-.01*unc; con=.35+.015*unc; can=(t-last)>=1000; ns=state
        if can and pc>=con:ns='CROSSING_PROTECTION'
        elif state=='CROSSING_PROTECTION':
            if can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
        elif state=='BUILD_WEAK_SIDE':
            if can and pb<boff:ns='ALLOW_ASYMMETRY'
        elif can and pb>=bon:ns='BUILD_WEAK_SIDE'
        changed=ns!=state
        if changed:state=ns; last=t
        out.append((t,state,changed,pb,pc,unc,f))
    return out

class Runtime:
    def __init__(self):
        if os.environ.get('PREDICT_LIVE_ENABLED','false').lower() not in {'0','false','no','off',''}: raise RuntimeError('R3 dual paper refuses live-enabled environment')
        self.db=conn(OUT_DB); self.lock=threading.RLock(); self.stop=threading.Event(); self.last_error=None; self.last_loop=None; self.started=int(time.time()*1000)
        self.db.executescript('''
        create table if not exists r3_shadow_states_v1(strategy_version text,lane text,market_id integer,checkpoint_ms integer,formation_state text,state_changed integer,build_p real,cross_p real,r21_uncertainty real,floor real,surplus_shares real,floor_change_5s real,surplus_change_5s real,weak_side_shares_15s real,payload_json text,primary key(strategy_version,lane,market_id,checkpoint_ms));
        create table if not exists r3_shadow_market_v1(strategy_version text,lane text,market_id integer,checkpoint_count integer,switch_count integer,safe_cross_observed integer,last_state text,updated_at_ms integer,primary key(strategy_version,lane,market_id));
        '''); self.db.commit()
    def _dream_events(self,mid):
        c=conn(DREAM_DB,True); rr=c.execute('select filled_at_ms,channel,side,price,shares from our_fills where strategy_version=? and market_id=? order by filled_at_ms,fill_id',(DREAM_SOURCE,mid)).fetchall(); c.close(); return [(int(r[0]),str(r[1]),str(r[2]),float(r[3]),float(r[4])) for r in rr]
    def _dream_unc(self,mid):
        c=conn(DREAM_DB,True); rows=c.execute('select placed_at_ms,status,filled_at_ms,cancelled_at_ms,shares from our_orders where strategy_version=? and market_id=? order by placed_at_ms',(DREAM_SOURCE,mid)).fetchall(); c.close()
        def fn(i,t,f):
            live=sum(1 for r in rows if int(r['placed_at_ms'])<=t and ((r['status']=='ACTIVE') or (r['filled_at_ms'] and int(r['filled_at_ms'])>t) or (r['cancelled_at_ms'] and int(r['cancelled_at_ms'])>t)))
            return min(1.,live/4.)
        return fn
    def _hft_events(self,mid):
        c=conn(HFT_DB,True); rr=c.execute("select fill_ms,channel,side,price,shares from hft_forward_fills_v1 where strategy_key='R2' and market_id=? order by fill_ms,fill_seq",(mid,)).fetchall(); c.close(); return [(int(r[0]),str(r[1]),str(r[2]),float(r[3]),float(r[4])) for r in rr]
    def _hft_unc(self,mid):
        def fn(i,t,f): return min(1.,.45*f['last_role_taker']+.35*(1. if f['age_since_last_ms']<1000 else 0.)+.20*min(1.,f['events_5s']/5.))
        return fn
    def _write(self,lane,mid,events,uncfn):
        rows=features(events)
        for ver,soft in [(BASE,False),(SOFT,True)]:
            pred=run_states(rows,soft,uncfn)
            self.db.execute('delete from r3_shadow_states_v1 where strategy_version=? and lane=? and market_id=?',(ver,lane,mid))
            for t,state,ch,pb,pc,unc,f in pred:
                payload={'paperOnly':True,'liveOrdersAffected':False,'r21ActionAuthority':False,'r3LiveOrderAuthority':False,'contextSource':'OWN_ORDER_LIFECYCLE' if lane=='DREAM_FILL_PAPER' else 'HFT_OWN_STATE','executionEvidence':lane}
                self.db.execute('insert or replace into r3_shadow_states_v1 values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ver,lane,mid,t,state,int(ch),pb,pc,unc,f['floor'],f['surplus_shares'],f['floor_change_5s'],f['surplus_change_5s'],f['opp_side_shares_15s'],json.dumps(payload,separators=(',',':'))))
            switches=sum(int(x[2]) for x in pred); safe=int(any(x[1]=='CROSSING_PROTECTION' for x in pred)); last=pred[-1][1] if pred else None
            self.db.execute('insert or replace into r3_shadow_market_v1 values(?,?,?,?,?,?,?,?)',(ver,lane,mid,len(pred),switches,safe,last,int(time.time()*1000)))
        self.db.commit()
    def tick(self):
        dc=conn(DREAM_DB,True); dm=[int(r[0]) for r in dc.execute('select distinct market_id from our_fills where strategy_version=? order by market_id desc limit 8',(DREAM_SOURCE,)).fetchall()]; dc.close()
        for mid in dm:self._write('DREAM_FILL_PAPER',mid,self._dream_events(mid),self._dream_unc(mid))
        hc=conn(HFT_DB,True); hm=[int(r[0]) for r in hc.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by market_id desc limit 8").fetchall()]; hc.close()
        for mid in hm:self._write('HFT_PAPER',mid,self._hft_events(mid),self._hft_unc(mid))
    def loop(self):
        while not self.stop.is_set():
            try:
                with self.lock:self.tick(); self.last_error=None
            except Exception as e:self.last_error=f'{type(e).__name__}: {e}'
            self.last_loop=int(time.time()*1000); self.stop.wait(5)
    def start(self):threading.Thread(target=self.loop,daemon=True,name='r3-dual-paper-shadow').start()
    def snapshot(self):
        with self.lock:
            rows=[dict(r) for r in self.db.execute('select strategy_version,lane,count(*) markets,sum(checkpoint_count) checkpoints,sum(switch_count) switches,sum(safe_cross_observed) safe_cross_markets,max(updated_at_ms) updated_at_ms from r3_shadow_market_v1 group by strategy_version,lane order by lane,strategy_version').fetchall()]
            latest=[dict(r) for r in self.db.execute('select * from r3_shadow_market_v1 order by updated_at_ms desc,market_id desc limit 12').fetchall()]
            return {'ok':self.last_error is None,'status':'ACTIVE' if self.last_error is None else 'DEGRADED','version':VERSION,'paperOnly':True,'liveOrdersAffected':False,'r21ActionAuthority':False,'r3LiveOrderAuthority':False,'dreamAndHftKeptSeparate':True,'database':str(OUT_DB),'lastLoopMs':self.last_loop,'lastError':self.last_error,'lanes':rows,'latest':latest}

class H(BaseHTTPRequestHandler):
    runtime=None
    def log_message(self,*a):pass
    def do_GET(self):
        b=json.dumps(self.runtime.snapshot(),default=str,separators=(',',':')).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)

def main():
    r=Runtime(); r.start(); h=type('R3DualPaperHandler',(H,),{'runtime':r}); s=ThreadingHTTPServer(('127.0.0.1',PORT),h); print(f'{VERSION} http://127.0.0.1:{PORT}/state paperOnly=true',flush=True)
    try:s.serve_forever(.5)
    finally:r.stop.set(); s.server_close(); r.db.close()
if __name__=='__main__':main()
