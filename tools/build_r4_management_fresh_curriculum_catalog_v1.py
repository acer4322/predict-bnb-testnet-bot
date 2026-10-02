from __future__ import annotations
import bisect,json,sqlite3,sys
from collections import Counter
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import build_supervisor_curriculum_catalog_v0 as base
P=ROOT/'data/research/r4_v0/p0_provenance_v1';ROWS=P/'r4_management_fresh_adaptation_v1_rows.csv';BOOK=ROOT/'data/wallet_maker_book_inference.db';TARGET=ROOT/'data/target_wallet_official_v1.db';EPS=1.0;SKILLS=base.SKILLS

def effect(pre,side,shares,maker=False): return base.effect(pre,side,shares,maker)
def main():
 d=pd.read_csv(ROWS).sort_values(['market_end_ms','market_id','checkpoint_ms']);bc=sqlite3.connect(BOOK);tc=sqlite3.connect(TARGET);out=[]
 for mid,x in d.groupby('market_id',sort=False):
  mid=int(mid);seq=[];times=x.checkpoint_ms.astype('int64').tolist();mn=pd.to_numeric(x.maker_net,errors='coerce').fillna(0).tolist();cn=pd.to_numeric(x.combined_net,errors='coerce').fillna(0).tolist()
  ps=[(int(t),str(s)) for t,s in bc.execute("select placement_first_ms,target_side from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by placement_first_ms",(mid,))]
  for t,side in ps:
   j=bisect.bisect_left(times,t)-1;pre=float(mn[j]) if j>=0 else 0.;seq.append((t,effect(pre,side,18.,True),'MAKER'))
  ts=[(int(t),str(s),float(sh or 0)) for t,s,sh in tc.execute("select first_event_ms,side,shares from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by first_event_ms",(mid,))]
  for t,side,sh in ts:
   j=bisect.bisect_left(times,t)-1;pre=float(cn[j]) if j>=0 else 0.;seq.append((t,effect(pre,side,sh,False),'TAKER'))
  seq.sort();cnt=Counter(z[1] for z in seq);total=sum(cnt.values());maker=sum(v for k,v in cnt.items() if k.startswith('MAKER_'));taker=total-maker;ch=sum(a[2]!=b[2] for a,b in zip(seq,seq[1:]));sw=sum(a[1]!=b[1] for a,b in zip(seq,seq[1:]));pur=max(cnt.values())/total if total else 0;ent=base.norm_entropy(cnt);mr=cnt['MAKER_REPAIR'];tr=cnt['TAKER_REPAIR'];mnrep=cnt['MAKER_BUILD']+cnt['MAKER_ADD'];tnrep=cnt['TAKER_BUILD']+cnt['TAKER_ADD'];tags=[]
  if total>=3:tags.append('FOUNDATION_ACT_HOLD')
  if maker>=6 and taker<=1 and mnrep/max(maker,1)>=.70:tags.append('PURE_MAKER_BUILD_ADD')
  if mr>=4 and mr/max(maker,1)>=.30:tags.append('PASSIVE_MAKER_REPAIR')
  if tr>=2 and tr/max(taker,1)>=.45:tags.append('ACTIVE_TAKER_REPAIR')
  if tnrep>=2 and tnrep/max(taker,1)>=.45:tags.append('TAKER_BUILD_ADD')
  if maker>=4 and taker>=2 and ch>=2:tags.append('MAKER_TAKER_HANDOFF')
  if ent>=.55 or (maker>=4 and taker>=3 and sw>=5):tags.append('MIXED_COORDINATION')
  if total<3:tags.append('SPARSE_SKIP_OR_HOLD_ONLY')
  out.append({'marketId':mid,'marketEndMs':int(x.market_end_ms.iloc[0]),'split':str(x['split'].iloc[0]),'totalTeacherActions':total,**{k:cnt.get(k,0) for k in SKILLS},'makerActions':maker,'takerActions':taker,'makerRepairRate':mr/max(maker,1),'takerRepairRate':tr/max(taker,1),'channelSwitches':ch,'skillSwitches':sw,'lessonPurity':pur,'skillEntropy':ent,'lessonTags':'|'.join(tags)})
 bc.close();tc.close();m=pd.DataFrame(out)
 def primary(r):
  tags=set(str(r.lessonTags).split('|'))
  if 'SPARSE_SKIP_OR_HOLD_ONLY' in tags:return 'SPARSE_SKIP_OR_HOLD_ONLY'
  if 'PURE_MAKER_BUILD_ADD' in tags and r.lessonPurity>=.55:return 'PURE_MAKER_BUILD_ADD'
  if 'PASSIVE_MAKER_REPAIR' in tags and r.makerRepairRate>=.45:return 'PASSIVE_MAKER_REPAIR'
  if 'ACTIVE_TAKER_REPAIR' in tags and r.takerRepairRate>=.55:return 'ACTIVE_TAKER_REPAIR'
  if 'TAKER_BUILD_ADD' in tags and r.takerRepairRate<.45:return 'TAKER_BUILD_ADD'
  if 'MAKER_TAKER_HANDOFF' in tags:return 'MAKER_TAKER_HANDOFF'
  if 'MIXED_COORDINATION' in tags:return 'MIXED_COORDINATION'
  return 'FOUNDATION_ACT_HOLD'
 m['primaryLesson']=m.apply(primary,axis=1);m.to_csv(P/'r4_management_fresh_curriculum_catalog_v1.csv',index=False);rep={'version':'R4_MANAGEMENT_FRESH_CURRICULUM_CATALOG_V1','markets':len(m),'primaryBySplit':{k:g.primaryLesson.value_counts().to_dict() for k,g in m.groupby('split')},'tagCounts':dict(Counter(t for s in m.lessonTags for t in str(s).split('|') if t))};(P/'r4_management_fresh_curriculum_catalog_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
