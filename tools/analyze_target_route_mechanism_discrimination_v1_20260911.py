"""Fixed contrasts to separate absolute price, observed directional support,
relative inventory restoration and visible ordercontinuation. No fitted model.
Cells are descriptive; overlap/serial dependence/private pending remain explicit.
"""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,math,statistics,time
from tools.build_target_route_mechanism_discrimination_v1_20260911 import OUT,SRC,SRC_SHA,TABLE,TABLE_SHA,sha,read,write,EPS

LABELS=['y_hasT','y_first_observed_T','y_hasM','y_both']


def sg(x):return -1 if x<-1e-8 else 1 if x>1e-8 else 0

def pb(x):return 0 if x<.1-EPS else 1 if x<.3-EPS else 2 if x<.5-EPS else 3 if x<.7-EPS else 4 if x<.9-EPS else 5

def ib(x):return 0 if x<.25 else 1 if x<.5 else 2

def fb(x):return 0 if x>=0 else 1 if x>=-.05 else 2 if x>=-.2 else 3

def sb(x):return 0 if x<=.01+EPS else 1 if x<=.03+EPS else 2

def db(x):return 0 if x<=.25 else 1 if x<=1 else 2

def ab(x):return 0 if x<=3 else 1 if x<=15 else 2

def mean(x):return statistics.mean(x) if x else None


def base(r):return r['fresh3'] and r['f_role']=='STRONG' and r['f_cost']>EPS

def has_direction(r):return r['z_public_direction_known'] and r['z_side_spot_return5'] is not None

def condition(r,kind,extended=False,directional=False):
 k=(r['block'],r['f_phase'],ib(r['f_relative_imbalance']),fb(r['f_floor_to_cost']),sb(r['f_spread']))
 if kind=='INVENTORY':k+=(pb(r['f_ask']),)
 else:k+=(db(r['f_gap_to_ask_band01']),)
 if extended:k+=(ab(r['f_seconds_since_same_taker']),db(r['f_gap_to_ask_band01']))
 if directional:k+=(sg(r['z_side_spot_distance']),sg(r['z_side_spot_return5']))
 return k


def contrast(rows,kind='INVENTORY',extended=False,directional=False,ref_role=False,maker_only=False,same_sample_keys=None):
 eligible=[]
 for r in rows:
  if not base(r):continue
  if r['f_gap_to_ask_band01'] is None:continue
  if directional and not has_direction(r):continue
  if kind=='INVENTORY':
   if not r['z_reference_same_dominant'] or r['z_delta_net_side'] is None or abs(r['z_delta_net_side'])<=1e-6:continue
   if ref_role and r['z_ref_pre_role']!='STRONG':continue
   if maker_only and r['z_delta_opp_T']>EPS:continue
   arm=0 if r['z_delta_net_side']<0 else 1
  else:
   arm=0 if r['f_ask']<.3-EPS else 1 if r['f_ask']>=.7-EPS else None
   if arm is None:continue
  if same_sample_keys is not None and (r['market_id'],r['t'],r['side']) not in same_sample_keys:continue
  eligible.append((r,arm))
 sets=[]
 for arm in (0,1):
  rr=[r for r,a in eligible if a==arm];ns=Counter(r['market_id'] for r in rr);cells={}
  for r in rr:
   key=condition(r,kind,extended,directional);w=1/len(ns)/ns[r['market_id']]
   c=cells.setdefault(key,dict(w=0.,markets=set(),n=0,sums=Counter()))
   c['w']+=w;c['markets'].add(r['market_id']);c['n']+=1
   for l in LABELS:c['sums'][l]+=w*float(r[l])
  sets.append(dict(rows=rr,cells=cells))
 a,b=sets;common=[k for k in a['cells'] if k in b['cells'] and len(a['cells'][k]['markets'])>=3 and len(b['cells'][k]['markets'])>=3]
 weights={k:min(a['cells'][k]['w'],b['cells'][k]['w']) for k in common};mass=sum(weights.values())
 rates={}
 for l in LABELS:
  def val(g):return sum(weights[k]*g['cells'][k]['sums'][l]/g['cells'][k]['w'] for k in common)/mass if mass else None
  av,bv=val(a),val(b)
  rates[l]=dict(arm0=av,arm1=bv,arm1Minus0=bv-av if mass else None)
 support={(r['market_id'],r['t'],r['side']) for r,arm in eligible if condition(r,kind,extended,directional) in common}
 result=dict(kind=kind,arms=['BELOW_PREVIOUS_SAME_ACTIVE_NET','ABOVE_PREVIOUS_SAME_ACTIVE_NET'] if kind=='INVENTORY' else ['LOW_ASK','HIGH_ASK'],
 extendedTimeDepth=extended,publicDirectionMatched=directional,referencePreRoleStrong=ref_role,makerOnlyDrift=maker_only,
 eligible=[len(a['rows']),len(b['rows'])],markets=[len({r['market_id'] for r in a['rows']}),len({r['market_id'] for r in b['rows']})],
 commonCells=len(common),commonMass=mass,coverage=[sum(g['cells'][k]['w'] for k in common) for g in [a,b]],rates=rates,
 cells=[dict(key=list(k),w=weights[k],markets0=len(a['cells'][k]['markets']),markets1=len(b['cells'][k]['markets']),n0=a['cells'][k]['n'],n1=b['cells'][k]['n']) for k in common])
 return result,support


def inventory_within_market(rows):
 # Postdesign sensitivity: same market/cell comparisons, instead of relying solely
 # on between-market differences within a nominal sizeblock. No fitted labels.
 cs=defaultdict(lambda:[[],[]])
 for r in rows:
  if not base(r) or not r['z_reference_same_dominant'] or abs(r['z_delta_net_side'])<=1e-6:continue
  if r['f_gap_to_ask_band01'] is None:continue
  k=(r['market_id'],r['f_phase'],pb(r['f_ask']),ib(r['f_relative_imbalance']),fb(r['f_floor_to_cost']),sb(r['f_spread']),ab(r['f_seconds_since_same_taker']),db(r['f_gap_to_ask_band01']))
  cs[k][int(r['z_delta_net_side']>0)].append(r)
 permarket=defaultdict(list)
 for key,(a,b) in cs.items():
  if len(a)<3 or len(b)<3:continue
  permarket[key[0]].append(dict(w=min(len(a),len(b)),a=mean([float(r['y_hasT']) for r in a]),b=mean([float(r['y_hasT']) for r in b]),n0=len(a),n1=len(b)))
 vals=[]
 for mid,cs in permarket.items():
  w=sum(c['w'] for c in cs);a=sum(c['w']*c['a'] for c in cs)/w;b=sum(c['w']*c['b'] for c in cs)/w
  vals.append(dict(market_id=mid,cells=len(cs),arm0=a,arm1=b,diff=b-a))
 return dict(markets=len(vals),cells=sum(v['cells'] for v in vals),marketEqualArm0=mean([v['arm0'] for v in vals]),
 marketEqualArm1=mean([v['arm1'] for v in vals]),marketsLessTWhenAbove=sum(v['diff']<0 for v in vals),marketRows=vals,
 limitation='exploratory within-market sensitivity,>=3side-seconds perarm/cell; not independent decisions or causal effect')


def event_summary(rows):
 rs=[r for r in rows if base(r) and r['z_reference_same_dominant'] and r['z_ref_pre_role']=='STRONG' and r['y_hasT']]
 observed=[r for r in rs if r['z_ask_minus_ref'] is not None]
 lowered=[r for r in observed if r['z_delta_net_side']<-1e-6];notcheaper=[r for r in lowered if r['z_ask_minus_ref']>=-EPS]
 known=[r for r in notcheaper if r['z_public_direction_known'] and r['z_side_distance_change_since_ref'] is not None]
 fields=['asset','block','market_id','t','side','f_ask','f_net','f_up','f_down','f_floor','f_cost','f_seconds_since_same_taker','z_ref_t','z_ref_net','z_ref_ask','z_delta_net_side','z_delta_same_M','z_delta_opp_M','z_delta_opp_T','z_side_spot_distance','z_side_distance_change_since_ref','y_Tqty','y_Tvwap','y_first_observed_T']
 # Contrast witnesses meet prespecified state hypotheses, not largestprofit.
 witness=sorted(notcheaper,key=lambda r:(r['t'],r['market_id']))[:3]
 return dict(repeatedStrongActive=len(rs),allFirstObserved=sum(r['y_all_T_first_observed'] is True for r in rs),
  haveComparablePreAsks=len(observed),preInventoryLower=len(lowered),preInventoryHigher=sum(r['z_delta_net_side']>1e-6 for r in observed),
  lowerInventoryAndNotCheaper=len(notcheaper),notCheaperMarkets=len({r['market_id'] for r in notcheaper}),
  notCheaperBlocks=sorted({r['block'] for r in notcheaper}),
  notCheaperAndNoOppositeActive=sum(r['z_delta_opp_T']<=EPS for r in notcheaper),
  notCheaperDirectionComparisonKnown=len(known),notCheaperPublicDistanceNotImproved=sum(r['z_side_distance_change_since_ref']<=EPS for r in known),
  witnesses=[{k:r[k] for k in fields} for r in witness],
  caveat='Reference priorquote and spotdistance are not desiredqty/fairvalue. Repeated actual fills are not successive submissions.')


def main():
 begin=time.monotonic();op=OUT/'SCORE.json'
 if op.exists():raise FileExistsError(str(op))
 rows=[];inputs=[];coverage={};sourcecols=None
 for block in ['W1','W2','W3','W4','W5','W6']:
  d=read(OUT/(block+'.json'));assert d['sourceScoreSha256']==SRC_SHA and d['sourceSideTableSha256']==TABLE_SHA
  p=Path(d['rows']['path']);assert sha(p)==d['rows']['sha256'];expanded=0;rr=[]
  with gzip.open(p,'rt',encoding='utf-8') as f:
   for line in f:
    expanded+=len(line.encode());assert expanded<48*1024**2;rr.append(json.loads(line))
  assert len(rr)==14400;rows+=rr;inputs.append(dict(summary=sha(OUT/(block+'.json')),rows=d['rows'],public=d['publicSource']))
  for asset in ['BTC','ETH']:
   ss=[r for r in rr if r['asset']==asset]
   coverage[asset+'_'+block]=dict(markets=12,sideRows=len(ss),publicPrior=sum(r['z_public_sample_ms'] is not None for r in ss),
    publicDirectionKnown=sum(r['z_public_direction_known'] for r in ss),completeDirectionAndBook=sum(base(r) and has_direction(r) for r in ss))
 assert len(rows)==86400 and len({(r['market_id'],r['t'],r['side']) for r in rows})==86400
 results={};eventstats={};within={}
 for asset in ['BTC','ETH']:
  rs=[r for r in rows if r['asset']==asset];res={}
  for name,kw in [('inventory_basic',{}),('inventory_time_depth',dict(extended=True)),('inventory_strong_reference',dict(extended=True,ref_role=True)),
      ('inventory_maker_only',dict(extended=True,ref_role=True,maker_only=True)),('inventory_with_direction',dict(extended=True,directional=True))]:
   value,support=contrast(rs,**kw);res[name]=value
   if name=='inventory_with_direction':
    v,_=contrast(rs,extended=True,same_sample_keys=support);res['inventory_no_direction_on_same_support']=v
  v,support=contrast(rs,kind='PRICE',directional=True);res['price_with_direction']=v
  v,_=contrast(rs,kind='PRICE',same_sample_keys=support);res['price_without_direction_same_support']=v
  res['perBlock']={b:contrast([r for r in rs if r['block']==b],extended=True)[0] for b in ['W1','W2','W3','W4','W5','W6']}
  results[asset]=res;eventstats[asset]=event_summary(rs);within[asset]=inventory_within_market(rs)
 # Keep six already-created bounded tables; optional joined export hit128MBcap.
 # Scoring is independent of file packaging; never raise memory to force it.
 target=None;diff=None
 d=dict(version='TARGET_ROUTE_MECHANISM_DISCRIMINATION_V1',status='OBSERVATIONAL_COMPETING_MECHANISMS_NOT_IDENTIFIED_PRIVATE_CONTROL',
  markets=144,sideRows=86400,inputs=inputs,publicCoverage=coverage,results=results,eventChecks=eventstats,withinMarketSensitivity=within,
  originalColumnsMismatch=diff,parquet=None,sourceFieldParity='not yet independently compared;new rows copy originals, later verification required',boundedTables=[x['rows'] for x in inputs],
  newHFT=0,newTraining=0,modelFits=0,policyChanges=0,elapsed=time.monotonic()-begin,
  limits=['Prior post-samesideTaker net is a reference, NOT observed desired exposure; lower/higher is not proof of under/overallocated work.',
   'Directionpublic snapshots may lack constituent timing; capturedbefore does not certify sourcefreshness or Target fairvalue.',
   'Historical ETH has no underlying source; no crossasset substitution or null-to-neutral fill.',
   'Only actual fills are observed; latency/matching/participation and hidden orders can change next observed intensity.',
   'Contrasts coarse-match currentstate andtime; support changes, endogeneity and serial dependence remain, no causal/invariant-policy proof.',
   'Withinmarket matchedcells are postdesign sensitivity; eachsecond is not independent evidence.',
   'Full book/sample/hash reconciliation does not validate the optimality or legality of any new OUR action.'])
 write(op,d)
 def small(x):return {k:v for k,v in x.items() if k!='cells'}
 print(json.dumps(dict(output=str(op),bytes=op.stat().st_size,sha256=sha(op),seconds=d['elapsed'],coverage=coverage,
  results={a:{k:(v if k=='perBlock' else small(v)) for k,v in rs.items() if k!='perBlock'} for a,rs in results.items()},
  block_inventory={a:{b:small(v) for b,v in rs['perBlock'].items()} for a,rs in results.items()},
  events=eventstats,withinMarket={a:{k:v for k,v in z.items() if k!='marketRows'} for a,z in within.items()},
  parquet=d['parquet'],originalColumnsMismatch=diff),ensure_ascii=False))


if __name__=='__main__':main()
