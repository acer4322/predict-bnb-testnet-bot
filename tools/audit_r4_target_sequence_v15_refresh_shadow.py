from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
import torch
import torch.nn as nn

ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import audit_r4_target_sequence_v12_wholemarket_shadow as v12

OBJF=['objective_is_repair','objective_is_add','objective_side_is_current_weak','objective_side_is_current_dominant','log_objective_age','log_since_same_objective_parent','same_objective_parent_count_5s','same_objective_parent_count_15s']
FEATURES=v12.FEATURES+OBJF
LABELS=['sameObjectiveParent1s','sameObjectiveParent3s','sameObjectiveTaker3s','sameObjectiveMaker3s']
SEQ=v12.SEQ
EPS=1e-9

class Seq(nn.Module):
    def __init__(self,fd):
        super().__init__();self.inp=nn.Sequential(nn.Linear(fd,96),nn.LayerNorm(96),nn.GELU());self.pos=nn.Parameter(torch.zeros(1,SEQ,96));enc=nn.TransformerEncoderLayer(96,4,256,.1,batch_first=True,norm_first=True,activation='gelu');self.enc=nn.TransformerEncoder(enc,3);self.norm=nn.LayerNorm(96);self.out=nn.Linear(96,len(LABELS))
    def forward(self,x,m):return self.out(self.norm(self.enc(self.inp(x)+self.pos,src_key_padding_mask=m)[:,-1]))

def phase_of(sec): return 'FORMATION_180_300' if sec>180 else 'MANAGEMENT_60_180' if sec>=60 else 'PROTECTION_0_60'

def ext_hist_token(cont,action,commit,price):
    return np.concatenate([v12.token(cont,action,commit,price,False),np.zeros(len(OBJF),np.float32)]).astype(np.float32)

def objective_query(cont,history,now,key,weak,dom):
    prior=[h for h in history if int(h['t'])<int(now)]
    p15=[h for h in prior if int(h['t'])>=int(now)-15000]
    ki=[h for h in p15 if h['key']==key]
    if not ki:return None,None,None
    objpur,objside=key.split('|',1);run_start=ki[0];last=ki[-1]
    ctx=np.asarray([
        1. if objpur=='REPAIR' else 0.,1. if objpur=='ADD' else 0.,1. if objside==weak else 0.,1. if objside==dom else 0.,
        np.log1p(max(0.,(int(now)-int(run_start['t']))/1000.)),np.log1p(max(0.,(int(now)-int(last['t']))/1000.)),
        sum(int(h['t'])>=int(now)-5000 for h in ki)/5.,len(ki)/15.
    ],dtype=np.float32)
    q=np.concatenate([v12.token(cont,'MAKER_REPAIR',0.,0.,True),ctx]).astype(np.float32)
    hp=prior[-(SEQ-1):];arr=np.zeros((SEQ,len(FEATURES)),np.float32);mask=np.ones(SEQ,bool);st=SEQ-1-len(hp)
    if hp: arr[st:SEQ-1]=np.stack([h['token'] for h in hp]);mask[st:SEQ-1]=False
    arr[-1]=q;mask[-1]=False
    return arr,mask,{'objectivePurpose':objpur,'objectiveSide':objside,'objectiveAgeS':(int(now)-int(run_start['t']))/1000.,'sinceSameObjectiveParentS':(int(now)-int(last['t']))/1000.,'sameObjectiveParents5s':sum(int(h['t'])>=int(now)-5000 for h in ki),'sameObjectiveParents15s':len(ki)}

def run_market(mid,wend,model,device):
    orig_new=base.new_controller;states=[];queries=[];actions=[];diag=Counter()
    def audit_new(a):
        c=orig_new(a);orig_step=c._step;history=[];wrapped={'done':False};last_q=-1
        def append_action(role,purpose,side,at,cont,raw,shares,price):
            if purpose not in {'REPAIR','ADD'}:return
            action=f'{role}_{purpose}'
            if action not in v12.A2I:return
            gap=max(v12.safe(raw['abs_gap']),18.);commit=float(np.log1p(max(0.,v12.safe(shares))/gap));key=f'{purpose}|{side}'
            history.append({'t':int(at),'role':role,'purpose':purpose,'side':side,'key':key,'token':ext_hist_token(cont,action,commit,np.clip(v12.safe(price),0.,1.))})
            actions.append({'atMs':int(at),'role':role,'purpose':purpose,'side':side,'key':key,'shares':v12.safe(shares),'price':v12.safe(price),'secondsLeft':v12.safe(raw['seconds_left'])})
        def install():
            if wrapped['done']:return
            wrapped['done']=True;final_add=c._add_order;final_taker=c._record_taker
            def add_shadow(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
                try:raw,cont,portable,weak,mem=v12.state_runtime(c,a,int(now),wend,history)
                except Exception:raw=cont=weak=None
                before=set(c.orders.keys());made=final_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
                if made and raw is not None:
                    new=[o for k,o in c.orders.items() if k not in before and int(getattr(o,'placed_at_ms',-1))==int(now)]
                    if new:
                        o=new[-1];actual=str(o.side);strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;purpose='REPAIR' if weak and actual==weak else 'ADD' if strong and actual==strong else 'FLAT';append_action('MAKER',purpose,actual,int(now),cont,raw,v12.safe(o.shares),v12.safe(o.price))
                return made
            def taker_shadow(side,price,now,decision_id,snapshot,raw_dec,p1,p3,ppass,pred_effect):
                try:raw,cont,portable,weak,mem=v12.state_runtime(c,a,int(now),wend,history)
                except Exception:raw=cont=weak=None
                n0=len(c.inventory.events);ok=final_taker(side,price,now,decision_id,snapshot,raw_dec,p1,p3,ppass,pred_effect)
                if ok and raw is not None:
                    ev=[e for e in c.inventory.events[n0:] if str(e.get('role'))=='TAKER'];e=ev[-1] if ev else None;actual=str(side);strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;purpose='REPAIR' if weak and actual==weak else 'ADD' if strong and actual==strong else 'FLAT';append_action('TAKER',purpose,actual,int(now),cont,raw,v12.safe(e.get('shares') if e else 0.),v12.safe(e.get('price') if e else price))
                return ok
            c._add_order=add_shadow;c._record_taker=taker_shadow
        def step_shadow(s):
            nonlocal last_q
            install();out=orig_step(s);qbase=max(int(s.get('sampledAtMs') or 0),int(a.bt.current_timestamp//1_000_000));qnow=qbase+1
            if qnow<=last_q or qnow>=int(wend):return out
            last_q=qnow
            try:
                raw,cont,portable,weak,mem=v12.state_runtime(c,a,qnow,wend,history);sec=float(raw['seconds_left']);dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None;states.append({'atMs':qnow,'floor':v12.safe(raw.get('floor')),'absNet':v12.safe(raw.get('abs_gap')),'secondsLeft':sec})
                prior15=[h for h in history if int(h['t'])<qnow and int(h['t'])>=qnow-15000]
                for key in sorted(set(h['key'] for h in prior15)):
                    arr,mask,obj=objective_query(cont,history,qnow,key,weak,dom)
                    if arr is None:continue
                    rec={'marketId':int(mid),'atMs':qnow,'phase':phase_of(sec),'secondsLeft':sec,'weakSide':weak,'dominantSide':dom,'floor':v12.safe(raw.get('floor')),'absNet':v12.safe(raw.get('abs_gap')),'coverage':v12.safe(raw.get('coverage')),'weakActiveOwners':v12.safe(raw.get('weak_active_roots')),'weakUnresolvedShares':v12.safe(raw.get('weak_unresolved_shares')),'weakProgressRatio':v12.safe(raw.get('weak_progress_ratio')),'weakFillShares5s':v12.safe(raw.get('weak_fill_shares_5s')),**obj,'X':arr,'M':mask}
                    rec['managementRepairWeak']=bool(rec['phase']=='MANAGEMENT_60_180' and obj['objectivePurpose']=='REPAIR' and obj['objectiveSide']==weak)
                    queries.append(rec)
            except Exception as e:diag['queryErrors']+=1
            return out
        c._step=step_shadow;return c
    base.new_controller=audit_new
    try:rep=r3ctl.run_market(int(mid),True)
    finally:base.new_controller=orig_new
    if queries:
        X=np.stack([q.pop('X') for q in queries]);M=np.stack([q.pop('M') for q in queries]);P=[]
        with torch.no_grad():
            for i in range(0,len(X),512):P.append(torch.sigmoid(model(torch.from_numpy(X[i:i+512]).to(device),torch.from_numpy(M[i:i+512]).to(device))).cpu().numpy())
        P=np.concatenate(P)
        bykey=defaultdict(list)
        for a in actions:bykey[a['key']].append(a)
        stt=np.asarray([s['atMs'] for s in states],np.int64)
        for i,q in enumerate(queries):
            for j,l in enumerate(LABELS):q['p'+l[0].upper()+l[1:]]=float(P[i,j])
            future=[a for a in bykey.get(q['objectivePurpose']+'|'+q['objectiveSide'],[]) if int(a['atMs'])>q['atMs'] and int(a['atMs'])<=q['atMs']+3000]
            q['actualSameObjectiveParent3s']=bool(future);q['actualSameObjectiveTaker3s']=bool(any(a['role']=='TAKER' for a in future));q['actualSameObjectiveMaker3s']=bool(any(a['role']=='MAKER' for a in future))
            q['takerShadowPositive']=bool(q['pSameObjectiveTaker3s']>=0.5);q['missedTakerEscalationShadow']=bool(q['managementRepairWeak'] and q['takerShadowPositive'] and not q['actualSameObjectiveTaker3s'])
            k=int(np.searchsorted(stt,q['atMs']+15000,'left'))
            if k<len(states):q['futureFloorDelta15s']=float(states[k]['floor']-q['floor']);q['futureAbsNetDelta15s']=float(states[k]['absNet']-q['absNet'])
            else:q['futureFloorDelta15s']=None;q['futureAbsNetDelta15s']=None
    s=rep['studentRollout'];fp=s['finalPortfolio']
    return {'marketId':int(mid),'queries':queries,'actualActions':actions,'diag':dict(diag),'r3Summary':{'makerFillEvents':s['makerFillEvents'],'makerFilledShares':s['makerFilledShares'],'takerFills':s['takerFills'],'finalAbsNet':fp.get('combined_abs_net'),'finalFloor':fp.get('worst_case_floor')}}

def summarize(rows):
    qs=[q for r in rows for q in r.get('queries',[])];mr=[q for q in qs if q.get('managementRepairWeak')];pos=[q for q in mr if q.get('takerShadowPositive')];miss=[q for q in mr if q.get('missedTakerEscalationShadow')]
    def mean(z,k):
        v=[float(x[k]) for x in z if x.get(k) is not None and math.isfinite(float(x[k]))];return float(np.mean(v)) if v else None
    def med(z,k):
        v=[float(x[k]) for x in z if x.get(k) is not None and math.isfinite(float(x[k]))];return float(np.median(v)) if v else None
    bymarket=Counter(q['marketId'] for q in miss)
    return {'markets':len(rows),'errors':sum('error' in r for r in rows),'objectiveQueries':len(qs),'managementRepairWeakQueries':len(mr),'takerShadowPositive':len(pos),'takerShadowPositiveMarkets':len(set(q['marketId'] for q in pos)),'actualSameObjectiveTaker3sAmongPositive':sum(q['actualSameObjectiveTaker3s'] for q in pos),'missedTakerEscalationShadow':len(miss),'missedMarkets':len(bymarket),'topMissedMarkets':bymarket.most_common(10),'positiveOwnerPresentRate':mean(pos,'weakActiveOwners'),'missedOwnerPresentRate':mean(miss,'weakActiveOwners'),'positiveMedianProgress':med(pos,'weakProgressRatio'),'missedMedianProgress':med(miss,'weakProgressRatio'),'positiveMeanFutureFloorDelta15s':mean(pos,'futureFloorDelta15s'),'missedMeanFutureFloorDelta15s':mean(miss,'futureFloorDelta15s'),'missedMeanFutureAbsNetDelta15s':mean(miss,'futureAbsNetDelta15s'),'warning':'Future actions and 15s portfolio deltas are scoring-only observational diagnostics; no action authority.'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--checkpoint',required=True);ap.add_argument('--ids-json',required=True);ap.add_argument('--window-map-json',required=True);ap.add_argument('--out',required=True);ap.add_argument('--data-root',default=None);args=ap.parse_args();
    if args.data_root:
        d=Path(args.data_root).resolve();base.STRATEGY_DB=d/'strategy_target_compare_v1.db';base.mod.BOOK_DB=d/'wallet_maker_book_inference.db';base.ex.BOOK_DB=d/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=d/'execution_tape_v1/markets'
    ids=json.loads(Path(args.ids_json).read_text());wm=json.loads(Path(args.window_map_json).read_text());ck=torch.load(args.checkpoint,map_location='cpu',weights_only=False);device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu');model=Seq(len(FEATURES));model.load_state_dict(ck['stateDict']);model.to(device).eval();rows=[]
    for mid in ids:
        try:r=run_market(int(mid),int(wm[str(int(mid))]),model,device)
        except Exception as e:r={'marketId':int(mid),'error':f'{type(e).__name__}:{e}','queries':[],'actualActions':[]}
        rows.append(r);print(json.dumps({'marketId':int(mid),'queries':len(r.get('queries',[])),'missed':sum(q.get('missedTakerEscalationShadow',False) for q in r.get('queries',[])),'error':r.get('error')},ensure_ascii=False),flush=True)
    out={'version':'R4_TARGET_SEQUENCE_V1_5_REFRESH_WHOLEMARKET_SHADOW_V1','researchOnly':True,'actionAuthority':False,'checkpointVersion':ck.get('version'),'labels':ck.get('labels'),'guards':['strict-past model inputs','same-objective history only','Management ADD ignored for authority','no order mutation','future actions/floor only scoring','0-60 Protection not acted upon'],'summary':summarize(rows),'markets':rows};Path(args.out).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['summary'],indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()

