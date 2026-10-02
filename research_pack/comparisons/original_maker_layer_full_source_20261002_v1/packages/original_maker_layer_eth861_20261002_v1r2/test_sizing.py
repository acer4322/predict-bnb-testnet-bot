"""Pure policy/sizing seam checks. Does not import or execute HFT."""
import argparse,hashlib,importlib.util,json,math,os,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;BASE=P.parent/'v12g_fresh30_generalization_20260927_v45/base'
sys.path.insert(0,str(BASE))
sys.path.insert(0,str(P))
from sizing import TICKET,TICKETS,adapt,policy_theta,rescale_scratch,once,transformed

def load(name,file):
    spec=importlib.util.spec_from_file_location(name,BASE/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def case():
    from governor import choose,required
    from roles_runtime import roles
    roles.configure('NO_DIRECTION',None)
    strong,weak=roles.strong,roles.weak
    checks=[]
    theta=list(range(12));theta[7]=math.log(15.)
    changed=policy_theta(theta)
    assert all(changed[i]==theta[i] for i in range(12) if i!=7)
    assert abs(math.exp(changed[7])-TICKET)<1e-9
    checks.append('only_passive_theta_component_changed')
    state=dict(inv={strong:100.,weak:80.},cost=85.,payoff={strong:15.,weak:-5.},pending_qty={strong:0.,weak:0.},pending_cash={strong:0.,weak:0.},owners=[])
    op=adapt('active_opportunity',load('old_active','active_opportunity.py'))
    row=op.decide(state,{strong:85.,weak:0.},.001,.5,100.,[],.01,0.,lambda *a:[])
    assert row['eligible'] and abs(row['quantity']-10.)<1e-8
    assert op.decide(state,{strong:85.,weak:0.},.5,.6,100.,[],.01,0.,lambda *a:[])['reason']=='FIXED15_PASSIVE_PRICE_STILL_AVAILABLE'
    checks.append('active_quantity_unchanged_passive_availability_consistent')
    demand=adapt('single_repair_demand',load('old_demand','demand_gate.py'))
    capacity_state=dict(inv={strong:100.,weak:40.},payoff={strong:20.,weak:-40.},pending_qty={strong:0.,weak:0.},pending_cash={strong:0.,weak:0.})
    cap=demand.economic_capacity(capacity_state,{strong:100.,weak:40.5},.5,TICKET,.01)
    assert cap['economically_eligible'] and cap['minimum_reference']==TICKET and cap['requested']==TICKET
    checks.append('finite_passive_demand_uses_configured_ticket')
    commitment=adapt('commitment_repair_probe',load('old_commitment','commitment_repair.py'))
    commitment_state=dict(state,pending_qty={strong:30.,weak:0.},pending_cash={strong:27.,weak:0.},owners=[dict(key='u',side=strong,state='SUBMITTED',qty=30.,limit=.9)])
    row=commitment.decide(commitment_state,[],.5,.51,True,[],lambda *a:[])
    assert row['eligible'] and row['quantity']==TICKET
    assert abs(row['conditional_after_ticket'][weak]-row['conditional_payoff'][weak]-TICKET*.5)<1e-8
    assert abs(row['confirmed_if_only_ticket_fills'][strong]-state['payoff'][strong]+TICKET*.5)<1e-8
    expected_price=round(math.ceil((1/TICKET-1e-8)/.01)*.01,10)
    coordination=adapt('reexposure_coordination',load('old_coordination','coordination.py'))
    crow=coordination.decide(dict(state,owners=[],payoff={strong:15.,weak:-20.}),[],expected_price,100.,dict(anchor_floor=0.),True,lambda *a:[])
    assert crow['minimum_passive_price']==expected_price and crow['eligible']
    old_coordination=load('old_coordination_check','coordination.py')
    oldrow=old_coordination.decide(dict(state,owners=[],payoff={strong:15.,weak:-20.}),[],expected_price,100.,dict(anchor_floor=0.),True,lambda *a:[])
    assert crow['quantity']==oldrow['quantity']
    checks.append('reexposure_passive_availability_scaled_active_formula_unchanged')
    quote=commitment.legal_quote(.01,.9)
    assert quote['minimum_legal_price']==expected_price and quote['price']==expected_price
    checks.append('passive_commitment_quantity_cash_and_legal_quote_consistent')
    empty=dict(inv={'UP':0.,'DOWN':0.},cost=0.,pending_qty={'UP':0.,'DOWN':0.},pending_cash={'UP':0.,'DOWN':0.})
    good=choose(empty,'UP',.5,TICKET,'PASSIVE',cap=300.,passive_ticket=TICKET)
    bad=choose(empty,'UP',.01,TICKET,'PASSIVE',cap=300.,passive_ticket=TICKET)
    assert good['quantity']==TICKET and bad['quantity']==0
    assert choose(empty,'UP',.5,TICKET,'PASSIVE',cap=1.,passive_ticket=TICKET)['quantity']==0
    checks.append('budget_and_min_notional_do_not_round_passive_up')
    # Same15-to-new-ticket transforms operate on the verified scratch sources,
    # without creating a worker scratch directory on this host.
    ticket=load('old_ticket','ticket_condition.py')
    tools_root=P/'fixtures'
    for filename,expected_hash in ticket.PINS.items():
        source=(tools_root/filename).read_text(encoding='utf8')
        assert hashlib.sha256((tools_root/filename).read_bytes()).hexdigest()==expected_hash
        if filename=='pair_core_asset_route_sizing_v2.py':
            source=once(source,"'BTC': Decimal('18')","'BTC': Decimal('15')")
            source=once(source,"    if route == 'PASSIVE':\n","    if route == 'PASSIVE':\n        if asset == 'BTC' and q != Decimal('15'):\n            raise ValueError('isolated BTC Passive NEW requires the full15 ticket')\n")
        else:source=once(source,"(18. if asset == 'BTC' else 12.)","(15. if asset == 'BTC' else 12.)")
        updated=rescale_scratch(filename,source,TICKET)
        if filename=='pair_core_asset_route_sizing_v2.py':
            ns={};exec(compile(updated,'pure_sizing_validator','exec'),ns)
            ns['validate_size']('BTC','PASSIVE',.5,TICKET,quantity_step=.01)
            ns['validate_size']('BTC','ACTIVE',.5,.01,quantity_step=.01)
            for q,px in [(TICKET+.01,.5),(TICKET,.01)]:
                try:ns['validate_size']('BTC','PASSIVE',px,q,quantity_step=.01)
                except ValueError:pass
                else:raise AssertionError('invalid passive accepted')
    checks.append('producer_and_gateway_scratch_sizes_match_active_unchanged')
    assert 'hftbacktest' not in sys.modules
    return dict(ticket=TICKET,status='PASS',checks=checks,minimum_legal_price=expected_price,native=0)

def run():
    from test_governor import run as budget_tests
    original=budget_tests();assert original['status']=='PASS'
    rows=[]
    for q in TICKETS:
        env=os.environ.copy();env['V12G_PASSIVE_TICKET']=str(q)
        cp=subprocess.run([sys.executable,str(P/'test_sizing.py'),'--case'],env=env,capture_output=True,text=True,encoding='utf8',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        assert cp.returncode==0,(q,cp.stdout,cp.stderr)
        rows.append(json.loads(cp.stdout))
    return dict(status='PASS',sizes=rows,budget_tests=original,native_paths=0,model_fits=0)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',action='store_true');args=parser.parse_args()
    value=case() if args.case else run()
    if not args.case:(P/'LOCAL_TESTS.json').write_text(json.dumps(value,indent=2),encoding='utf8')
    print(json.dumps(value))
