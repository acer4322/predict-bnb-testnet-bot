from __future__ import annotations
import json,glob,math,sys
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.evaluate_r4_repair_forward_readiness_v1 import readiness
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
F=['riskAgeS','secondsLeft','makerAbsNet','makerCoverage','preFloor','preUpside','directionScore','netDir','pRepairMaker','pDomMaker','pResidualWake','activeOrderCount','repairActive','dominantActive','repairRemUnits','dominantRemUnits','repairAgeS','dominantAgeS','repairQuote','dominantQuote','repairFill5','dominantFill5','repairCont','dominantCont']

def feat(cand,path):
 p=cand.get('portfolio') or {};m=cand.get('models') or {};u=cand.get('public') or {};s=(path or {}).get('summary') or {};rd=readiness(path or {},p);net=float(p.get('maker_net') or 0);repm=float(m.get('pMakerDown') if net>0 else m.get('pMakerUp') if net<0 else 0);domm=float(m.get('pMakerUp') if net>0 else m.get('pMakerDown') if net<0 else 0)
 return [float(cand.get('riskAgeMs') or 0)/1000,float(u.get('secondsLeft') or 0),float(p.get('maker_abs_net') or 0),float(p.get('maker_paired_coverage') or 0),float(p.get('worst_case_floor') or 0),float(p.get('best_case_pnl') or 0),float(u.get('directionScore') or 0),net*float(u.get('directionScore') or 0),repm,domm,float(m.get('pResidualWake') or 0),float(s.get('activeOrderCount') or 0),float(s.get('repairSideActiveCount') or 0),float(s.get('dominantSideActiveCount') or 0),float(s.get('repairSideRemainingQty') or 0)/18,float(s.get('dominantSideRemainingQty') or 0)/18,float(s.get('repairMeanOrderAgeMs') or 0)/1000,float(s.get('dominantMeanOrderAgeMs') or 0)/1000,float(s.get('repairBestQuoteOffsetTicks')) if s.get('repairBestQuoteOffsetTicks') is not None else math.nan,float(s.get('dominantBestQuoteOffsetTicks')) if s.get('dominantBestQuoteOffsetTicks') is not None else math.nan,rd['repairAnyFill5'],rd['dominantAnyFill5'],rd['repairMaxContinue'],rd['dominantMaxContinue']]
def load_dev():
 conv={};teach={}
 for pat in ['data/research/lan_worker_returns/r4-winrate-conversion-recent8-v2/conversion.json','data/research/lan_worker_returns/r4-winrate-recent16-*/conversion.json','data/research/lan_worker_returns/r4-winrate-recent16b-*/conversion.json']:
  for fp in glob.glob(str(ROOT/pat)):
   for r in json.load(open(fp,encoding='utf-8'))['rows']:conv[int(r['marketId'])]=r
 for pat in ['data/research/lan_worker_returns/r4-winrate-recent8-causal-v1/teacher.json','data/research/lan_worker_returns/r4-winrate-path16a-*/teacher.json','data/research/lan_worker_returns/r4-winrate-path16b-*/teacher.json']:
  for fp in glob.glob(str(ROOT/pat)):
   for r in json.load(open(fp,encoding='utf-8'))['rows']:teach[int(r['marketId'])]=r
 out=[]
 for mid,c in conv.items():
  t=teach.get(mid);forced=(((t or {}).get('counterfactual') or {}).get('forced') or [])
  if not t or not forced or c.get('conversion','').endswith('NO_CANDIDATE'):continue
  out.append((mid,c['conversion'],feat(t.get('candidate') or {},forced[0].get('activeOrderPathState') or {})))
 return out
def main():
 dev=load_dev();X=np.asarray([x[2] for x in dev],float);y=np.asarray([1 if x[1]=='LOSS->WIN' else 0 for x in dev],int);med=np.nanmedian(X,axis=0);med=np.where(np.isfinite(med),med,0.0);X=np.where(np.isfinite(X),X,med);model=make_pipeline(StandardScaler(),LogisticRegression(C=.25,class_weight='balanced',max_iter=3000,random_state=20260830));model.fit(X,y)
 ch=[]
 for fp in glob.glob(str(ROOT/'data/research/lan_worker_returns/r4-winrate-ch16c-*/result.json')):
  for r in json.load(open(fp,encoding='utf-8'))['rows']:ch.append(r)
 rows=[];basewins=policywins=forcedwins=0;sel_lw=sel_wl=0
 for r in ch:
  b=bool((r.get('baseline') or {}).get('positive'));cf=bool((r.get('counterfactual') or {}).get('positive')) if r.get('counterfactual') else b;basewins+=b;forcedwins+=cf
  if r.get('exactBranchApplied') and isinstance(r.get('pathState'),dict):
   x=np.asarray(feat(r.get('candidate') or {},r['pathState']),float);x=np.where(np.isfinite(x),x,med);p=float(model.predict_proba(x.reshape(1,-1))[0,1]);selected=p>=.70
  else:p=None;selected=False
  chosen=cf if selected else b;policywins+=chosen
  if selected and r.get('conversion')=='LOSS->WIN':sel_lw+=1
  if selected and r.get('conversion')=='WIN->LOSS':sel_wl+=1
  rows.append({'marketId':r['marketId'],'conversion':r.get('conversion'),'pLossToWin':p,'selectedRepair':selected,'baselineWin':b,'counterfactualWin':cf,'policyWin':chosen,'deltaPnl':r.get('deltaPnl')})
 n=len(ch);rep={'version':'R4_WIN_CONVERSION_SELECTOR_CHALLENGE16C_SCORE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'threshold':0.70,'developmentMarkets':len(dev),'developmentLossToWin':int(y.sum()),'challengeMarkets':n,'baselineWins':basewins,'baselineWinRate':basewins/n if n else None,'forcedRepairWins':forcedwins,'forcedRepairWinRate':forcedwins/n if n else None,'selectorPolicyWins':policywins,'selectorPolicyWinRate':policywins/n if n else None,'selectedLossToWin':sel_lw,'selectedWinToLoss':sel_wl,'netSelectedWinDelta':sel_lw-sel_wl,'rows':rows};(P/'r4_adaptive_win_conversion_selector_challenge16c_score_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
