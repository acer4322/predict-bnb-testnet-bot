"""Source integrity and cash/quantity differential verification; no native execution."""
import json
from audit_btc5m_payoff_repair_panel_v1 import PACKAGE,R,RET,STEM,read,sha,get


def main():
    m=read(PACKAGE/'manifest.json');checks={name:sha(PACKAGE/name)==h for name,h in m['files'].items()};assert all(checks.values())
    assert read(R/'BTC5M_PAYOFF_REPAIR_PREREGISTERED_20260913.json')==m
    a=read(R/(STEM+'_RESULT.json'))['arms'];traces={}
    for tag in ('quantity','zero','minus-one'):
        d,tr=get(RET/f'payoff-repair-2026085-{tag}-20260913-v1');traces[tag]=tr
        assert a[tag]['payoff_curve'][-1]['t']==1788758108845
        assert a[tag]['metrics']['inventory_up']==a['quantity']['metrics']['inventory_up']
        assert abs(a[tag]['payoff']['up']-(d['final_inventory']['UP']-d['final_cost']))<1e-7
        assert abs(a[tag]['payoff']['down']-(d['final_inventory']['DOWN']-d['final_cost']))<1e-7
        # Actual cumulative acquisition path pays the sole old lot at the same reported clock.
        start=tr['money_events'][0]['state'];cut=m['selection']['t'];old=m['selection']['initial_remaining']
        completed=next(r['t']-cut for r in tr['states'] if r['t']>cut and r['inv']['DOWN']-start['inv']['DOWN']>=old-1e-9)
        assert completed==a[tag]['old_fifo_cohort']['exact_completion_ms']
        # Tail states include the terminal drain. Verify no hidden extra end fill or cost.
        tail=[r for r in tr['states'] if r['t']>1788758108845]
        assert all(r['inv']==d['final_inventory'] and abs(r['cost']-d['final_cost'])<1e-7 for r in tail)
    contrasts={}
    for tag in ('zero','minus-one'):
        q=a['quantity'];x=a[tag]
        cost_saved=q['metrics']['cost']-x['metrics']['cost']
        up_gain=x['payoff']['up']-q['payoff']['up']
        down_reduction=q['metrics']['inventory_down']-x['metrics']['inventory_down']
        assert abs(cost_saved-up_gain)<1e-7
        assert abs((q['payoff']['down']-x['payoff']['down'])-(down_reduction-cost_saved))<1e-7
        contrasts[tag]=dict(cost_saved_vs_quantity=cost_saved,up_payoff_gain=up_gain,
            fewer_down_shares=down_reduction,down_payoff_sacrificed=q['payoff']['down']-x['payoff']['down'])
    # Zero/quantity share all submitted order keys/times/prices. Only two sizes differ.
    def plans(tag):return [(p['t'],o) for p in traces[tag]['plans'] for o in p['operations'] if o['kind']=='NEW']
    qp,zp=plans('quantity'),plans('zero');assert len(qp)==len(zp)
    differences=[]
    for (qt,qo),(zt,zo) in zip(qp,zp):
        assert qt==zt and {k:v for k,v in qo.items() if k!='qty'}=={k:v for k,v in zo.items() if k!='qty'}
        if qo['qty']!=zo['qty']:differences.append(dict(t=qt,key=qo['key'],price=qo['price'],quantity_qty=qo['qty'],zero_qty=zo['qty']))
    assert len(differences)==2
    stated_notional_delta=sum((r['quantity_qty']-r['zero_qty'])*r['price'] for r in differences)
    assert abs(stated_notional_delta-contrasts['zero']['cost_saved_vs_quantity'])<1e-7
    out=dict(status='PASS',package_hashes=checks,isolated_same_up_inventory=True,terminal_drain_no_extra_economics=True,
        independent_old_completion=True,contrasts=contrasts,quantity_vs_zero_new_size_differences=differences,
        matching_price_notional_delta=stated_notional_delta,
        interpretation='Cash budget implements the constructed payoff shape; anchor attainment is not evidence of Target threshold identification.')
    (R/'BTC5M_PAYOFF_REPAIR_VERIFICATION_20260913.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');print(json.dumps(out))


if __name__=='__main__':main()
