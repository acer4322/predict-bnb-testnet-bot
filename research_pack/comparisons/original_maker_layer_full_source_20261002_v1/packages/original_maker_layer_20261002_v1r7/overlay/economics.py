"""Payoff-geometric restoration and unresolved-order accounting. No prediction labels."""
import math,os
EPS=1e-8
# V12x experiment: only these two constants are parameterised (defaults = original V12).
K=float(os.environ.get('V12X_K','2'))
RETAIN=float(os.environ.get('V12X_RETAIN','.5'))

def with_plan(state,operations):
    out={**state,'pending_qty':dict(state['pending_qty']),'pending_cash':dict(state['pending_cash']),'owners':[dict(x) for x in state['owners']]}
    known={x['key'] for x in out['owners']}
    for op in operations:
        if op['kind']!='NEW':continue
        assert op['key'] not in known,'Do not double-count plan reservations'
        known.add(op['key']);side=op['side'];q=float(op['qty']);p=float(op['price'])
        out['pending_qty'][side]+=q;out['pending_cash'][side]+=q*p
        out['owners'].append({'key':op['key'],'side':side,'qty':q,'limit':p,'state':'SAME_PLAN_UNCONFIRMED'})
    # CANCEL requests deliberately do not release anything.
    return out

def geometry(state,strong):
    weak='DOWN' if strong=='UP' else 'UP';g=float(state['payoff'][strong]);h=float(state['payoff'][weak])
    margin=g+K*h;penalty=0.
    for owner in state['owners']:
        weight=1. if owner['side']==strong else K
        penalty+=min(0.,float(owner['qty'])*(weight-(K+1)*float(owner['limit'])))
    return {'G':g,'H':h,'margin':margin,'worst_margin':margin+penalty,'negative_pending_margin':penalty,
            'worst_G':g-float(state['pending_cash'][weak]),'weak_pending_cash':float(state['pending_cash'][weak]),
            'net_unreserved':max(0.,float(state['inv'][strong])-float(state['inv'][weak])-float(state['pending_qty'][weak]))}

def proposal(state,strong,ask,depth,peak,step=.01):
    x=geometry(state,strong);row={**x,'eligible':False,'price':ask,'visible_depth':depth,'peak_G':peak,'retained_G_floor':RETAIN*peak,'target_ratio':K}
    if ask is None or not 0<float(ask)<1 or depth<=0:return {**row,'reason':'NO_VALID_BOOK'}
    p=float(ask);slope=K-(K+1)*p;debt=max(0.,-x['worst_margin'])
    if x['G']<=0 or x['H']>=0 or debt<=EPS:return {**row,'reason':'NO_RESTORABLE_NEGATIVE_BRANCH'}
    if slope<=EPS:return {**row,'reason':'REPAIR_PRICE_CANNOT_IMPROVE_TARGET_RATIO'}
    needed=debt/slope;cash_capacity=max(0.,x['worst_G']-RETAIN*peak)/p
    total_cap=min(cash_capacity,x['net_unreserved'])
    row.update(needed_qty=needed,full_target_cost=needed*p,cash_capacity=cash_capacity,total_cap=total_cap,slope=slope)
    if needed>total_cap+EPS:return {**row,'reason':'FULL_RESTORATION_NOT_AFFORDABLE'}
    quantity=max(0.,round(math.floor((min(needed,depth,total_cap)+1e-9)/step)*step,8))
    if quantity<=EPS or quantity*p<1.-EPS:return {**row,'reason':'BELOW_LEGAL_ACTIVE_MINIMUM','quantity':quantity}
    return {**row,'eligible':True,'reason':'QUALIFIED_RESTORATION_OPPORTUNITY','quantity':quantity,'quoted_cost':quantity*p,
            'after_G_if_filled':x['G']-quantity*p,'after_H_if_filled':x['H']+quantity*(1-p),
            'depth_limited':quantity<needed-step,'not_a_fill':True}

def admission(state,strong,side,price,quantity,peak,joint):
    x=geometry(state,strong);q=float(quantity);p=float(price);weak='DOWN' if strong=='UP' else 'UP'
    delta=q*((1. if side==strong else K)-(K+1)*p)
    margin_ok=x['worst_margin']+delta>=min(0.,x['worst_margin'])-EPS
    reserve_ok=side==strong or x['worst_G']-p*q>=RETAIN*peak-EPS
    allowed=(reserve_ok and margin_ok) if side==weak else (margin_ok if joint else True)
    return {'allowed':allowed,'side':side,'price':p,'quantity':q,'delta_margin':delta,'margin_ok':margin_ok,'reserve_ok':reserve_ok,'peak_G':peak,**x}

def self_test():
    # Original fixtures assume the original constants: test them under K=2, RETAIN=.5, then restore.
    global K,RETAIN
    saved=(K,RETAIN);K,RETAIN=2.,.5
    try:return _self_test()
    finally:K,RETAIN=saved

def _self_test():
    checks=[]
    for strong in ['UP','DOWN']:
        weak='DOWN' if strong=='UP' else 'UP'
        s={'inv':{strong:660.,weak:126.},'payoff':{strong:160.,weak:-374.},'pending_qty':{strong:0.,weak:0.},'pending_cash':{strong:0.,weak:0.},'owners':[]}
        p=proposal(s,strong,.12,1000.,225.)
        assert p['eligible'] and p['quoted_cost']<=47.5+EPS and p['after_G_if_filled']>=112.5-EPS;checks.append(strong+'_feasible_repair')
        assert not proposal(s,strong,.99,1000.,225.)['eligible'];checks.append(strong+'_costly_repair_rejected')
        assert not proposal(s,strong,.30,1000.,225.)['eligible'];checks.append(strong+'_unaffordable_full_target_rejected')
        limited=proposal(s,strong,.12,20.,225.);assert limited['eligible'] and limited['depth_limited'] and limited['quantity']==20.;checks.append(strong+'_visible_depth')
        assert not proposal(s,strong,.12,2.,225.)['eligible'];checks.append(strong+'_minimum_notional')
        pending=with_plan(s,[{'kind':'NEW','key':'a','side':strong,'qty':15.,'price':.8}])
        assert geometry(pending,strong)['worst_margin']<geometry(s,strong)['worst_margin'];checks.append(strong+'_pending_risk')
        assert with_plan(pending,[{'kind':'CANCEL','key':'a'}])==pending;checks.append(strong+'_cancel_no_release')
        repaired={**s,'payoff':{strong:120.,weak:-60.}}
        assert not admission(repaired,strong,strong,.7,15.,225.,True)['allowed'];checks.append(strong+'_no_reborrow')
        assert admission(repaired,strong,strong,.2,15.,225.,True)['allowed'];checks.append(strong+'_productive_add_allowed')
        assert admission(repaired,strong,strong,.7,15.,225.,False)['allowed'];checks.append(strong+'_repair_only_control')
        assert not admission(repaired,strong,weak,.99,15.,225.,True)['allowed'];checks.append(strong+'_reserve_not_destroyed')
        profitable_pending=with_plan(repaired,[{'kind':'NEW','key':'w','side':weak,'qty':15.,'price':.2}])
        assert geometry(profitable_pending,strong)['worst_margin']==geometry(repaired,strong)['worst_margin'];checks.append(strong+'_unfilled_repair_no_credit')
    return {'status':'PASS','checks':len(checks),'names':checks,'direction_writer':False,'future_data':False,'fixed_time_trigger':False}

if __name__=='__main__':
    import json
    print(json.dumps(self_test()))
