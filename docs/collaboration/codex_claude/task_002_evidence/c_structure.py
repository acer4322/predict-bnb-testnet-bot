"""TASK_002 work C: structure ratios on the fixed v28 10 markets for v24 PADD80, v27 AR3_R25, v28 AR4_R25.
P=max(UP,0)+max(DOWN,0), L=max(-UP,0)+max(-DOWN,0), eps=1e-8. Official winner (target_markets.winner) only for offline scoring.
Also post-first-flip fills by side x route for the two case markets (evidence for the 'repair-then-add' candidate)."""
import json,gzip,hashlib,sqlite3,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];RS=ROOT/'data'/'research';LW=RS/'lan_worker_returns'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
EPS=1e-8
M=json.loads((RS/'v12g_active_repair4_small_20260927_v28'/'MARKETS.json').read_text())['markets']
t=sqlite3.connect(f'file:{ROOT/"data"/"target_wallet_official_v1.db"}?mode=ro',uri=True)
W={m:w for m,w in t.execute("select market_id,winner from target_markets where asset='BTC' and market_id in (%s)"%','.join(map(str,M)))}
GROUPS=[('v24_PADD80','btc5m-v12g-fresh30-20260927-v24','v12g24','PADD80'),('v27_AR3_R25','btc5m-v12g-active-repair3-small-20260927-v27','v12g27','AR3_R25'),('v28_AR4_R25','btc5m-v12g-active-repair4-small-20260927-v28','v12g28','AR4_R25')]
def cls(up,dn):
    if up>EPS and dn>EPS:return 'BOTH_POSITIVE'
    if (up>EPS and abs(dn)<=EPS) or (dn>EPS and abs(up)<=EPS):return 'POSITIVE_ZERO'
    if up<=EPS and dn<=EPS:return 'BOTH_NONPOSITIVE'
    P=max(up,0)+max(dn,0);L=max(-up,0)+max(-dn,0)
    return 'MIXED_P_GT_L' if P>L+EPS else 'MIXED_P_LE_L'
out={'markets':M,'official_winner':{str(k):v for k,v in W.items()},'groups':{},'per_market':{}}
for g,job,pre,arm in GROUPS:
    rows={r['market']:r for r in json.loads((LW/job/'ROWS.json').read_text(encoding='utf-8')) if r['arm']==arm and r['market'] in M}
    recs=[]
    for m in M:
        r=rows.get(m);rec=dict(market=m,group=g)
        if r is None:rec.update(status='MISSING');recs.append(rec);continue
        d=LW/job/'arms'/f'{pre}_{arm}_{m}';res=json.loads(gzip.decompress((d/'result.json.gz').read_bytes()))
        ao=res.get('active_filled_orders');po=res.get('passive_filled_orders')
        sg=res.get('safety_gate') or {};failed=[k for k,v in sg.items() if k!='pass' and not v]
        up,dn=r['UP'],r['DOWN'];P=max(up,0)+max(dn,0);L=max(-up,0)+max(-dn,0)
        rec.update(status=res.get('status'),result_sha256=sha(d/'result.json.gz'),UP=round(up,3),DOWN=round(dn,3),winner=W.get(m),winner_payoff=round(r[W[m]],3) if W.get(m) else None,cost=round(r['cost'],2),
                   P=round(P,3),L=round(L,3),structure=cls(up,dn),p_gt_l=(P>EPS and P>L+EPS),
                   active_fill_qty=round(float(res.get('active_fill_qty') or 0),2),passive_fill_qty=round(float(res.get('passive_fill_qty') or 0),2),
                   active_filled_orders=(len(ao) if isinstance(ao,list) else ao),passive_filled_orders=(len(po) if isinstance(po,list) else po),
                   unresolved_owners=res.get('unresolved_owners'),accounting_valid=res.get('execution_accounting_valid'),safety_failed=failed,
                   validity=('FULL_PASS' if not failed else 'COMPLETE_ACCOUNTING_VALID__LEGACY_ACTIVE_COUNT_ASSERT_ONLY' if failed==['active_matches_opportunity'] and res.get('unresolved_owners')==0 and res.get('execution_accounting_valid') else 'UNKNOWN'))
        recs.append(rec)
    fin=[x for x in recs if x.get('status')=='COMPLETE']
    conf=[x for x in fin if x['validity'] in ('FULL_PASS','COMPLETE_ACCOUNTING_VALID__LEGACY_ACTIVE_COUNT_ASSERT_ONLY') and x['unresolved_owners']==0]
    def ratio(xs):
        n=len(xs)
        if n==0:return dict(n=0,N=0,pct='N/A')
        k=sum(x['p_gt_l'] for x in xs);return dict(n=k,N=n,pct=round(100*k/n,1))
    mixed=[x for x in conf if x['structure'].startswith('MIXED')]
    wp=[x for x in conf if x['winner_payoff'] is not None]
    out['groups'][g]=dict(denominators=dict(planned=len(M),artifact_complete=len(fin),nominal_pnl_computable=len(fin),diagnosed_usable=len(conf),responsibility_confirmed_unresolved_zero=sum(1 for x in fin if x['unresolved_owners']==0),unknown_or_missing=len(M)-len(conf)),
        P_gt_L_nominal=ratio(fin),P_gt_L_confirmed=ratio(conf),structure_counts=dict(collections.Counter(x['structure'] for x in conf)),mixed_only_P_gt_L=ratio(mixed),
        winner_positive=dict(n=sum(x['winner_payoff']>EPS for x in wp),N=len(wp),pct=round(100*sum(x['winner_payoff']>EPS for x in wp)/len(wp),1) if wp else 'N/A'),
        sum_P=round(sum(x['P'] for x in conf),1),sum_L=round(sum(x['L'] for x in conf),1),note_sums='sum_P/sum_L are totals, not the market share metric',
        validity_counts=dict(collections.Counter(x['validity'] for x in fin)))
    for x in recs:out['per_market'].setdefault(str(x['market']),{})[g]=x
# paired changes vs v24 PADD80
pair={}
for g in ('v27_AR3_R25','v28_AR4_R25'):
    rows=[];
    for m in M:
        a=out['per_market'][str(m)]['v24_PADD80'];b=out['per_market'][str(m)][g]
        if a.get('status')!='COMPLETE' or b.get('status')!='COMPLETE':continue
        w=W[m];lw='DOWN' if w=='UP' else 'UP'
        rows.append(dict(market=m,winner_delta=round(b['winner_payoff']-a['winner_payoff'],2),loser_delta=round(b[lw]-a[lw],2),P_delta=round(b['P']-a['P'],2),L_delta=round(b['L']-a['L'],2),
                         positive_retained=(round(b['P']/a['P'],3) if a['P']>EPS else None),loss_reduction=round(a['L']-b['L'],2)))
    pair[g]=dict(rows=rows,winner_improved=sum(r['winner_delta']>EPS for r in rows),winner_worse=sum(r['winner_delta']<-EPS for r in rows),L_reduced=sum(r['L_delta']<-EPS for r in rows),L_increased=sum(r['L_delta']>EPS for r in rows),
                 P_reduced=sum(r['P_delta']<-EPS for r in rows),sum_positive_retained=round(sum(out['per_market'][str(r['market'])][g]['P'] for r in rows),1),sum_positive_base=round(sum(out['per_market'][str(r['market'])]['v24_PADD80']['P'] for r in rows),1),
                 sum_loss_reduction=round(sum(r['loss_reduction'] for r in rows),1))
out['paired_vs_v24']=pair
out['v27_v28_identical']=all(abs(out['per_market'][str(m)]['v27_AR3_R25'][k]-out['per_market'][str(m)]['v28_AR4_R25'][k])<1e-9 for m in M for k in ('UP','DOWN','cost'))
# post-first-flip fills by side x route x role (case markets, v28)
J=LW/'btc5m-v12g-active-repair4-small-20260927-v28';rows28={(r['arm'],r['market']):r for r in json.loads((J/'ROWS.json').read_text(encoding='utf-8'))}
post={}
for m in (2629199,2628553):
    d=J/'arms'/f'v12g28_AR4_R25_{m}';res=json.loads(gzip.decompress((d/'result.json.gz').read_bytes()));ct=json.loads(gzip.decompress((d/'clock_trace.json.gz').read_bytes()))
    info={o['key']:(o['side'],o.get('route'),o.get('role') or '') for pl in ct['plans'] for o in pl['operations'] if o['kind']=='NEW'}
    tf=next(e['t'] for e in rows28[('AR4_R25',m)]['v12g']['events'] if e['kind']=='FLIP')
    c=collections.Counter()
    for e in res.get('atomic_responsibility_events') or []:
        if e['t']<tf:continue
        for fr in e['fill_rows']:
            if fr['key'] in info:
                s,rt_,role=info[fr['key']];c[f'{s}|{rt_}|{role}']+=fr['fill_increment']
    post[str(m)]={k:round(v,1) for k,v in sorted(c.items())}
out['post_first_flip_fills_v28']=post
(Path(__file__).parent/'C_STRUCTURE.json').write_text(json.dumps(out,indent=1,ensure_ascii=False,default=str),encoding='utf-8')
for g,x in out['groups'].items():print(g,json.dumps(x,ensure_ascii=False))
for g,x in pair.items():print(g,{k:v for k,v in x.items() if k!='rows'})
print('identical v27/v28',out['v27_v28_identical']);print(json.dumps(post))
for m in M:
    pm=out['per_market'][str(m)];print(m,W[m],[(g,pm[g]['UP'],pm[g]['DOWN'],pm[g]['structure'],pm[g]['active_filled_orders'],pm[g]['passive_filled_orders'],pm[g]['unresolved_owners']) for g in ('v24_PADD80','v28_AR4_R25')])
