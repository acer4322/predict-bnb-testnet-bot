"""V46: local opening-factor probes and two fixed-ticket amplitude replays."""
import argparse
import ast
from copy import deepcopy
import inspect
import json
import math
import shutil
import sys
from types import SimpleNamespace

from prepare_btc5m_transfer_structural_v1 import ROOT,R,read,sha,once,load,compiled
from verify_btc5m_transfer_components_v1 import same,swap

STEM='BTC5M_OPENING_STRENGTH_ISOLATION_V1_20260914'
PARENT=ROOT/'.lan_worker_v1/frozen_new_market_v44_20260913_v1'
PACKAGE=ROOT/'.lan_worker_v1/opening_strength_isolation_2127218_20260914_v1'
V45='BTC5M_FROZEN_NEW_MARKET_PANEL_V1_20260913'
MARKET=2127218


def dump(tag,x):
    (R/(STEM+'_'+tag+'.json')).write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def jobs():return read(R/(STEM+'_WAVE.json'))['jobs']


def worker():
    import run_btc5m_transfer_structural_worker_v1 as w
    w.STEM=STEM;w.PACKAGE=PACKAGE;w.jobs=jobs;w.dump=dump
    return w


def controller(path,roles,reference=None):
    tree=ast.parse((path/'money_runner.py').read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ExposureIntent')
    ns=dict(roles=roles,MODES=('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'),REFERENCE_AMPLITUDE=reference)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'PURE_EXPOSURE_INTENT_ONLY','exec'),ns)
    return ns['ExposureIntent']


def prepare():
    assert not PACKAGE.exists() and not (R/(STEM+'_PROTOCOL.json')).exists()
    parent=read(PARENT/'manifest.json');assert sha(PARENT/'manifest.json')=='7085735ed3039daacb491672b047670c6e645f121124433e6e74b1769e2eb6be'
    assert all(sha(PARENT/k)==h for k,h in parent['files'].items())
    reference=math.tanh(abs(parent['theta'][3])*15./16.)
    assert read(R/(V45+'_PROGRESS.json'))['status']=='COMPLETE'
    dump('PROTOCOL',dict(status='FROZEN_BEFORE_COMPONENT_AND_NATIVE',parent_manifest_sha256=sha(PARENT/'manifest.json'),
        market=MARKET,maximum_native_jobs=2,max_threads=4,
        hypothesis='Separate first observed OWN fill amount from held amplitude. Compare each changed arm with its existing V45 same-mode path, then compare the two changed arms at a common amplitude. Opening direction/quote path is still not neutral.',
        intervention='At the original first nonzero confirmed OWN net trigger, hold tanh(abs(frozen theta[3])*15/(1+15)). Pre-trigger amplitude, direction selection, repair, growth, tail cutoff and all execution constraints unchanged.',
        reference_amplitude=reference,reference_ticket=15.,reference_formula='tanh(abs(theta[3])*15/16)',
        reference_choice='Derived from existing Passive15, chosen without a payoff search; neither V45 arm amplitude is selected as the preferred parameter.',
        arms=['ORACLE_DOWN fixed reference','NO_DIRECTION fixed reference; existing UP bootstrap remains'],
        local_probes=['Recorded first-birth algebra and common-amplitude raw demand on three unique V44 paths','Split-versus-batched first observation with identical final inventory','Side-mirrored fixed-amplitude controller and existing capacity gates','Flat-state neutral-role capacity falsifier at legal prices'],
        local_warning='Counterfactual desired values on fixed recorded states are not simulated fills, realizable savings or whole-world forecasts.',
        selection='Use already consumed V45 2127218 because its two starting amplitudes differ. Development diagnosis, no new holdout or independent generalization claim.',
        dedup='V31 held the first OWN amplitude but did not replace first-packet magnitude by one common fixed-ticket reference. V33/V39/V45 retained UP bootstrap. Existing side-role component tests covered selected-side symmetry, not physical neutral opening symmetry. No located fixed-ticket amplitude protocol in current handoffs/prereg/tools.',
        inherited=['Passive15 and NEW>=1','Variable Active','Cash gates OFF, cash caps null','V44 last-third directional NEW stop','Exact work dust, five-Active and two-renewed ceilings','Canonical pending/UNKNOWN lifecycle','Native binary and theta/qref'],
        stop='Stop on component, native or accounting failure. Do not duplicate V45, refit or replace weak economic results. Target path stays offline; known DOWN is final observed Target net only.',
        existing_solution='Reuse frozen runner, Roles, ExposureIntent, capacity functions, LAN dispatcher and full auditors; no new simulator or package dependency.',
        model_fits=0,parameter_search=0,local_native_jobs=0))
    PACKAGE.mkdir()
    for name in parent['files']:
        dst=PACKAGE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PARENT/name,dst)
    p=PACKAGE/'money_runner.py';s=p.read_text(encoding='utf-8')
    s=once(s,"MODES = ('ORACLE_UP', 'ORACLE_DOWN', 'NO_DIRECTION')", "MODES = ('ORACLE_UP', 'ORACLE_DOWN', 'NO_DIRECTION')\nREFERENCE_AMPLITUDE = "+repr(reference))
    s=once(s,'self.held_amplitude = abs(legacy)','self.held_amplitude = REFERENCE_AMPLITUDE')
    s=once(s,"provenance='FIRST_CONFIRMED_OWN_NET'","provenance='FIRST_CONFIRMED_OWN_NET_FIXED15_REFERENCE'")
    s=once(s,"amplitude_mode='FIRST_CONFIRMED_OWN_HELD'","amplitude_mode='FIRST_OWN_TRIGGER_FIXED15_REFERENCE', reference_amplitude=REFERENCE_AMPLITUDE")
    p.write_text(s,encoding='utf-8')
    m=deepcopy(parent);m.update(version=STEM,parent_manifest_sha256=sha(PARENT/'manifest.json'),market=MARKET,
        maximum_native_jobs=2,reference_amplitude=reference,reference_ticket=15.,
        amplitude_intervention='First confirmed OWN trigger unchanged; amplitude from fixed15 reference, not actual packet quantity.')
    m['files']={k:sha(PACKAGE/k) for k in parent['files']}
    assert [k for k,h in parent['files'].items() if m['files'][k]!=h]==['money_runner.py']
    (PACKAGE/'manifest.json').write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    wave=[]
    for mode,arm in [('ORACLE_DOWN','known_down'),('NO_DIRECTION','no_direction')]:
        job=f'fixed15-core-loop-{MARKET}-opening-reference-{arm.replace("_","-")}-20260914-v1'
        wave.append(dict(job_id=job,market=MARKET,arm=arm,mode=mode,version='V44',cwd='.',max_threads=4,
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--market-id',str(MARKET),'--mode',mode,'--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE']))
    dump('WAVE',dict(jobs=wave,sequential=True))
    out=components(reference)
    for mode in ('ORACLE_UP','ORACLE_DOWN','NO_DIRECTION'):compile(compiled(PACKAGE,mode),'V46_COMPILE_ONLY','exec')
    out.update(status='PASS',manifest_sha256=sha(PACKAGE/'manifest.json'),files=len(m['files']),compile_modes=3,changed_files=['money_runner.py'])
    dump('COMPONENT',out);dump('PROGRESS',dict(status='PREPARED_NOT_SUBMITTED',submissions=0,pending_jobs=[j['job_id'] for j in wave]))
    return {k:out[k] for k in ('status','reference_amplitude','pure_sequence_checks','flat_capacity','files')}


def components(reference):
    sys.path.insert(0,str(PACKAGE));from roles_runtime import roles
    Old=controller(PARENT,roles);New=controller(PACKAGE,roles,reference)
    gate=load('opening_parallel_capacity',PACKAGE/'money_gate.py')
    theta=read(PACKAGE/'manifest.json')['theta'];checks=0;examples=[]
    amounts=(.01,.1,.53,1.,1.84,5.,15.,30.,45.,75.,150.)
    for side in ('UP','DOWN'):
        other='DOWN' if side=='UP' else 'UP'
        for qty in amounts:
            for mode in ('KNOWN_FINAL_DIRECTION','NO_DIRECTION'):
                roles.configure(mode,side if mode=='KNOWN_FINAL_DIRECTION' else None)
                old=Old('ORACLE_'+side if mode=='KNOWN_FINAL_DIRECTION' else 'NO_DIRECTION')
                new=New(old.mode)
                sequence=[dict(UP=0.,DOWN=0.),{side:qty,other:0.},{side:qty,other:2*qty},dict(UP=0.,DOWN=0.)]
                for i,inv in enumerate(sequence):
                    f=dict(t=i,index=i,own_view=dict(inv=inv),pending_qty=dict(UP=150.,DOWN=150.))
                    before=deepcopy(f);roles.observe(f)
                    net=(inv[roles.strong]-inv[roles.weak])/(1+sum(inv.values()));legacy=math.tanh(theta[3]*net)
                    a=old.apply(legacy,net,f);b=new.apply(legacy,net,f);same(f,before)
                    same(b,0. if i==0 else reference)
                    same(a,0. if i==0 else math.tanh(abs(theta[3])*qty/(1+qty)))
                    if i==0:assert roles.side==(side if mode=='KNOWN_FINAL_DIRECTION' else None)
                    else:assert roles.side==side
                    for gross in (15.,100.,896.441004,6327.573151):
                        physical={side:gross*(1+b)/2,other:gross*(1-b)/2}
                        mirror={other:gross*(1+b)/2,side:gross*(1-b)/2}
                        same(swap(physical),mirror);checks+=1
                if mode=='NO_DIRECTION':examples.append(dict(side=side,first_qty=qty,legacy_held=old.held_amplitude,fixed_held=new.held_amplitude))
    # Same final 45 shares, different first observed receipt batch sizes.
    packet=[]
    for first in (1.84,45.):
        roles.configure('NO_DIRECTION',None);a=Old('NO_DIRECTION');b=New('NO_DIRECTION')
        for i,q in enumerate((0.,first,45.)):
            f=dict(t=i,index=i,own_view=dict(inv=dict(UP=q,DOWN=0.)));roles.observe(f)
            legacy=math.tanh(theta[3]*q/(1+q));a.apply(legacy,q/(1+q),f);b.apply(legacy,q/(1+q),f)
        packet.append(dict(first_observed_qty=first,final_qty=45.,legacy_held=a.held_amplitude,fixed_held=b.held_amplitude))
    assert packet[0]['legacy_held']!=packet[1]['legacy_held'] and packet[0]['fixed_held']==packet[1]['fixed_held']
    flat=dict(inv=dict(UP=0.,DOWN=0.),payoff=dict(UP=0.,DOWN=0.),pending_qty=dict(UP=0.,DOWN=0.),pending_cash=dict(UP=0.,DOWN=0.))
    caps=[]
    for price in (.07,.5,.9):
        roles.configure('NO_DIRECTION',None)
        row={s:gate.capacity(flat,s,price,15.,'PARALLEL_QUANTITY')[0] for s in ('UP','DOWN')}
        assert row==dict(UP=15.,DOWN=0.)
        roles.configure('KNOWN_FINAL_DIRECTION','DOWN')
        mirrored={s:gate.capacity(flat,s,price,15.,'PARALLEL_QUANTITY')[0] for s in ('UP','DOWN')}
        same(swap(row),mirrored);caps.append(dict(price=price,no_direction=row,known_down=mirrored))
    panel=read(R/(V45+'_RESULT.json'));observed=[]
    for result in panel['results']:
        if result['arm']!='v44_known' and not (result['market']==MARKET and result['arm']=='v44_no_direction'):continue
        folder=R/'lan_worker_returns'/result['job_id'];tr=read(folder/'clock_trace.json.gz');n=read(folder/'result.json')
        first=n['clock_smoke']['amplitude_birth'];a=first['amplitude'];inv=first['inv']
        same(a,abs(math.tanh(theta[3]*(inv['UP']-inv['DOWN'])/(1+sum(inv.values())))))
        fixed_on_frozen_states=[]
        for intent,growth,obs in zip(tr['intent'],tr['addition_growth_rows'],tr['observations']):
            if intent['t']<first['t']:continue
            strong=result['selected_direction'];weak='DOWN' if strong=='UP' else 'UP';gross=obs['gross']
            desired={strong:gross*(1+reference)/2,weak:gross*(1-reference)/2}
            fixed_on_frozen_states.append(dict(t=intent['t'],index=intent['index'],gross=gross,old=growth['original_desired'],fixed_raw_desired=desired))
        observed.append(dict(market=result['market'],arm=result['arm'],first=first,old_ratio=(1+a)/(1-a),fixed_ratio=(1+reference)/(1-reference),
            frozen_state_rows=len(fixed_on_frozen_states),counterfactual_rows=fixed_on_frozen_states,
            first_money_gate_rows=tr['money_rows'][:4],trace_sha256=sha(folder/'clock_trace.json.gz')))
    dump('LOCAL_PROBES',dict(status='PASS',reference_amplitude=reference,pure_sequence_checks=checks,packet_sensitivity=packet,quantity_examples=examples,
        flat_capacity=caps,observed=observed,neutral_bootstrap_status='NOT_SYMMETRIC: no direction maps UP to unrestricted addition and DOWN to quantity-limited repair',
        raw_target_only=True,model_fits=0,parameter_search=0,native_jobs=0))
    return dict(reference_amplitude=reference,pure_sequence_checks=checks,flat_capacity=caps,unique_recorded_paths=len(observed),packet_dependence_removed_in_component=True,neutral_bootstrap_fixed=False)


def submit(index):
    assert not (R/(STEM+'_SUPERSEDED.json')).exists(),'User changed the next experiment; retain this unsubmitted package'
    w=worker();j=jobs()[index];w.old.identity();w.idle()
    assert read(R/(STEM+'_PREFLIGHT.json'))['status']=='PASS'
    assert not w.artifact(j,'SUBMIT').exists(),'Use status for an existing attempt'
    if index:
        assert read(w.artifact(jobs()[index-1],'AUDIT'))['execution_status']=='PASS'
        assert read(w.artifact(jobs()[index-1],'MECHANISM_AUDIT'))['status']=='PASS'
    assert w.dispatch.cmd_status(w.HOST,j['job_id'])['state']=='missing'
    w.save(j,'SUBMIT',dict(status='ATTEMPT_IN_PROGRESS',attempts=1,job_id=j['job_id']))
    out=w.dispatch.cmd_submit(w.HOST,j['argv'],'.',j['job_id'],4,6,90,auto_collect=False);w.save(j,'SUBMIT',out)
    dump('PROGRESS',dict(status='NATIVE_IN_PROGRESS',submissions=index+1,current_job=j['job_id']))
    return out


def audit(index):
    w=worker();job=jobs()[index];reference=read(PACKAGE/'manifest.json')['reference_amplitude']
    import verify_btc5m_transfer_structural_v1 as v
    v.STEM=STEM;v.PACKAGE=PACKAGE;v.jobs=jobs;v.artifact=w.artifact;v.save=w.save
    tr=read(R/'lan_worker_returns'/job['job_id']/'clock_trace.json.gz')
    code=once(inspect.getsource(v.inspect_job),'len(tr[\'coordination_submissions\'])<=2','len(tr[\'coordination_submissions\'])<=5')
    code=once(code,"same(effective,obs['desired'])","hold=hold_by_index[row['index']];same(effective,hold['input_desired']);effective=hold['effective_desired'];same(effective,obs['desired'])")
    code=once(code,"            else:same(op['price'],ask)","            elif op.get('role')=='ACTIVE_RENEWED_FINITE_CONTINUATION':assert op['price']>=ask-1e-8\n            else:same(op['price'],ask)")
    code=once(code,"if held is None and abs(inv['UP']-inv['DOWN'])>1e-8:held=abs(legacy)","if held is None and abs(inv['UP']-inv['DOWN'])>1e-8:held=reference_amplitude")
    ns=dict(v.__dict__,reference_amplitude=reference,hold_by_index={r['index']:r for r in tr['growth_hold_rows']});exec(compile(code,'V46_FIXED_REFERENCE_STRUCTURAL_AUDIT','exec'),ns)
    out=ns['inspect_job'](index)
    if out['execution_status']!='PASS':return out
    import verify_btc5m_frozen_panel_mechanisms_v1 as mech
    mech.d=SimpleNamespace(jobs=jobs,PACKAGES={'V44':PACKAGE},worker=lambda *args:worker(),read=read,R=R,STEM=STEM,once=once,sha=sha)
    mech.audit(index)
    from roles_runtime import roles
    roles.configure('NO_DIRECTION' if job['mode']=='NO_DIRECTION' else 'KNOWN_FINAL_DIRECTION',None if job['mode']=='NO_DIRECTION' else 'DOWN')
    c=controller(PACKAGE,roles,reference)(job['mode']);intent={r['index']:r for r in tr['intent']}
    for r in tr['direction_rows']:
        f=dict(t=r['t'],index=r['index'],own_view=dict(inv=r['inv']));roles.observe(f)
        if r['index'] in intent:
            row=intent[r['index']];value=c.apply(row['legacy_exposure'],0.,f);same(value,row['applied_exposure'])
    same(c.amplitude_rows,tr['exposure_amplitude_rows'])
    n=read(R/'lan_worker_returns'/job['job_id']/'result.json')
    same(c.amplitude_birth,n['clock_smoke']['amplitude_birth']);same(c.held_amplitude,reference)
    w.save(job,'AMPLITUDE_AUDIT',dict(status='PASS',rows=len(c.amplitude_rows),held=c.held_amplitude,birth=c.amplitude_birth,reference_formula='tanh(abs(theta[3])*15/16)'))
    return out


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=('prepare','preflight','submit','status','collect','audit'));ap.add_argument('--index',type=int,default=0);a=ap.parse_args()
    if a.action=='prepare':out=prepare()
    elif a.action=='preflight':out=worker().preflight()
    elif a.action in ('submit','audit'):out=globals()[a.action](a.index)
    elif a.action=='collect':out=worker().collect(a.index)
    else:
        w=worker();out=w.dispatch.cmd_status(w.HOST,jobs()[a.index]['job_id'])
    print(json.dumps(out,ensure_ascii=False,allow_nan=False))
