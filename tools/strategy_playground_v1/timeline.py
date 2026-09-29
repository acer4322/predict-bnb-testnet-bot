"""Convert recorded native facts to a time-indexed view. No fill simulation."""
from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import gzip,hashlib,json,math

SIDES=('UP','DOWN')
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def zipped(p):return json.loads(gzip.decompress(Path(p).read_bytes()))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load_case(path):
    p=Path(path)
    return read(p/'result.json'),zipped(p/'clock_trace.json.gz'),zipped(p/'joint_operator_trace.json.gz'),zipped(p/'neural_trace.json.gz')

def build(path,market,start,end,settings,origin,audit=None):
    raw,clock,joint,neural=load_case(path)
    if raw['status']!='COMPLETE' or not raw['execution_accounting_valid']:raise ValueError('Incomplete native case cannot become a playback')
    owners_by_key={o['key']:o for o in clock['demand_final']['all_final_carriers']}
    points=defaultdict(list);fills=[];orders=[]
    rows={r['t']:r for r in neural['rows'] if r['status']=='NN'}
    for d in joint['decisions']:points[float(d['t'])].append(('decision',d))
    for r in clock['states']:points[float(r['t'])].append(('state',r))
    for r in clock['demand_owner_rows']:points[float(r['t'])].append(('owner',r))
    for p in clock['plans']:points[float(p['t'])].append(('plan',p))
    inv={s:0. for s in SIDES};paid=0.
    # Each receipt records incremental quantity/cost, not a cumulative fill credit.
    receipts=clock['demand_final']['canonical_receipts']
    for r in receipts:
        if r['qty']<=0:continue
        t=int(r['receive_ts'])/1000000
        points[t].append(('receipt',r))
        o=owners_by_key[r['key']]
        fills.append({'ms':(int(r['receive_ts'])-start*1000000)/1000000,'side':o['side'],'route':o['route'],
            'qty':r['qty'],'price':r.get('contractPrice',r['price']),'fee':r.get('fee',0.),'key':r['key']})
    frames=[];owners={};book={'bid':None,'ask':None};decision={'action':'尚無決策','settings':settings}
    # Display information is carried forward only; no interpolated price/inventory.
    for t,items in sorted(points.items()):
        for kind,r in items:
            if kind!='receipt':continue
            o=owners_by_key[r['key']];q=float(r['qty']);price=float(r.get('contractPrice',r['price']))
            inv[o['side']]+=q;paid+=q*price+float(r.get('fee',0.))
            if r['key'] in owners:owners[r['key']]['qty']=max(0.,owners[r['key']]['qty']-q)
        for kind,r in items:
            if kind=='owner':
                key=r['key'];o=owners_by_key[key]
                if r['state']=='TERMINAL':owners.pop(key,None)
                else:owners[key]={'key':key,'side':o['side'],'route':o['route'],'limit':r['limit'],
                    'qty':max(0.,float(r.get('remaining',r['qty']-r['filled']))),'state':r['state']}
            elif kind=='decision':
                owners={o['key']:dict(o) for o in r['state']['owners']}
                book={k:r['public_features'].get(k) for k in ('bid','ask')}
                nn=rows.get(r['t'])
                if nn:
                    legal=[i for i,v in enumerate(nn['native_legality']) if v=='LEGAL']
                    score=nn['scores_main'];top=sorted(legal,key=lambda i:score[i],reverse=True)
                    decision={'action':nn['action_name'],'index':nn['index'],'rolling_h':nn['rolling_h'],
                        'selected_score':score[nn['action']],'score_gap':score[top[0]]-score[top[1]] if len(top)>1 else None,
                        'settings':nn.get('playground',{}).get('settings',settings),'phase':'R65_NN'}
                else:decision={'action':'原生共同開局','index':r['index'],'settings':settings,'phase':'LEGACY_OPENING'}
            elif kind=='plan':
                for op in r['operations']:
                    if op['kind']=='NEW':
                        owners[op['key']]={'key':op['key'],'side':op['side'],'route':op['route'],'limit':op['price'],'qty':op['qty'],'state':'SUBMITTED'}
                        orders.append({'ms':t-start,**op})
                    elif op['kind']=='CANCEL' and op['key'] in owners:
                        # A cancel request retains the quantity and its reservation.
                        owners[op['key']]['state']='CANCEL_PENDING';orders.append({'ms':t-start,**op})
        p={s:inv[s]-paid for s in SIDES}
        pending={s:sum(o['qty']*o['limit'] for o in owners.values() if o['side']==s) for s in SIDES}
        frames.append({'ms':t-start,'quote':dict(book),'inv':dict(inv),'cost':paid,'payoff':p,
            'floor':min(p.values()),'pending_cash':pending,'pending_floor':min(p['UP']-pending['DOWN'],p['DOWN']-pending['UP']),
            'owners':[dict(o) for o in owners.values()],'decision':dict(decision)})
    if abs(paid-raw['final_cost'])>1e-5 or any(abs(inv[s]-raw['final_inventory'][s])>1e-5 for s in SIDES):
        raise ValueError('Playback receipt totals differ from native result')
    if not frames:raise ValueError('No native timeline')
    # Final reconciliation is a separate audited record, never backdated onto t=end.
    final={'inv':raw['final_inventory'],'cost':raw['final_cost'],'payoff':{s:raw['final_inventory'][s]-raw['final_cost'] for s in SIDES},
        'unresolved_owners':raw['unresolved_owners'],'last_observation_ms':frames[-1]['ms']}
    return {'version':'NATIVE_TIME_PLAYBACK_V1','market':market,'start_ms':start,'end_ms':end,'duration_ms':end-start,
        'settings':settings,'origin':origin,'frames':frames,'fills':sorted(fills,key=lambda x:x['ms']),'orders':orders,'final':final,
        'audit':audit,'source_hashes':{n:sha(Path(path)/n) for n in ('result.json','clock_trace.json.gz','joint_operator_trace.json.gz','neural_trace.json.gz')},
        'scope':{'native_hft':True,'fees':0,'live_authority':False,'consumed_market':True,
                 'initial_direction':'SHARED_LEGACY_UP_NOT_WINNER','nn_time_input':'h1..3, not raw seconds',
                 'quotes':'recorded decision-time public book','state':'canonical received fills; no interpolation',
                 'evidence':'DIAGNOSTIC_NOT_NET_LIVE_PNL'}}

def prefix_compare(base_path,new_path,t_ms,whole=False):
    br,bc,bj,bn=load_case(base_path);nr,nc,nj,nn=load_case(new_path)
    pred=lambda t:whole or t<t_ms
    checks={}
    for key in ('plans','native_actions','intent','states','demand_owner_rows'):
        a=[r for r in bc[key] if pred(r['t'])];b=[r for r in nc[key] if pred(r['t'])]
        checks[key]=a==b
    a=[r for r in bc['demand_final']['canonical_receipts'] if pred(int(r['receive_ts'])/1000000)]
    b=[r for r in nc['demand_final']['canonical_receipts'] if pred(int(r['receive_ts'])/1000000)]
    checks['canonical_receipts']=a==b
    def projected(z):
        return [{k:v for k,v in r.items() if k!='playground'} for r in z['rows'] if pred(r['t'])]
    checks['model_inputs_scores_actions']=projected(bn)==projected(nn)
    checks['joint_decisions']=[r for r in bj['decisions'] if pred(r['t'])]==[r for r in nj['decisions'] if pred(r['t'])]
    if whole:
        checks['final_inventory']=br['final_inventory']==nr['final_inventory'];checks['final_cost']=br['final_cost']==nr['final_cost']
    return {'pass':all(checks.values()),'whole_path':whole,'strict_before_t_ms':t_ms,'checks':checks}
