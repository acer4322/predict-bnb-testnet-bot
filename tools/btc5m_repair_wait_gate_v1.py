"""Research-only admission intervention for one preselected old responsibility."""
import json

MODES = ('NO_GATE', 'WAIT_FIRST_PAYMENT', 'EXISTING_ONLY')


class RepairWaitGate:
    def __init__(self, selection, mode):
        assert mode in MODES
        self.selection = selection; self.mode = mode; self.started = False
        self.waiting = False; self.initial = None; self.events = []; self.blocked = []

    def on_frame(self, f, producer):
        if f['t'] < self.selection['t']: return
        # The accounting lot belongs to the shared prefix; later born IDs do not substitute for it.
        old = self.selection['old_id']
        lot = next((r for q in producer.atomic.q.values() for r in q if r['id']==old), None)
        remaining = float(lot['remaining']) if lot else 0.
        if not self.started:
            assert f['t'] == self.selection['t'] and lot is not None
            assert abs(remaining-self.selection['initial_remaining']) < 1e-7
            self.started=True; self.initial=remaining; self.waiting=self.mode!='NO_GATE'
            self.events.append(dict(event='START',t=int(f['t']),remaining=remaining,
                quantity_step=float(f['world_profile']['quantity_step']),
                inventory=dict(f['own_view']['inv']),atomic_queues=json.loads(json.dumps(producer.atomic.q))))
        release = remaining < self.initial-1e-9 if self.mode=='WAIT_FIRST_PAYMENT' else remaining<=1e-9
        if self.waiting and release:
            self.waiting=False
            self.events.append(dict(event='RELEASE',t=int(f['t']),remaining=remaining,
                reason='OLD_RESPONSIBILITY_PAYMENT' if self.mode=='WAIT_FIRST_PAYMENT' else 'OLD_RESPONSIBILITY_COMPLETE'))

    def block(self, f, side, site):
        if self.waiting and side==self.selection['repair_side']:
            self.blocked.append(dict(t=int(f['t']),side=side,site=site))
            return True
        return False


def instrument(source, replace):
    source=replace(source,'self.intent=_ExposureIntent(_MODE)',
                   'self.intent=_ExposureIntent(_MODE);self.wait_gate=_WaitGate(_WAIT_SELECTION,_WAIT_MODE)')
    marker="    if f['t']>=f['end']:\n"
    source=replace(source,marker,"    self.wait_gate.on_frame(f,self)\n"+marker)
    # Gate every new-order path on this side. Cancels, existing orders and receipt
    # processing remain untouched; do not just gate the capacity-frontier path.
    lines=source.splitlines(); added=0; result=[]
    for line in lines:
        result.append(line)
        if line.strip().startswith(('for ss in sorted(', 'for s in sorted(pid,')):
            var='ss' if line.strip().startswith('for ss ') else 's'
            indent=line[:len(line)-len(line.lstrip())]+' '
            result.append(indent+f"if self.wait_gate.block(f,{var},'NEW_PATH_{added}'):continue")
            added+=1
    assert added==4, added
    return '\n'.join(result)+'\n'


def self_test():
    from types import SimpleNamespace
    select=dict(t=100,old_id=2,initial_remaining=10.,repair_side='DOWN')
    p=SimpleNamespace(atomic=SimpleNamespace(q={'UP':[dict(id=2,remaining=10.)],'DOWN':[]}))
    f=dict(t=100,own_view=dict(inv={'UP':10.,'DOWN':0.}),world_profile=dict(quantity_step=.01))
    for mode in MODES:
        p.atomic.q['UP'][0]['remaining']=10.
        g=RepairWaitGate(select,mode);g.on_frame(f,p)
        assert g.block(f,'DOWN','test')==(mode!='NO_GATE')
        assert not g.block(f,'UP','test')
        # A frame/terminal wake without payment does not release.
        g.on_frame(dict(f,t=101),p);assert g.waiting==(mode!='NO_GATE')
        p.atomic.q['UP'][0]['remaining']=7.
        g.on_frame(dict(f,t=102),p);assert g.waiting==(mode=='EXISTING_ONLY')
        p.atomic.q['UP'][0]['remaining']=0.
        g.on_frame(dict(f,t=103),p);assert not g.waiting
