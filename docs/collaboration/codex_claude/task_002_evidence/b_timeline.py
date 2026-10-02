"""TASK_002 work B: event timelines for AR4_R25 2629199 and 2628553 (v28), plus first-vs-DECIDE for all 10 AR4 markets and v25.
Read-only; compressed artifacts decoded in memory; only selected fields are written."""
import json,gzip,hashlib,bisect,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];RS=ROOT/'data'/'research';LW=RS/'lan_worker_returns'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
J28=LW/'btc5m-v12g-active-repair4-small-20260927-v28';BASE=RS/'v12g_fresh30_20260927_v24'/'base'
rows28={(r['arm'],r['market']):r for r in json.loads((J28/'ROWS.json').read_text(encoding='utf-8'))}

def load(m,arm='AR4_R25',J=J28,pre='v12g28'):
    d=J/'arms'/f'{pre}_{arm}_{m}'
    g=lambda n:json.loads(gzip.decompress((d/n).read_bytes()))
    return d,g('result.json.gz'),g('clock_trace.json.gz'),g('restoration_trace.json.gz')

def pub(m):
    return json.loads(gzip.decompress((BASE/'inputs'/f'public_{m}.json.gz').read_bytes()))

def timeline(m):
    d,res,ct,rt=load(m);p=pub(m);s0=p['market']['window_start_ms'];e0=p['market']['window_end_ms']
    bk=[(x['received_ms'],x.get('source_ms'),x['best_bid'],x['best_ask']) for x in p['books'] if x['best_bid'] is not None and x['best_ask'] is not None];bts=[x[0] for x in bk]
    def upmid(t):
        b=bk[max(0,bisect.bisect_right(bts,t)-1)];return round((b[2]+b[3])/2,4)
    row=rows28[('AR4_R25',m)]
    ev=[dict(kind=e['kind'],t=e['t'],side=e.get('side') or e.get('to'),frm=e.get('frm')) for e in row['v12g']['events']]
    info={o['key']:dict(side=o['side'],price=o['price'],route=o.get('route'),role=o.get('role'),qty=o['qty']) for pl in ct['plans'] for o in pl['operations'] if o['kind']=='NEW'}
    newt={o['key']:pl.get('t') for pl in ct['plans'] for o in pl['operations'] if o['kind']=='NEW'}
    fills=sorted((e['t'],fr['key'],fr['fill_increment']) for e in res.get('atomic_responsibility_events') or [] for fr in e['fill_rows'] if fr['key'] in info)
    def inv_at(T):
        inv={'UP':0.,'DOWN':0.};c=0.
        for t,k,x in fills:
            if t<=T:inv[info[k]['side']]+=x;c+=x*info[k]['price']
        return dict(UP=round(inv['UP'],2),DOWN=round(inv['DOWN'],2),cost=round(c,2),P_UP=round(inv['UP']-c,2),P_DOWN=round(inv['DOWN']-c,2))
    def side_at(T):
        s=[e['side'] for e in ev if T is not None and e['t']<=T];return s[-1] if s else None
    table=[]
    for e in ev:
        table.append(dict(t_raw=e['t'],t_rel_s=round((e['t']-s0)/1000,3),clock='frame t (runner SOURCE_EVENT clock)',event=e['kind'],detail=dict(to=e['side'],frm=e['frm']),chosen_after=e['side'],state=inv_at(e['t']),upmid_recv=upmid(e['t'])))
    f=rt.get('first')
    if f:table.append(dict(t_raw=f['t'],t_rel_s=round((f['t']-s0)/1000,3),clock='frame t',event='FIRST(ctx.first)',detail=dict(action=f.get('action'),key=f.get('key')),chosen_after=side_at(f['t']),state=inv_at(f['t'])))
    last_H=None;prev_w=None
    for x in rt['events']:
        if not x.get('eligible'):continue
        table.append(dict(t_raw=x['t'],t_rel_s=round((x['t']-s0)/1000,3),clock='frame t',event='RESTORATION_SUBMITTED',
            detail=dict(weak_side=x['side'],price=x['price'],qty=round(x['quantity'],2),G_strong=round(x['G'],2),H_weak=round(x['H'],2),peak_G=round(x['peak_G'],2),retained_floor=round(x['retained_G_floor'],2),partial=x.get('partial'),trigger=x.get('v12g_trigger'),last_H_before=last_H,last_H_same_branch=(None if prev_w is None else prev_w==x['side'])),
            chosen_after=side_at(x['t']),state=inv_at(x['t'])))
        last_H=round(x['H'],2);prev_w=x['side']
    act=collections.defaultdict(lambda:[0.,0])
    for t,k,x in fills:
        i=info[k]
        if i['route']=='ACTIVE':
            a=act[(k,i['side'],i['role'],i['price'])];a[0]+=x;a[1]=max(a[1],t)
    for (k,s,role,px),(q,tl) in act.items():
        tn=newt.get(k)
        table.append(dict(t_raw=tn,t_rel_s=round((tn-s0)/1000,3) if tn else None,clock='plan t (NEW)',event='ACTIVE_ORDER_FILLED',detail=dict(key=k,side=s,role=role,price=px,filled_qty=round(q,2),last_fill_rel_s=round((tl-s0)/1000,3)),chosen_after=side_at(tn or tl)))
    table.sort(key=lambda r:(r['t_raw'] if r['t_raw'] is not None else 0))
    actsum=collections.Counter()
    for (k,s,role,px),(q,tl) in act.items():actsum[(s,role)]+=q
    post=dict(window_start_ms=s0,window_end_ms=e0,last_book_source_ms=max((x[1] or 0) for x in bk),last_book_recv_ms=max(bts),
              news_after_end=[dict(key=k,rel_s=round((t-s0)/1000,3),**info[k]) for k,t in newt.items() if t is not None and t>=e0],
              fills_after_end=[dict(key=k,rel_s=round((t-s0)/1000,3),qty=x,**info[k]) for t,k,x in fills if t>=e0],
              final_from_fills=inv_at(10**15),final_before_end=inv_at(e0-1),row_final=dict(UP=row['UP'],DOWN=row['DOWN'],cost=row['cost']))
    return dict(market=m,sources=dict(result=sha(d/'result.json.gz'),clock_trace=sha(d/'clock_trace.json.gz'),restoration_trace=sha(d/'restoration_trace.json.gz'),row=sha(d/'row.json'),public=sha(BASE/'inputs'/f'public_{m}.json.gz')),
                gate=dict(AR_NOGATE='1 (job env)',admissions_recorded=len(rt.get('admissions') or []),note='Context.check returns True before recording when AR_NOGATE is set: gate never evaluated in v28'),
                peak_final=rt.get('peak_G'),active_fill_by_side_role={f'{s}|{r}':round(q,1) for (s,r),q in actsum.items()},events=table,expiry=post)

out={'2629199':timeline(2629199),'2628553':timeline(2628553)}
fd=[]
for (arm,m),r in rows28.items():
    if arm!='AR4_R25':continue
    d,res,ct,rt=load(m);s0=pub(m)['market']['window_start_ms'];dec=next(e['t'] for e in r['v12g']['events'] if e['kind']=='DECIDE');f=rt.get('first') or {}
    fd.append(dict(job='v28',market=m,decide_rel_s=round((dec-s0)/1000,3),first_rel_s=round((f['t']-s0)/1000,3) if f else None,first_action=f.get('action'),first_before_decide=(f['t']<dec) if f else None))
J25=LW/'btc5m-v12g-active-repair-20260927-v25'
for r in json.loads((J25/'ROWS.json').read_text(encoding='utf-8')):
    if r['control'] or r['arm']!='AR_DP':continue
    m=r['market'];d,res,ct,rt=load(m,'AR_DP',J25,'v12g25');s0=pub(m)['market']['window_start_ms']
    dec=next((e['t'] for e in (r.get('v12g') or {}).get('events',[]) if e['kind']=='DECIDE'),None);f=rt.get('first') or {}
    fd.append(dict(job='v25',market=m,decide_rel_s=round((dec-s0)/1000,3) if dec else None,first_rel_s=round((f['t']-s0)/1000,3) if f else None,first_action=f.get('action'),first_before_decide=(f['t']<dec) if f and dec else (True if f and dec is None else None),admissions=len(rt.get('admissions') or []),admissions_blocked=sum(not x['allowed'] for x in rt.get('admissions') or [])))
out['first_vs_decide']=fd
(Path(__file__).parent/'B_TIMELINE.json').write_text(json.dumps(out,indent=1,ensure_ascii=False,default=str),encoding='utf-8')
for k in ('2629199','2628553'):
    x=out[k];print('==',k,'expiry',json.dumps(x['expiry'],default=str)[:1200]);print(' gate',x['gate'],'peak_final',x['peak_final'],'act',x['active_fill_by_side_role'])
    for r in x['events']:
        if r['event']!='ACTIVE_ORDER_FILLED':print(' ',r['t_rel_s'],r['event'],json.dumps(r['detail'],default=str)[:360],'| chosen',r.get('chosen_after'),'|',r.get('state'))
print('v28 first vs decide',[(x['market'],x['decide_rel_s'],x['first_rel_s'],x['first_action']) for x in fd if x['job']=='v28'])
v25=[x for x in fd if x['job']=='v25'];print('v25 first_before_decide',sum(1 for x in v25 if x['first_before_decide']),'/',len(v25),dict(collections.Counter(x['first_action'] for x in v25)),'decide None',sum(1 for x in v25 if x['decide_rel_s'] is None))
