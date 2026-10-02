"""Research-only passive ticket parameter, applied before imports/submission."""
import ast
import hashlib
import math
import os
from pathlib import Path
from types import ModuleType

TICKETS = (15., 10., 7.5, 5., 3.)
TICKET = float(os.environ.get('V12G_PASSIVE_TICKET', '15'))
assert TICKET in TICKETS

def once(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new, 1)

def policy_theta(theta):
    if TICKET == 15.:
        return theta
    result = list(theta)
    result[7] = math.log(TICKET)
    return result

def money_source(source):
    source = once(source, "_FIXED_THETA=manifest['theta']", "_FIXED_THETA=__import__('sizing').policy_theta(manifest['theta'])")
    source=once(source, '    source=bridge.instrument(source,replace)', "    source=bridge.instrument(source,replace)\n    source=__import__('hard_stop').instrument(source)")
    source=once(source, 'passive_ticket=15.', "passive_ticket=__import__('sizing').TICKET")
    return once(source, '    source=transform_policy(source)', "    source=__import__('passive_offset').instrument(source)\n    source=__import__('w_ladder').instrument(source)\n    source=__import__('demand_scale').instrument(source)\n    source=transform_policy(source)")

SITES = {
    'single_repair_demand': ({'economic_capacity'}, 1),
    'active_opportunity': ({'decide'}, 1),
    'reexposure_coordination': ({'decide'}, 1),
    'commitment_repair_probe': ({'legal_quote','decide','apply'}, 3),
    'commitment_base': ({'decide','apply'}, 8),
}

def transformed(source, name, ticket):
    names, expected = SITES[name]
    count = [0]
    class Numbers(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, (float,int)) and node.value == 15:
                count[0] += 1
                return ast.copy_location(ast.Constant(float(ticket)), node)
            return node
    class RuntimeOnly(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            return Numbers().visit(node) if node.name in names else node
    tree = RuntimeOnly().visit(ast.parse(source))
    assert count[0] == expected, (name, count[0], expected)
    ast.fix_missing_locations(tree)
    return compile(tree, name+'_passive_ticket', 'exec'), count[0]

def clone_sized(original, name):
    replacement = ModuleType(original.__name__+'_v53')
    replacement.__file__ = original.__file__
    code, _ = transformed(Path(original.__file__).read_text(encoding='utf8'), name, TICKET)
    exec(code, replacement.__dict__)
    # Original self-tests retain their original module globals and original15
    # fixtures. New-size behavior is separately verified in test_sizing.py.
    if hasattr(original, 'self_test'):
        replacement.self_test = original.self_test
    if name == 'commitment_repair_probe':
        replacement.base = clone_sized(replacement.base, 'commitment_base')
    return replacement

def rescale_scratch(name, source, ticket):
    value = repr(float(ticket))
    if name == 'pair_core_asset_route_sizing_v2.py':
        source = once(source, "'BTC': Decimal('15')", "'BTC': Decimal("+repr(value)+")")
        source = once(source, "q != Decimal('15')", 'q != Decimal('+repr(value)+')')
        source = source.replace('full15 ticket','configured passive ticket')
        source = source.replace('NEW quantity exactly15', 'NEW quantity exactly'+value)
    elif name == 'minimal_student_quantity_seam_v1.py':
        source = once(source, "(15. if asset == 'BTC' else 12.)", '('+value+" if asset == 'BTC' else 12.)")
    else:
        raise ValueError(name)
    compile(source, name, 'exec')
    return source

def adapt(name, original):
    if TICKET == 15.:
        return original
    if name in SITES and name != 'commitment_base':
        return clone_sized(original, name)
    if name == 'fixed15_condition':
        original_install = original.install
        original.CONTRACT = f'V53_BTC_PASSIVE_FIXED_{TICKET:g}_MIN_NOTIONAL1_ACTIVE_UNCHANGED_RESEARCH_ONLY'
        def install(root):
            proof = original_install(root)
            for filename, row in proof.items():
                path = Path(root)/'tools'/filename
                assert hashlib.sha256(path.read_bytes()).hexdigest() == row['after']
                source = rescale_scratch(filename, path.read_text(encoding='utf8'), TICKET)
                path.write_text(source, encoding='utf8', newline='\n')
                row.update(v52_fixed15_after=row['after'],after=hashlib.sha256(path.read_bytes()).hexdigest(),passive_ticket=TICKET)
            return proof
        original.install = install
        return original
    return original
