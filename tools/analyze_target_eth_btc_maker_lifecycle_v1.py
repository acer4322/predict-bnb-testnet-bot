import argparse,json,math,os,sqlite3,statistics
from collections import Counter

def q(v,p):
 if not v:return None
 s=sorted(v); x=(len(s)-1)*p; i=int(math.floor(x)); j=int(math.ceil(x)); return s[i] if i==j else s[i]*(j-x)+s[j]*(x-i)

def summ(rows):
 out={'n':len(rows)}
 for name in ['expected_parent_shares','target_filled_shares','fill_allocation_coverage','placement_coverage','resting_ms','post_action_delay_ms','confidence']:
  v=[float(r[name]) for r in rows if r[name] is not None]
  out[name]={'p10':q(v,.1),'p25':q(v,.25),'median':q(v,.5),'p75':q(v,.75),'p90':q(v,.9),'mean':sum(v)/len(v) if v else None}
 out['multiFillRate']=sum(int(r['multi_fill_parent'] or 0)!=0 for r in rows)/len(rows) if rows else None
 out['placementCoverage80Rate']=sum((r['placement_coverage'] or 0)>=.8 for r in rows)/len(rows) if rows else None
 out['fillCoverage80Rate']=sum((r['fill_allocation_coverage'] or 0)>=.8 for r in rows)/len(rows) if rows else None
 out['postActionTop']=Counter((r['post_action'] or 'NULL') for r in rows).most_common(12)
 out['expectedSizeTop']=Counter(round(float(r['expected_parent_shares']),3) for r in rows if r['expected_parent_shares'] is not None).most_common(12)
 out['filledSizeTop']=Counter(round(float(r['target_filled_shares']),3) for r in rows if r['target_filled_shares'] is not None).most_common(12)
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--db',required=True); ap.add_argument('--output',required=True); a=ap.parse_args(); c=sqlite3.connect(a.db); c.row_factory=sqlite3.Row
 bm={r['market_id']:r['window_end_ms'] for r in c.execute('select * from btc_markets')}; em={r['market_id']:r['window_end_ms'] for r in c.execute('select * from eth_markets')}
 br=list(c.execute('select * from btc_parents')); er=list(c.execute('select * from eth_parents'))
 b_ends={bm.get(r['market_id']) for r in br if bm.get(r['market_id']) is not None}; e_ends={em.get(r['market_id']) for r in er if em.get(r['market_id']) is not None}; common=b_ends&e_ends
 bc=[r for r in br if bm.get(r['market_id']) in common]; ec=[r for r in er if em.get(r['market_id']) in common]
 out={'version':'TARGET_ETH_BTC_MAKER_LIFECYCLE_V1','sourceDb':os.path.abspath(a.db),'all':{'BTC':summ(br),'ETH':summ(er)},'commonChronology':{'commonWindows':len(common),'minEndMs':min(common) if common else None,'maxEndMs':max(common) if common else None,'BTC':summ(bc),'ETH':summ(ec)}}
 # matched-window aggregate intensity correlations
 def agg(rows,mm):
  d={}
  for r in rows:
   e=mm.get(r['market_id']);
   if e not in common:continue
   x=d.setdefault(e,{'n':0,'expected':0.0,'filled':0.0,'rest':[],'multi':0})
   x['n']+=1; x['expected']+=float(r['expected_parent_shares'] or 0); x['filled']+=float(r['target_filled_shares'] or 0); x['multi']+=int(r['multi_fill_parent'] or 0)!=0
   if r['resting_ms'] is not None:x['rest'].append(float(r['resting_ms']))
  return d
 B=agg(br,bm); E=agg(er,em)
 def corr(x,y):
  if len(x)<3:return None
  mx=sum(x)/len(x); my=sum(y)/len(y); vx=sum((z-mx)**2 for z in x); vy=sum((z-my)**2 for z in y)
  return sum((a-mx)*(b-my) for a,b in zip(x,y))/math.sqrt(vx*vy) if vx>0 and vy>0 else None
 cc={}
 for key in ['n','expected','filled']:
  x=[B[e][key] for e in sorted(common)]; y=[E[e][key] for e in sorted(common)]; cc[key+'Corr']=corr(x,y)
 out['commonWindowCoupling']=cc
 os.makedirs(os.path.dirname(a.output),exist_ok=True); json.dump(out,open(a.output,'w',encoding='utf-8'),ensure_ascii=False,indent=2); print(json.dumps({'ok':True,'commonWindows':len(common),'coupling':cc,'btcPost':out['commonChronology']['BTC']['postActionTop'],'ethPost':out['commonChronology']['ETH']['postActionTop']},ensure_ascii=False))
if __name__=='__main__':main()
