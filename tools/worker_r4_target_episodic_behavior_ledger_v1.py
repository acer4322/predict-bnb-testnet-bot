from __future__ import annotations
import argparse, json, sys
from pathlib import Path
import numpy as np
import pandas as pd
import torch

PHASE={0:'FORMATION_180_300',1:'MANAGEMENT_60_180',2:'PROTECTION_0_60'}

def ownership_context(r):
    w=float(r.get('weak_active_roots',0) or 0); d=float(r.get('dominant_active_roots',0) or 0)
    if w>0 and d>0:return 'BOTH_ACTIVE'
    if w>0:return 'WEAK_ACTIVE'
    if d>0:return 'DOMINANT_ACTIVE'
    return 'NO_ACTIVE_ROOT'

def floor_state(r):
    f=float(r.get('floor',0) or 0)
    if f>1e-9:return 'POSITIVE_FLOOR'
    if f<-1e-9:return 'NEGATIVE_FLOOR'
    return 'ZERO_FLOOR'

def transition(y,p0,p1):
    b=int(p0>=.5); a=int(p1>=.5)
    cb=(b==int(y)); ca=(a==int(y))
    if (not cb) and ca:return 'WRONG_TO_RIGHT'
    if cb and (not ca):return 'RIGHT_TO_WRONG'
    if cb and ca:return 'STAY_RIGHT'
    return 'STAY_WRONG'

def target_prob(y,p):return float(p if int(y)==1 else 1-p)

@torch.no_grad()
def pred_binary(model,X,M,idx,device,b=1024):
    model.eval(); out=[]
    for s in range(0,len(idx),b):
        ii=idx[s:s+b]
        out.append(torch.softmax(model(torch.from_numpy(X[ii]).to(device),torch.from_numpy(M[ii]).to(device)),1)[:,1].cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0,np.float32)

@torch.no_grad()
def pred_hazard(model,X,M,idx,device,b=1024):
    model.eval(); out=[]
    for s in range(0,len(idx),b):
        ii=idx[s:s+b]
        out.append(torch.sigmoid(model(torch.from_numpy(X[ii]).to(device),torch.from_numpy(M[ii]).to(device))).cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0,3),np.float32)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--bundle-dir',required=True)
    ap.add_argument('--fresh-dir',required=True)
    ap.add_argument('--start-checkpoint',required=True)
    ap.add_argument('--final-checkpoint',required=True)
    ap.add_argument('--v4-result',required=True)
    ap.add_argument('--out-dir',required=True)
    ap.add_argument('--cache-out',default='')
    args=ap.parse_args()
    bundle=Path(args.bundle_dir).resolve(); fresh=Path(args.fresh_dir).resolve(); out=Path(args.out_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(bundle))
    import train_r4_target_sequence_teacher_v1 as v1
    import train_r4_target_sequence_teacher_v11_factorized as v11
    import train_r4_target_sequence_hazard_v1 as hz
    device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

    v1.D=fresh; hz.D=fresh
    d=v1.build_rows(); X,M,ya,yc,yf,ph,mids,times,sup=v1.build_sequences(d)
    actions=np.array([v1.ACTIONS[i] for i in ya]); keep=np.array([not a.endswith('_FLAT') for a in actions],bool)
    # build_sequences preserves d row order; verify before using d as context table.
    ds=d.reset_index(drop=True).copy()
    assert len(ds)==len(ya)
    assert np.array_equal(ds.market_id.astype(int).to_numpy(),mids)
    assert np.array_equal(ds.t.astype(np.int64).to_numpy(),times)
    ctx=ds.loc[keep].reset_index(drop=True)
    X=X[keep]; M=M[keep]; ph=ph[keep]; mids=mids[keep]; times=times[keep]; actions=actions[keep]
    purpose=np.array([1 if a.endswith('_ADD') else 0 for a in actions],np.int64)
    role=np.array([1 if a.startswith('TAKER_') else 0 for a in actions],np.int64)
    HX,HM,HY,HPH,HMIDS,HTIMES=hz.build_grid(); HY=HY.astype(np.float32)

    split=json.loads((fresh/'split.json').read_text(encoding='utf-8'))
    split_ids={'GUARD20':set(map(int,split['guard20'])),'FINAL20':set(map(int,split['final20']))}
    def idx(arr,ids):return np.where(np.array([int(x) in ids for x in arr],bool))[0].astype(np.int64)

    c0=torch.load(Path(args.start_checkpoint),map_location='cpu',weights_only=False)
    c1=torch.load(Path(args.final_checkpoint),map_location='cpu',weights_only=False)
    def models(ck):
        p=v11.SeqBinary(X.shape[-1]); p.load_state_dict(ck['states']['purposeBinary']); p.to(device)
        r=v11.SeqBinary(X.shape[-1]); r.load_state_dict(ck['states']['roleBinary']); r.to(device)
        h=hz.HazardSeq(HX.shape[-1]); h.load_state_dict(ck['states']['hazard']); h.to(device)
        return p,r,h
    p0,r0,h0=models(c0); p1,r1,h1=models(c1)

    led=[]
    for split_name,ids in split_ids.items():
        ai=idx(mids,ids); hi=idx(HMIDS,ids)
        pp0=pred_binary(p0,X,M,ai,device); pp1=pred_binary(p1,X,M,ai,device)
        rp0=pred_binary(r0,X,M,ai,device); rp1=pred_binary(r1,X,M,ai,device)
        hp0=pred_hazard(h0,HX,HM,hi,device); hp1=pred_hazard(h1,HX,HM,hi,device)
        for j,k in enumerate(ai):
            c=ctx.iloc[int(k)]; base={
              'split':split_name,'market_id':int(mids[k]),'t':int(times[k]),'phase':PHASE[int(ph[k])],
              'seconds_left':float(c.seconds_left),'actual_action':str(actions[k]),'actual_role':str(c.role),'actual_purpose':str(c.purpose),
              'floor':float(c.floor),'coverage':float(c.coverage),'abs_gap':float(c.abs_gap),'risk_deficit':float(c.risk_deficit),
              'weak_active_roots':float(c.weak_active_roots),'dominant_active_roots':float(c.dominant_active_roots),
              'weak_unresolved_shares':float(c.weak_unresolved_shares),'dominant_unresolved_shares':float(c.dominant_unresolved_shares),
              'ownership_context':ownership_context(c),'floor_state':floor_state(c)
            }
            for head,y,bp,ap,label0,label1 in [
                ('PURPOSE',purpose[k],pp0[j],pp1[j],'REPAIR','ADD'),('ROLE',role[k],rp0[j],rp1[j],'MAKER','TAKER')]:
                tb=target_prob(y,bp); ta=target_prob(y,ap)
                led.append({**base,'head':head,'target_label':label1 if int(y) else label0,'before_p1':float(bp),'after_p1':float(ap),
                            'target_prob_before':tb,'target_prob_after':ta,'target_prob_delta':ta-tb,'threshold_transition':transition(y,bp,ap)})
        labels=['ANY_ACTION_1S','TAKER_ACTION_1S','ADD_ACTION_1S']
        for j,k in enumerate(hi):
            phase=PHASE[int(HPH[k])]; sec={0:240.0,1:120.0,2:30.0}[int(HPH[k])]
            for q,name in enumerate(labels):
                y=int(HY[k,q]); bp=float(hp0[j,q]); ap=float(hp1[j,q]);tb=target_prob(y,bp);ta=target_prob(y,ap)
                led.append({'split':split_name,'market_id':int(HMIDS[k]),'t':int(HTIMES[k]),'phase':phase,'seconds_left':sec,
                            'actual_action':'','actual_role':'','actual_purpose':'','floor':np.nan,'coverage':np.nan,'abs_gap':np.nan,'risk_deficit':np.nan,
                            'weak_active_roots':np.nan,'dominant_active_roots':np.nan,'weak_unresolved_shares':np.nan,'dominant_unresolved_shares':np.nan,
                            'ownership_context':'GRID','floor_state':'GRID','head':name,'target_label':'YES' if y else 'NO','before_p1':bp,'after_p1':ap,
                            'target_prob_before':tb,'target_prob_after':ta,'target_prob_delta':ta-tb,'threshold_transition':transition(y,bp,ap)})
    L=pd.DataFrame(led)
    L.to_csv(out/'behavior_ledger.csv',index=False)

    def agg(cols):
        z=L.groupby(cols,dropna=False).agg(n=('target_prob_delta','size'),meanTargetProbDelta=('target_prob_delta','mean'),
            medianTargetProbDelta=('target_prob_delta','median'),wrongToRight=('threshold_transition',lambda x:int((x=='WRONG_TO_RIGHT').sum())),
            rightToWrong=('threshold_transition',lambda x:int((x=='RIGHT_TO_WRONG').sum())),
            improvedProbability=('target_prob_delta',lambda x:int((x>1e-8).sum())),worsenedProbability=('target_prob_delta',lambda x:int((x<-1e-8).sum()))).reset_index()
        z['netThresholdCorrections']=z.wrongToRight-z.rightToWrong
        return z.sort_values(['meanTargetProbDelta','netThresholdCorrections'],ascending=False)
    a1=agg(['split','head','phase','target_label']); a1.to_csv(out/'behavior_phase_label_summary.csv',index=False)
    action=L[L['head'].isin(['PURPOSE','ROLE'])].copy()
    a2=action.groupby(['split','head','phase','target_label','floor_state','ownership_context'],dropna=False).agg(
        n=('target_prob_delta','size'),meanTargetProbDelta=('target_prob_delta','mean'),wrongToRight=('threshold_transition',lambda x:int((x=='WRONG_TO_RIGHT').sum())),
        rightToWrong=('threshold_transition',lambda x:int((x=='RIGHT_TO_WRONG').sum()))).reset_index()
    a2['netThresholdCorrections']=a2.wrongToRight-a2.rightToWrong
    a2.to_csv(out/'behavior_context_summary.csv',index=False)

    eligible=a2[a2.n>=10].sort_values(['meanTargetProbDelta','netThresholdCorrections'],ascending=False)
    top_good=eligible.head(20).to_dict('records'); top_bad=eligible.sort_values(['meanTargetProbDelta','netThresholdCorrections']).head(20).to_dict('records')
    v4=json.loads(Path(args.v4_result).read_text(encoding='utf-8'))
    accepted={k:[int(x['marketId']) for x in v4.get('learningCurve',[]) if x.get('acceptedHeads',{}).get(k)] for k in ['purpose','role','hazard']}
    summary={'version':'R4_TARGET_EPISODIC_BEHAVIOR_LEDGER_V1','researchOnly':True,'actionAuthority':False,
      'comparison':{'startCheckpoint':str(args.start_checkpoint),'finalCheckpoint':str(args.final_checkpoint),'splits':['GUARD20','FINAL20']},
      'acceptedEpisodeMarketsByHead':accepted,'rows':int(len(L)),
      'phaseLabelSummary':a1.to_dict('records'),'topImprovingActionContextsMinN10':top_good,'topWorseningActionContextsMinN10':top_bad,
      'interpretationGuard':'Behavior movement is descriptive start-vs-final model change. It does not prove any single accepted episode caused a downstream change; use recurring clusters across future fresh streams as causal candidates.',
      'guards':['no PnL/winner/settlement input','0.5 threshold used only to describe flips, never tuned','probability-to-Target movement also recorded to avoid threshold-only conclusions','no live mutation']}
    (out/'synthesis.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    if args.cache_out:
        torch.save({'version':'R4_TARGET_EPISODIC_FRESH120_TENSOR_CACHE_V1','X':torch.from_numpy(X),'M':torch.from_numpy(M),'purpose':torch.from_numpy(purpose),'role':torch.from_numpy(role),'phase':torch.from_numpy(ph),'mids':torch.from_numpy(mids),'times':torch.from_numpy(times),'HX':torch.from_numpy(HX),'HM':torch.from_numpy(HM),'HY':torch.from_numpy(HY),'HPH':torch.from_numpy(HPH),'HMIDS':torch.from_numpy(HMIDS),'HTIMES':torch.from_numpy(HTIMES)},Path(args.cache_out))
    print(json.dumps({'rows':len(L),'acceptedEpisodeMarketsByHead':accepted,'topGood':top_good[:8],'topBad':top_bad[:8],'out':str(out)},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
