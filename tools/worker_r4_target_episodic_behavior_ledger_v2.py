from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,pandas as pd,torch

PHASE={0:'FORMATION_180_300',1:'MANAGEMENT_60_180',2:'PROTECTION_0_60'}
def ownctx(r):
 w=float(r.get('weak_active_roots',0) or 0);d=float(r.get('dominant_active_roots',0) or 0)
 return 'BOTH_ACTIVE' if w>0 and d>0 else 'WEAK_ACTIVE' if w>0 else 'DOMINANT_ACTIVE' if d>0 else 'NO_ACTIVE_ROOT'
def fstate(r):
 f=float(r.get('floor',0) or 0);return 'POSITIVE_FLOOR' if f>1e-9 else 'NEGATIVE_FLOOR' if f<-1e-9 else 'ZERO_FLOOR'
def tr(y,b,a):
 cb=int(b>=.5)==int(y);ca=int(a>=.5)==int(y)
 return 'WRONG_TO_RIGHT' if (not cb and ca) else 'RIGHT_TO_WRONG' if (cb and not ca) else 'STAY_RIGHT' if cb else 'STAY_WRONG'
def tp(y,p):return float(p if int(y) else 1-p)
@torch.no_grad()
def pb(m,X,M,idx,d,b=2048):
 m.eval();o=[]
 for s in range(0,len(idx),b):
  ii=idx[s:s+b];o.append(torch.softmax(m(torch.from_numpy(X[ii]).to(d),torch.from_numpy(M[ii]).to(d)),1)[:,1].cpu().numpy())
 return np.concatenate(o) if o else np.zeros(0,np.float32)
@torch.no_grad()
def ph(m,X,M,idx,d,b=2048):
 m.eval();o=[]
 for s in range(0,len(idx),b):
  ii=idx[s:s+b];o.append(torch.sigmoid(m(torch.from_numpy(X[ii]).to(d),torch.from_numpy(M[ii]).to(d))).cpu().numpy())
 return np.concatenate(o) if o else np.zeros((0,3),np.float32)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--fresh-dir',required=True);ap.add_argument('--fresh-cache',required=True);ap.add_argument('--start-checkpoint',required=True);ap.add_argument('--final-checkpoint',required=True);ap.add_argument('--result',required=True);ap.add_argument('--out-dir',required=True);args=ap.parse_args()
 b=Path(args.bundle_dir).resolve();f=Path(args.fresh_dir).resolve();sys.path.insert(0,str(b))
 import train_r4_target_sequence_teacher_v1 as v1
 import train_r4_target_sequence_teacher_v11_factorized as v11
 import train_r4_target_sequence_hazard_v1 as hz
 dev=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu');v1.D=f;hz.D=f
 c=torch.load(Path(args.fresh_cache),map_location='cpu',weights_only=False);n=lambda x:x.numpy() if torch.is_tensor(x) else np.asarray(x)
 X,M,purpose,role,phase,mids,times=n(c['X']),n(c['M']),n(c['purpose']),n(c['role']),n(c['phase']),n(c['mids']),n(c['times']);HX,HM,HY,HPH,HMIDS,HTIMES=n(c['HX']),n(c['HM']),n(c['HY']),n(c['HPH']),n(c['HMIDS']),n(c['HTIMES'])
 d=v1.build_rows().reset_index(drop=True);ctx=d[d.purpose.astype(str)!='FLAT'].reset_index(drop=True);assert len(ctx)==len(X);assert np.array_equal(ctx.market_id.astype(int).to_numpy(),mids);assert np.array_equal(ctx.t.astype(np.int64).to_numpy(),times)
 split=json.loads((f/'split.json').read_text());spl={'GUARD20':set(map(int,split['guard20'])),'FINAL20':set(map(int,split['final20']))};idx=lambda arr,ids:np.where(np.array([int(x) in ids for x in arr],bool))[0].astype(np.int64)
 c0=torch.load(Path(args.start_checkpoint),map_location='cpu',weights_only=False);c1=torch.load(Path(args.final_checkpoint),map_location='cpu',weights_only=False)
 def mods(ck):
  p=v11.SeqBinary(X.shape[-1]);p.load_state_dict(ck['states']['purposeBinary']);r=v11.SeqBinary(X.shape[-1]);r.load_state_dict(ck['states']['roleBinary']);h=hz.HazardSeq(HX.shape[-1]);h.load_state_dict(ck['states']['hazard']);return p.to(dev),r.to(dev),h.to(dev)
 p0,r0,h0=mods(c0);p1,r1,h1=mods(c1);rows=[]
 for sn,ids in spl.items():
  ai=idx(mids,ids);hi=idx(HMIDS,ids);pp0,pp1=pb(p0,X,M,ai,dev),pb(p1,X,M,ai,dev);rp0,rp1=pb(r0,X,M,ai,dev),pb(r1,X,M,ai,dev);hp0,hp1=ph(h0,HX,HM,hi,dev),ph(h1,HX,HM,hi,dev)
  for j,k in enumerate(ai):
   q=ctx.iloc[int(k)];base={'split':sn,'market_id':int(mids[k]),'t':int(times[k]),'phase':PHASE[int(phase[k])],'seconds_left':float(q.seconds_left),'actual_action':('TAKER' if role[k] else 'MAKER')+'_'+('ADD' if purpose[k] else 'REPAIR'),'actual_role':'TAKER' if role[k] else 'MAKER','actual_purpose':'ADD' if purpose[k] else 'REPAIR','floor':float(q.floor),'coverage':float(q.coverage),'abs_gap':float(q.abs_gap),'risk_deficit':float(q.risk_deficit),'weak_active_roots':float(q.weak_active_roots),'dominant_active_roots':float(q.dominant_active_roots),'weak_unresolved_shares':float(q.weak_unresolved_shares),'dominant_unresolved_shares':float(q.dominant_unresolved_shares),'ownership_context':ownctx(q),'floor_state':fstate(q)}
   for head,y,x0,x1,l0,l1 in [('PURPOSE',purpose[k],pp0[j],pp1[j],'REPAIR','ADD'),('ROLE',role[k],rp0[j],rp1[j],'MAKER','TAKER')]:
    a,b=tp(y,x0),tp(y,x1);rows.append({**base,'head':head,'target_label':l1 if int(y) else l0,'before_p1':float(x0),'after_p1':float(x1),'target_prob_before':a,'target_prob_after':b,'target_prob_delta':b-a,'threshold_transition':tr(y,x0,x1)})
  for j,k in enumerate(hi):
   for z,name in enumerate(['ANY_ACTION_1S','TAKER_ACTION_1S','ADD_ACTION_1S']):
    y=int(HY[k,z]);x0=float(hp0[j,z]);x1=float(hp1[j,z]);a,b=tp(y,x0),tp(y,x1);rows.append({'split':sn,'market_id':int(HMIDS[k]),'t':int(HTIMES[k]),'phase':PHASE[int(HPH[k])],'seconds_left':float({0:240,1:120,2:30}[int(HPH[k])]),'actual_action':'','actual_role':'','actual_purpose':'','floor':np.nan,'coverage':np.nan,'abs_gap':np.nan,'risk_deficit':np.nan,'weak_active_roots':np.nan,'dominant_active_roots':np.nan,'weak_unresolved_shares':np.nan,'dominant_unresolved_shares':np.nan,'ownership_context':'GRID','floor_state':'GRID','head':name,'target_label':'YES' if y else 'NO','before_p1':x0,'after_p1':x1,'target_prob_before':a,'target_prob_after':b,'target_prob_delta':b-a,'threshold_transition':tr(y,x0,x1)})
 L=pd.DataFrame(rows);out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True);L.to_csv(out/'behavior_ledger.csv',index=False)
 def agg(cols,df=L):
  z=df.groupby(cols,dropna=False).agg(n=('target_prob_delta','size'),meanTargetProbDelta=('target_prob_delta','mean'),medianTargetProbDelta=('target_prob_delta','median'),wrongToRight=('threshold_transition',lambda x:int((x=='WRONG_TO_RIGHT').sum())),rightToWrong=('threshold_transition',lambda x:int((x=='RIGHT_TO_WRONG').sum())),improvedProbability=('target_prob_delta',lambda x:int((x>1e-8).sum())),worsenedProbability=('target_prob_delta',lambda x:int((x<-1e-8).sum()))).reset_index();z['netThresholdCorrections']=z.wrongToRight-z.rightToWrong;return z
 a1=agg(['split','head','phase','target_label']);a1.to_csv(out/'behavior_phase_label_summary.csv',index=False);A=L[L['head'].isin(['PURPOSE','ROLE'])];a2=agg(['split','head','phase','target_label','floor_state','ownership_context'],A);a2.to_csv(out/'behavior_context_summary.csv',index=False)
 res=json.loads(Path(args.result).read_text());curve=res.get('curve',res.get('learningCurve',[]));accepted={h:[int(x['marketId']) for x in curve if x.get('acceptedHeads',{}).get(h)] for h in ['purpose','role','hazard']}
 good=a2[a2.n>=10].sort_values(['meanTargetProbDelta','netThresholdCorrections'],ascending=False).head(30);bad=a2[a2.n>=10].sort_values(['meanTargetProbDelta','netThresholdCorrections']).head(30)
 syn={'version':'R4_TARGET_EPISODIC_BEHAVIOR_LEDGER_V2','researchOnly':True,'actionAuthority':False,'rows':int(len(L)),'acceptedEpisodeMarketsByHead':accepted,'phaseLabelSummary':a1.sort_values('meanTargetProbDelta',ascending=False).to_dict('records'),'topImprovingActionContextsMinN10':good.to_dict('records'),'topWorseningActionContextsMinN10':bad.to_dict('records'),'guards':['descriptive only','fixed 0.5','no outcome input','no live mutation']};(out/'synthesis.json').write_text(json.dumps(syn,indent=2),encoding='utf-8');print(json.dumps({'rows':len(L),'accepted':accepted,'topGood':good.head(8).to_dict('records'),'topBad':bad.head(8).to_dict('records'),'out':str(out)},indent=2),flush=True)
if __name__=='__main__':main()