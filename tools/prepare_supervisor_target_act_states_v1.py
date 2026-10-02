from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parent))
import train_supervisor_mode_v0 as b
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'supervisor_options_v0';CSV=OUT/'supervisor_target_act_states_v1.csv'
d,mem=b.build();d['gate_act']=d.option_mode.where(d.option_mode.eq('HOLD'),'ACT');cols=['market_id','market_end_ms','checkpoint_ms','gate_act']+b.CURRENT+mem;d[cols].to_csv(CSV,index=False);print(json.dumps({'rows':len(d),'markets':int(d.market_id.nunique()),'features':len(b.CURRENT+mem),'file':str(CSV)}))