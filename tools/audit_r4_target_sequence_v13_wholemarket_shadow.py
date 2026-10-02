from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import joblib
import torch
import torch.nn as nn

ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features

SEQ=32
ACTIONS=['MAKER_REPAIR','MAKER_ADD','TAKER_REPAIR','TAKER_ADD','MAKER_FLAT','TAKER_FLAT']
A2I={x:i for i,x in enumerate(ACTIONS)}
BASE_CONT=['seconds_left_norm','log_abs_gap','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','log_weak_active','log_dom_active','weak_unresolved_gap_ratio','dom_unresolved_gap_ratio','weak_progress','dom_progress','weak_fill5_gap_ratio','dom_fill5_gap_ratio','events5_rate','events15_rate','transitions15_rate','log_since_prev','last_purpose_run_age_log']
FEATURES=BASE_CONT+[f'hist_{x}' for x in ACTIONS]+['hist_commitment','hist_price','is_query']
SUPPORT_F=['seconds_left_norm','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','events_5s_rate','events_15s_rate','transitions_15s_rate','mode_age_log','weak_active_roots','dominant_active_roots','weak_unresolved_gap_ratio','dominant_unresolved_gap_ratio','weak_progress_ratio','dominant_progress_ratio','weak_fill5_gap_ratio','dominant_fill5_gap_ratio']
EPS=1e-9

def safe(v,d=0.):
    try:
        x=float(v); return x if math.isfinite(x) else d
    except Exception:return d

def memory(history,now):
    h=[x for x in history if int(x['t'])<int(now)]
    p5=[x for x in h if int(now)-int(x['t'])<=5000]
    p15=[x for x in h if int(now)-int(x['t'])<=15000]
    trans=sum(p15[i]['purpose']!=p15[i-1]['purpose'] for i in range(1,len(p15))) if len(p15)>1 else 0
    since=(int(now)-int(h[-1]['t']))/1000. if h else 999.
    age=0.
    if h:
        lp=h[-1]['purpose']; st=int(h[-1]['t']); k=len(h)-1
        while k>0 and h[k-1]['purpose']==lp and int(h[k]['t'])-int(h[k-1]['t'])<=15000:
            st=int(h[k-1]['t']); k-=1
        age=max(0.,(int(now)-st)/1000.)
    return {'events5':len(p5),'events15':len(p15),'transitions15':trans,'since_prev':since,'last_purpose_run_age':age}

def state_runtime(c,a,now,wend,history):
    up=float(c.inventory.maker_up+c.inventory.taker_up); dn=float(c.inventory.maker_down+c.inventory.taker_down)
    gross=up+dn; abs_gap=abs(up-dn); weak='DOWN' if up>dn+EPS else 'UP' if dn>up+EPS else None
    port=c.inventory.features(int(now)); floor=safe(port.get('worst_case_floor'),0.); upside=safe(port.get('best_case_pnl'),floor+abs_gap)
    try: mf,_=mgmt_features(c,a,int(now),{'floor':floor})
    except Exception:
        mf={'weak_active_owners':0,'dominant_active_owners':0,'weak_unresolved_shares':0.,'dominant_unresolved_shares':0.,'weak_progress_ratio':0.,'dominant_progress_ratio':0.,'weak_fill_shares_5s':0.,'dominant_fill_shares_5s':0.}
    sec=max(0.,min(300.,(int(wend)-int(now))/1000.)); cov=2.*min(up,dn)/gross if gross>EPS else 0.; mem=memory(history,now); gap=max(abs_gap,18.)
    raw={'seconds_left':sec,'abs_gap':abs_gap,'risk_deficit':max(0.,-floor),'floor':floor,'upside':upside,'coverage':cov,'floor_per_gross':floor/gross if gross>EPS else 0.,'gross':gross,
         'weak_active_roots':safe(mf.get('weak_active_owners')),'dominant_active_roots':safe(mf.get('dominant_active_owners')),'weak_unresolved_shares':safe(mf.get('weak_unresolved_shares')),'dominant_unresolved_shares':safe(mf.get('dominant_unresolved_shares')),
         'weak_progress_ratio':safe(mf.get('weak_progress_ratio')),'dominant_progress_ratio':safe(mf.get('dominant_progress_ratio')),'weak_fill_shares_5s':safe(mf.get('weak_fill_shares_5s')),'dominant_fill_shares_5s':safe(mf.get('dominant_fill_shares_5s'))}
    cont=np.asarray([sec/300.,np.log1p(abs_gap),raw['risk_deficit']/gap,floor/gap,upside/gap,np.clip(cov,0.,1.2),np.clip(raw['floor_per_gross'],-3.,3.),np.log1p(max(0.,raw['weak_active_roots'])),np.log1p(max(0.,raw['dominant_active_roots'])),
        np.clip(raw['weak_unresolved_shares']/gap,0.,8.),np.clip(raw['dominant_unresolved_shares']/gap,0.,8.),np.clip(raw['weak_progress_ratio'],0.,1.),np.clip(raw['dominant_progress_ratio'],0.,1.),np.clip(raw['weak_fill_shares_5s']/gap,0.,8.),np.clip(raw['dominant_fill_shares_5s']/gap,0.,8.),
        mem['events5']/5.,mem['events15']/15.,mem['transitions15']/15.,np.log1p(max(0.,mem['since_prev'])),np.log1p(max(0.,mem['last_purpose_run_age']))],dtype=np.float32)
    portable={'seconds_left_norm':sec/300.,'risk_deficit_gap_ratio':raw['risk_deficit']/gap,'floor_gap_ratio':floor/gap,'upside_gap_ratio':upside/gap,'coverage':cov,'floor_per_gross':raw['floor_per_gross'],'events_5s_rate':mem['events5']/5.,'events_15s_rate':mem['events15']/15.,'transitions_15s_rate':mem['transitions15']/15.,'mode_age_log':float(np.log1p(max(0.,mem['last_purpose_run_age']))),'weak_active_roots':raw['weak_active_roots'],'dominant_active_roots':raw['dominant_active_roots'],'weak_unresolved_gap_ratio':raw['weak_unresolved_shares']/gap,'dominant_unresolved_gap_ratio':raw['dominant_unresolved_shares']/gap,'weak_progress_ratio':raw['weak_progress_ratio'],'dominant_progress_ratio':raw['dominant_progress_ratio'],'weak_fill5_gap_ratio':raw['weak_fill_shares_5s']/gap,'dominant_fill5_gap_ratio':raw['dominant_fill_shares_5s']/gap}
    return raw,cont,portable,weak,mem

def token(cont,action,commit,price,is_query=False):
    z=np.zeros(len(FEATURES),np.float32); z[:len(BASE_CONT)]=cont
    if not is_query:z[len(BASE_CONT)+A2I[action]]=1.
    z[len(BASE_CONT)+len(ACTIONS)]=0. if is_query else float(commit); z[len(BASE_CONT)+len(ACTIONS)+1]=0. if is_query else float(price); z[-1]=1. if is_query else 0.; return z

def query_sequence(cont,history,now):
    q=token(cont,'MAKER_REPAIR',0.,0.,True); prior=[h for h in history if int(h['t'])<int(now)][-(SEQ-1):]; arr=np.zeros((SEQ,len(FEATURES)),np.float32); mask=np.ones(SEQ,bool); st=SEQ-1-len(prior)
    if prior: arr[st:SEQ-1]=np.stack([h['token'] for h in prior]); mask[st:SEQ-1]=False
    arr[-1]=q; mask[-1]=False; return arr,mask

class SeqBinary(nn.Module):
    def __init__(self,fd):
        super().__init__();self.inp=nn.Sequential(nn.Linear(fd,96),nn.LayerNorm(96),nn.GELU());self.pos=nn.Parameter(torch.zeros(1,SEQ,96));enc=nn.TransformerEncoderLayer(96,4,256,.1,batch_first=True,norm_first=True,activation='gelu');self.enc=nn.TransformerEncoder(enc,3);self.norm=nn.LayerNorm(96);self.out=nn.Linear(96,2)
    def forward(self,x,m):return self.out(self.norm(self.enc(self.inp(x)+self.pos,src_key_padding_mask=m)[:,-1]))
class HazardSeq(nn.Module):
    def __init__(self,fd):
        super().__init__();self.inp=nn.Sequential(nn.Linear(fd,96),nn.LayerNorm(96),nn.GELU());self.pos=nn.Parameter(torch.zeros(1,SEQ,96));enc=nn.TransformerEncoderLayer(96,4,256,.1,batch_first=True,norm_first=True,activation='gelu');self.enc=nn.TransformerEncoder(enc,3);self.norm=nn.LayerNorm(96);self.out=nn.Linear(96,3)
    def forward(self,x,m):return self.out(self.norm(self.enc(self.inp(x)+self.pos,src_key_padding_mask=m)[:,-1]))

def load_deep(adir,device):
    hz=torch.load(adir/'r4_target_sequence_hazard_v1.pt',map_location='cpu',weights_only=False); fc=torch.load(adir/'r4_target_sequence_teacher_v11_factorized.pt',map_location='cpu',weights_only=False)
    h=HazardSeq(len(FEATURES));h.load_state_dict(hz['stateDict']);h.to(device).eval(); p=SeqBinary(len(FEATURES));p.load_state_dict(fc['states']['purposeBinary']);p.to(device).eval(); r=SeqBinary(len(FEATURES));r.load_state_dict(fc['states']['roleBinary']);r.to(device).eval(); return h,p,r,joblib.load(adir/'r4_state_shaping_transport_support_gate_v1.joblib')

def support_score(gate,portable):
    f=gate['features']; x=np.asarray([[safe(portable[k]) for k in f]],float); med=np.asarray([safe(gate['median'][k]) for k in f]);iqr=np.asarray([max(safe(gate['iqr'][k],1.),1e-6) for k in f]); sc=float(gate['isolationForest'].score_samples((x-med)/iqr)[0]);return sc,bool(sc>=float(gate['supportThreshold']))

def run_market(mid,wend,models,device,adir):
    hz,pm,rm,gate=models; orig_new=base.new_controller; queries=[]; action_rows=[]; diag=Counter()
    def audit_new(a):
        c=orig_new(a); orig_step=c._step; history=[]; wrapped={'done':False}; last_q=-1
        def append_action(role,purpose,at,cont,raw,shares,price):
            action=f'{role}_{purpose}';
            if action=='MAKER_FLAT':diag['makerFlatOmittedFromHistory']+=1;return
            if action not in A2I:return
            gap=max(safe(raw['abs_gap']),18.);commit=float(np.log1p(max(0.,safe(shares))/gap));history.append({'t':int(at),'role':role,'purpose':purpose,'action':action,'token':token(cont,action,commit,np.clip(safe(price),0.,1.),False)});action_rows.append({'atMs':int(at),'role':role,'purpose':purpose,'action':action,'shares':safe(shares),'price':safe(price),'secondsLeft':safe(raw['seconds_left'])})
        def install_wrappers():
            if wrapped['done']:return
            wrapped['done']=True; final_add=c._add_order; final_taker=c._record_taker
            def add_shadow(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
                try:raw,cont,portable,weak,mem=state_runtime(c,a,int(now),wend,history)
                except Exception:raw=cont=weak=None
                before=set(c.orders.keys()); made=final_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
                if made and raw is not None:
                    new=[o for k,o in c.orders.items() if k not in before and int(getattr(o,'placed_at_ms',-1))==int(now)]
                    if new:
                        o=new[-1]; actual=str(o.side); strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;purpose='REPAIR' if weak and actual==weak else 'ADD' if strong and actual==strong else 'FLAT';append_action('MAKER',purpose,int(now),cont,raw,safe(o.shares),safe(o.price))
                return made
            def taker_shadow(side,price,now,decision_id,snapshot,raw_dec,p1,p3,ppass,pred_effect):
                try:raw,cont,portable,weak,mem=state_runtime(c,a,int(now),wend,history)
                except Exception:raw=cont=weak=None
                n0=len(c.inventory.events); ok=final_taker(side,price,now,decision_id,snapshot,raw_dec,p1,p3,ppass,pred_effect)
                if ok and raw is not None:
                    ev=[e for e in c.inventory.events[n0:] if str(e.get('role'))=='TAKER']; e=ev[-1] if ev else None; actual=str(side);strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;purpose='REPAIR' if weak and actual==weak else 'ADD' if strong and actual==strong else 'FLAT';append_action('TAKER',purpose,int(now),cont,raw,safe(e.get('shares') if e else 0.),safe(e.get('price') if e else price))
                return ok
            c._add_order=add_shadow;c._record_taker=taker_shadow
        def step_shadow(s):
            nonlocal last_q
            install_wrappers(); out=orig_step(s); qbase=max(int(s.get('sampledAtMs') or 0),int(a.bt.current_timestamp//1_000_000)); qnow=qbase+1
            if qnow<=last_q or qnow>=int(wend):return out
            last_q=qnow
            try:
                raw,cont,portable,weak,mem=state_runtime(c,a,qnow,wend,history); arr,mask=query_sequence(cont,history,qnow); phase='FORMATION_180_300' if raw['seconds_left']>180 else 'MANAGEMENT_60_180' if raw['seconds_left']>=60 else 'PROTECTION_0_60'; queries.append({'atMs':qnow,'phase':phase,'secondsLeft':raw['seconds_left'],'raw':raw,'portable':portable,'X':arr,'M':mask,'historyActions':len([h for h in history if h['t']<qnow])})
            except Exception as e:diag['queryErrors']+=1
            return out
        c._step=step_shadow;return c
    base.new_controller=audit_new
    orig_strategy_db=base.STRATEGY_DB; orig_book_db=base.ex.BOOK_DB; orig_tape_dir=base.tape_v1.ARCHIVE_DIR
    compact_strategy=adir/'strategy_compact.db'; compact_book=adir/'book_compact.db'; compact_tape_dir=adir/'execution_tape_v1'/'markets'
    use_compact=False
    if compact_strategy.exists() and compact_book.exists():
        import sqlite3
        con=sqlite3.connect(compact_strategy)
        try: use_compact=bool(con.execute('select 1 from our_decisions where market_id=? limit 1',(int(mid),)).fetchone())
        finally: con.close()
    if use_compact:
        base.STRATEGY_DB=compact_strategy; base.ex.BOOK_DB=compact_book
    if compact_tape_dir.exists(): base.tape_v1.ARCHIVE_DIR=compact_tape_dir
    try:rep=r3ctl.run_market(int(mid),True)
    finally:
        base.new_controller=orig_new; base.STRATEGY_DB=orig_strategy_db; base.ex.BOOK_DB=orig_book_db; base.tape_v1.ARCHIVE_DIR=orig_tape_dir
    if queries:
        X=np.stack([q.pop('X') for q in queries]);M=np.stack([q.pop('M') for q in queries]);bs=512;ha=[];pp=[];rr=[]
        with torch.no_grad():
            for i in range(0,len(X),bs):
                xb=torch.from_numpy(X[i:i+bs]).to(device);mb=torch.from_numpy(M[i:i+bs]).to(device);ha.append(torch.sigmoid(hz(xb,mb)).cpu().numpy());pp.append(torch.softmax(pm(xb,mb),1)[:,1].cpu().numpy());rr.append(torch.softmax(rm(xb,mb),1)[:,1].cpu().numpy())
        H=np.concatenate(ha);P=np.concatenate(pp);R=np.concatenate(rr)
        sf=list(gate['features']); xdf=pd.DataFrame([[safe(q['portable'][k]) for k in sf] for q in queries],columns=sf); med=pd.Series(gate['median']); iqr=pd.Series({k:max(safe(gate['iqr'][k],1.),1e-6) for k in sf}); scores=gate['isolationForest'].score_samples((xdf-med)/iqr); ins_arr=scores>=float(gate['supportThreshold'])
        for i,q in enumerate(queries):
            sc=float(scores[i]);ins=bool(ins_arr[i]);q.update({'supportScore':sc,'inTargetSupport':ins,'pAnyAction1s':float(H[i,0]),'pTakerAction1s':float(H[i,1]),'pAddAction1s':float(H[i,2]),'pActionTimePurposeAdd':float(P[i]),'pRoleTaker':float(R[i])});anypos=H[i,0]>=.5; add_pre=H[i,2]>=.5; taker=R[i]>=.5; flat=safe(q['raw'].get('abs_gap'))<=1e-6; q['hazardPositive']=bool(anypos);q['deepPurpose']='FLAT_START' if flat else ('ADD' if add_pre else 'REPAIR');q['deepRole']='TAKER' if taker else 'MAKER';q['supportedDeepPositive']=bool(anypos and ins);q['formationNewAddShadow']=bool(q['phase']=='FORMATION_180_300' and anypos and ins and add_pre and not flat);q['blockedNewAddByPhase']=bool(q['phase']!='FORMATION_180_300' and anypos and add_pre and not flat)
    s=rep['studentRollout'];fp=s['finalPortfolio'];return {'marketId':int(mid),'queries':queries,'actualActions':action_rows,'diag':dict(diag),'r3Summary':{'makerFillEvents':s['makerFillEvents'],'makerFilledShares':s['makerFilledShares'],'takerFills':s['takerFills'],'finalAbsNet':fp.get('combined_abs_net'),'finalFloor':fp.get('worst_case_floor')}}

def summarize(markets):
    qs=[q for m in markets for q in m.get('queries',[])]; acts=[a for m in markets for a in m.get('actualActions',[])]; out={'markets':len(markets),'errors':sum('error' in m for m in markets),'queries':len(qs),'actualActions':len(acts),'actualActionByRole':dict(Counter(a['role'] for a in acts)),'actualActionByPurpose':dict(Counter(a['purpose'] for a in acts)),'byPhase':{}}
    for ph in ['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']:
        z=[q for q in qs if q['phase']==ph];a=[x for x in acts if ('FORMATION_180_300' if x['secondsLeft']>180 else 'MANAGEMENT_60_180' if x['secondsLeft']>=60 else 'PROTECTION_0_60')==ph]
        def mean(k):return float(np.mean([q[k] for q in z])) if z else None
        out['byPhase'][ph]={'queries':len(z),'actualActions':len(a),'actualTaker':sum(x['role']=='TAKER' for x in a),'actualAdd':sum(x['purpose']=='ADD' for x in a),'supportRate':mean('inTargetSupport'),'hazardPositiveRate':mean('hazardPositive'),'supportedHazardRate':mean('supportedDeepPositive'),'meanPAny':mean('pAnyAction1s'),'meanPTakerHazard':mean('pTakerAction1s'),'meanPAddHazard':mean('pAddAction1s'),'meanPActionTimePurposeAdd':mean('pActionTimePurposeAdd'),'meanPRoleTaker':mean('pRoleTaker'),'deepTakerAmongHazard':float(np.mean([q['pRoleTaker']>=.5 for q in z if q['hazardPositive']])) if any(q['hazardPositive'] for q in z) else None,'deepAddAmongHazard':float(np.mean([q['pAddAction1s']>=.5 for q in z if q['hazardPositive']])) if any(q['hazardPositive'] for q in z) else None,'formationNewAddShadow':sum(q['formationNewAddShadow'] for q in z),'blockedNewAddByPhase':sum(q['blockedNewAddByPhase'] for q in z)}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--artifact-dir',required=True);ap.add_argument('--ids-json',required=True);ap.add_argument('--window-map-json',required=True);ap.add_argument('--out',required=True);args=ap.parse_args();adir=Path(args.artifact_dir);ids=json.loads(Path(args.ids_json).read_text());wm=json.loads(Path(args.window_map_json).read_text());device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu');models=load_deep(adir,device);rows=[]
    for mid in ids:
        try:r=run_market(int(mid),int(wm[str(int(mid))]),models,device,adir)
        except Exception as e:r={'marketId':int(mid),'error':f'{type(e).__name__}:{e}','queries':[],'actualActions':[]}
        rows.append(r);print(mid,'queries',len(r.get('queries',[])),'actions',len(r.get('actualActions',[])),'error',r.get('error'),flush=True)
    out={'version':'R4_TARGET_SEQUENCE_V1_3_WHOLEMARKET_SHADOW','researchOnly':True,'actionAuthority':False,'device':str(device),'guards':['no order mutation by deep stack','strict-past post-decision source checkpoints only','coverage semantic correction V1 applied','pre-action purpose uses ADD_ACTION1S; action-time Purpose head diagnostic only','<=180s no new exposure','0.5 thresholds frozen before Hazard result','Target-support gate required for supported trigger'],'summary':summarize(rows),'markets':rows};Path(args.out).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out['summary'],indent=2),flush=True)
if __name__=='__main__':main()
