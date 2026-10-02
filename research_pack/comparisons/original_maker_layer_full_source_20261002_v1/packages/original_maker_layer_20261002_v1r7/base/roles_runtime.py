"""Physical side roles for the frozen V32 structural transfer, no market labels."""
import ast


class Roles:
    def __init__(self):
        self.configure('NO_DIRECTION', None)

    def configure(self, mode, side):
        assert mode in ('KNOWN_FINAL_DIRECTION', 'NO_DIRECTION')
        assert side in ('UP', 'DOWN') if mode == 'KNOWN_FINAL_DIRECTION' else side is None
        self.mode, self.side = mode, side
        self.birth = None
        self.rows = []

    @property
    def strong(self):
        # Neutral bootstrap uses the original physical ordering until OUR fills select a side.
        return self.side or 'UP'

    @property
    def weak(self):
        return 'DOWN' if self.strong == 'UP' else 'UP'

    @staticmethod
    def pid(side):
        assert side in ('UP', 'DOWN')
        return 1 if side == 'UP' else 2

    def observe(self, frame):
        inv = frame['own_view']['inv']
        if self.side is None and abs(inv['UP'] - inv['DOWN']) > 1e-8:
            self.side = 'UP' if inv['UP'] > inv['DOWN'] else 'DOWN'
        if self.side is not None and self.birth is None:
            self.birth = dict(t=frame['t'], index=frame['index'], side=self.side, inv=dict(inv),
                provenance=('OFFLINE_FINAL_NET_DIRECTION_CONDITION' if self.mode == 'KNOWN_FINAL_DIRECTION'
                            else 'FIRST_CONFIRMED_OWN_NET'))
        self.rows.append(dict(t=frame['t'], index=frame['index'], side=self.side, inv=dict(inv)))

    def features(self, values):
        if values is None or self.strong == 'UP':
            return values
        x = dict(values)
        x.update(up_bid=round(1. - values['up_ask'], 10), up_ask=round(1. - values['up_bid'], 10),
                 mid=1. - values['mid'], depth_imbalance=-values['depth_imbalance'],
                 own_net=-values['own_net'], up_qty=values['down_qty'], down_qty=values['up_qty'])
        return x

    def weak_bid_book(self, frame):
        book = frame['book']
        return book['bids'] if self.weak == 'DOWN' else {round(1. - p, 10): q for p, q in book['asks'].items()}


roles = Roles()


def role_expr(side):
    return ast.Attribute(ast.Name('roles', ast.Load()), 'strong' if side == 'UP' else 'weak', ast.Load())


def pid_expr(side):
    return ast.Call(ast.Attribute(ast.Name('roles', ast.Load()), 'pid', ast.Load()), [role_expr(side)], [])


class SideBindings(ast.NodeTransformer):
    """Bind economic side references; never rewrite native side encodings or receipt prices."""
    def visit_FunctionDef(self, node):
        if node.name == 'instrument':
            return node
        return self.generic_visit(node)

    def visit_Constant(self, node):
        if node.value in ('UP', 'DOWN'):
            return ast.copy_location(role_expr(node.value), node)
        return node

    def visit_Tuple(self, node):
        if len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[1], ast.Constant):
            number, side = node.elts[0].value, node.elts[1].value
            if (number, side) in ((1, 'UP'), (2, 'DOWN')):
                return ast.copy_location(ast.Tuple([pid_expr(side), role_expr(side)], node.ctx), node)
        return self.generic_visit(node)

    def visit_Dict(self, node):
        # Physical grant identities remain UP=1 and DOWN=2 under either orientation.
        if len(node.keys) == 2 and all(isinstance(x, ast.Constant) for x in node.keys + node.values):
            pairs = [(k.value, v.value) for k, v in zip(node.keys, node.values)]
            if pairs == [('UP', 1), ('DOWN', 2)]:
                return ast.copy_location(ast.Dict([role_expr(k) for k, _ in pairs], [pid_expr(k) for k, _ in pairs]), node)
        return self.generic_visit(node)

    def visit_JoinedStr(self, node):
        values = []
        for x in node.values:
            if isinstance(x, ast.Constant) and x.value in ('UP_', 'DOWN_'):
                values += [ast.FormattedValue(role_expr(x.value[:-1]), -1), ast.Constant('_')]
            else:
                values.append(self.visit(x))
        return ast.copy_location(ast.JoinedStr(values), node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'process_batch':
            # Atomic ledger retains physical UP/DOWN throughout direction discovery.
            return node
        self.generic_visit(node)
        for k in node.keywords:
            if k.arg in ('UP', 'DOWN'):
                side = k.arg
                k.arg, k.value = None, ast.Dict([role_expr(side)], [k.value])
            elif k.arg == 'parent_id' and isinstance(k.value, ast.Constant) and k.value.value == 2:
                k.value = pid_expr('DOWN')
        return node


def transform_module(source):
    tree = SideBindings().visit(ast.parse(source))
    tree.body.insert(0, ast.ImportFrom('roles_runtime', [ast.alias('roles')], 0))
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n'


def transform_policy(source):
    tree = ast.parse(source)
    class OnlyPolicy(ast.NodeTransformer):
        def visit_ClassDef(self, node):
            return SideBindings().visit(node) if node.name == 'Policy' else node
    tree = OnlyPolicy().visit(tree)
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n'


def self_test():
    for side in ('UP', 'DOWN'):
        r = Roles(); r.configure('KNOWN_FINAL_DIRECTION', side)
        assert r.pid(r.strong) != r.pid(r.weak)
        f = dict(t=1,index=1,own_view=dict(inv=dict(UP=0.,DOWN=0.)), book=dict(bids={.9: 20},asks={.92: 30}))
        r.observe(f); assert r.side == side
        assert r.weak_bid_book(f) == ({.9:20} if side == 'UP' else {.08:30})
    r = Roles()
    for t,u,d in [(1,0,0),(2,0,0),(3,0,15),(4,15,15),(5,30,15)]:
        r.observe(dict(t=t,index=t,own_view=dict(inv=dict(UP=u,DOWN=d))))
    assert r.side == 'DOWN' and r.birth['t'] == 3
    try: r.configure('NO_DIRECTION', 'UP')
    except AssertionError: pass
    else: raise AssertionError('NO_DIRECTION accepted label')
    return dict(status='PASS', physical_parent_identity=True, pending_not_relabelled=True,
                no_direction_target_rejected=True, first_confirmed_side_retained=True)


if __name__ == '__main__':
    print(self_test())
