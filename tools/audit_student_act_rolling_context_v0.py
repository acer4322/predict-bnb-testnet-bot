from __future__ import annotations
import json,sqlite3,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_student_state_supervisor_v0 as ss
OUT=ROOT/'data'/'research'/'supervisor_curriculum_v0';REPORT=OUT/'student_act_rolling_context_v0_report.json';CSV=OUT/'student_act_rolling_context_v0_markets.csv'
BOOK=ROOT/'data'/'wallet_maker_book_inference.db';TARGET=ROOT/'data'/'target_wallet_official_v1.db'
BLOCKS=[('W20_29',20,30),('W30_39',30,40),('FAIL40_49',40,50),('W50_59',50,60),('W60_69',60,70)]

def q(s,p):
 z=pd.to_numeric(s,errors='coerce').dropna();return float(z.quantile(p)) if len(z) else None

def switches(vals):
 xs=list(vals);return sum(xs[i]!=xs[i-1] for i in range(1,len(xs)))

def main():
 OUT.mkdir(parents=True,exist_ok=True);d,mem,files=ss.build();d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);ms=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']).reset_index(drop=True);idx={int(r.market_id):i for i,r in ms.iterrows()}
 b=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True);t=sqlite3.connect(f'file:{TARGET.resolve().as_posix()}?mode=ro',uri=True);rows=[]
 try:
  for mid,g in d.groupby('market_id',sort=False):
   mid=int(mid);g=g.sort_values('checkpoint_ms');we=int(g.market_end_ms.iloc[0]);modes=g.teacher_mode.astype(str);act=modes.ne('HOLD');maker=modes.eq('MAKER');taker=modes.eq('TAKER');
   mp=int(b.execute("select count(*) from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75",(mid,)).fetchone()[0]);tp=int(t.execute("select count(*) from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null",(mid,)).fetchone()[0]);rp=int(t.execute("select count(*) from target_parent_orders where market_id=? and asset='BTC' and quote_type='BID' and first_event_ms is not null",(mid,)).fetchone()[0])
   # concentration: fraction of ACT checkpoints sitting in contiguous runs >=2 at 2s cadence
   aa=act.astype(int).to_numpy();runlens=[];cur=0
   for v in aa:
    if v:cur+=1
    elif cur:runlens.append(cur);cur=0
   if cur:runlens.append(cur)
   clustered=sum(x for x in runlens if x>=2)/(sum(runlens) or 1)
   rows.append({'ourIndex':idx[mid],'marketId':mid,'marketEndMs':we,'rows':len(g),'actRate':float(act.mean()),'makerRate':float(maker.mean()),'takerRate':float(taker.mean()),'takerShareOfAct':float(taker.sum()/max(1,act.sum())),'modeSwitches':switches(modes),'modeSwitchRate':switches(modes)/max(1,len(g)-1),'actRuns':len(runlens),'actClusteredFrac':float(clustered),'targetMakerParents':mp,'targetTakerParents':tp,'targetBidParents':rp,'parentTakerShare':tp/max(1,mp+tp),'ourMakerAbsNetMean':float(pd.to_numeric(g.maker_abs_net,errors='coerce').mean()),'ourMakerAbsNetP90':q(g.maker_abs_net,.9),'ourMakerCoverageMean':float(pd.to_numeric(g.maker_paired_coverage,errors='coerce').mean()),'ourCombinedAbsNetMean':float(pd.to_numeric(g.combined_abs_net,errors='coerce').mean()),'ourWorstCaseFloorMean':float(pd.to_numeric(g.worst_case_floor,errors='coerce').mean()),'ourMakerAbsVelocityP90':q(g.maker_absnet_change_10s,.9),'ourPlacement10Mean':float(pd.to_numeric(g.placements_10s,errors='coerce').mean()),'ourTakerFills10Mean':float(pd.to_numeric(g.taker_fills_10s,errors='coerce').mean()),'episodeRate':float(pd.to_numeric(g.episodeActive,errors='coerce').fillna(0).mean()),'readinessRate':float(pd.to_numeric(g.readiness,errors='coerce').fillna(0).mean())})
 finally:b.close();t.close()
 mdf=pd.DataFrame(rows).sort_values('ourIndex');mdf.to_csv(CSV,index=False);summary={}
 for name,lo,hi in BLOCKS:
  x=mdf[(mdf.ourIndex>=lo)&(mdf.ourIndex<hi)];summary[name]={'markets':len(x)}
  for c in ['actRate','makerRate','takerRate','takerShareOfAct','modeSwitches','modeSwitchRate','actRuns','actClusteredFrac','targetMakerParents','targetTakerParents','parentTakerShare','ourMakerAbsNetMean','ourMakerAbsNetP90','ourMakerCoverageMean','ourCombinedAbsNetMean','ourWorstCaseFloorMean','ourMakerAbsVelocityP90','ourPlacement10Mean','ourTakerFills10Mean','episodeRate','readinessRate']:
   z=pd.to_numeric(x[c],errors='coerce');summary[name][c]={'mean':float(z.mean()),'median':float(z.median())}
  summary[name]['marketIds']=x.marketId.astype(int).tolist()
 rep={'reportVersion':'STUDENT_ACT_ROLLING_CONTEXT_V0','researchOnly':True,'question':'What structural context distinguishes the rolling block where soft ACT distillation fails?','source':{'markets':int(mdf.marketId.nunique()),'rows':int(d.shape[0]),'teacherMapping':'current book metadata + raw Target parent orders via fixed student builder'},'blocks':summary,'interpretationRule':'Use descriptive differences to propose a context/lesson gate; do not tune a threshold from these same rolling test labels. A proposed context must then be tested on independent/future ordinary data.','files':{'marketAudit':str(CSV)},'guards':['No winner/PnL.','No 2026-08-16.','No final75-99.','No runtime changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
