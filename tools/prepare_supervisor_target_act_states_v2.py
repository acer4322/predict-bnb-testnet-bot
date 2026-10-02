from __future__ import annotations
import bisect,json,sqlite3,sys
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_supervisor_mode_v0 as b
SRC=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
OLD=SRC/'target_general_maker_side_hazard_v1.csv'
INC=OUT/'target_general_state_increment_v2.csv'
CSV=OUT/'supervisor_target_act_states_v2.csv'
REPORT=OUT/'supervisor_target_act_states_v2_report.json'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'

def sample2(d):
    d=d.sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    return d[d.groupby('market_id',sort=False).cumcount().mod(2).eq(0)].copy().reset_index(drop=True)

def add_memory(d):
    gb=d.groupby('market_id',sort=False);mem={}
    for base in b.MEM_BASE:
        cur=pd.to_numeric(d[base],errors='coerce')
        for lag in b.LAGS:
            past=pd.to_numeric(gb[base].shift(lag),errors='coerce');mem[f'{base}_delta{lag}s']=cur-past
    m=pd.DataFrame(mem,index=d.index);return pd.concat([d,m],axis=1),list(m.columns)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    old=pd.read_csv(OLD);inc=pd.read_csv(INC)
    raw=pd.concat([old,inc],ignore_index=True).sort_values(['market_end_ms','market_id','checkpoint_ms']).drop_duplicates(['market_id','checkpoint_ms'],keep='last').reset_index(drop=True)
    raw=raw[pd.to_numeric(raw.seconds_left,errors='coerce').between(3,297,inclusive='both')].copy();d=sample2(raw);d,mem=add_memory(d)
    mids=sorted(d.market_id.astype(int).unique().tolist());ph=','.join('?'*len(mids));tak={m:[] for m in mids}
    con=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row;con.execute('pragma query_only=on')
    try:
        q=f"select market_id,first_event_ms from target_parent_orders where asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null and market_id in ({ph}) order by market_id,first_event_ms,parent_id"
        for r in con.execute(q,tuple(mids)):tak[int(r['market_id'])].append(int(r['first_event_ms']))
    finally:con.close()
    mode=[];taker_next3=[]
    up=pd.to_numeric(d.label_up_next1s,errors='coerce').fillna(0).astype(int).gt(0).to_numpy();dn=pd.to_numeric(d.label_down_next1s,errors='coerce').fillna(0).astype(int).gt(0).to_numpy()
    for i,r in enumerate(d[['market_id','checkpoint_ms']].itertuples(index=False)):
        ts=tak.get(int(r.market_id),[]);t=int(r.checkpoint_ms);j=bisect.bisect_right(ts,t);tk=bool(j<len(ts) and 0<ts[j]-t<=3000);mk=bool(up[i] or dn[i]);taker_next3.append(int(tk));mode.append('TAKER' if tk else 'MAKER' if mk else 'HOLD')
    d['taker_next3s_raw']=taker_next3;d['option_mode_v2']=mode;d['gate_act']=np.where(d.option_mode_v2.eq('HOLD'),'HOLD','ACT')
    cols=['market_id','market_end_ms','checkpoint_ms','gate_act','option_mode_v2','taker_next3s_raw']+b.CURRENT+mem;d[cols].to_csv(CSV,index=False)
    oldv1=pd.read_csv(OUT/'supervisor_target_act_states_v1.csv',usecols=['market_id','checkpoint_ms','gate_act']).rename(columns={'gate_act':'gate_act_v1'});cmp=d.merge(oldv1,on=['market_id','checkpoint_ms'],how='inner');changed=int((cmp.gate_act!=cmp.gate_act_v1).sum());h2a=int(((cmp.gate_act_v1=='HOLD')&(cmp.gate_act=='ACT')).sum());a2h=int(((cmp.gate_act_v1=='ACT')&(cmp.gate_act=='HOLD')).sum())
    rep={'reportVersion':'SUPERVISOR_TARGET_ACT_STATES_V2','researchOnly':True,'source':{'oldMarkets':int(old.market_id.nunique()),'incrementMarkets':int(inc.market_id.nunique()),'unionMarkets':int(d.market_id.nunique()),'rows2s':len(d),'minEndMs':int(d.market_end_ms.min()),'maxEndMs':int(d.market_end_ms.max())},'labels':{'gateActCounts':d.gate_act.value_counts().to_dict(),'modeCounts':d.option_mode_v2.value_counts().to_dict(),'rawTakerNext3Count':int(d.taker_next3s_raw.sum())},'v1OverlapCorrection':{'rowsCompared':len(cmp),'changedGateRows':changed,'holdToAct':h2a,'actToHold':a2h},'features':{'current':len(b.CURRENT),'memory':len(mem),'total':len(b.CURRENT)+len(mem),'memorySemantics':'~6/20/60s strict-past after 2s cadence'},'file':str(CSV),'guards':['Raw Target Taker future action is teacher label only.','No winner/PnL.','Old V1 untouched.','2026-08-16 absent.','No runtime changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
