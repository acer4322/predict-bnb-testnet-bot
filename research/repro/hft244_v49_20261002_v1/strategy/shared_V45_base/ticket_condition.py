"""Pinned repair: install the same15 condition in a disposable job checkout.

The failed V1 intercepted only the producer. Adapter, gateway and exact planner
kept separate imports of the original18 research rule. Patch two verified job
copies before any of these modules import; never edit the shared source tree.
"""
import ast
import hashlib

CONTRACT = 'BTC5M_2026085_PASSIVE_FIXED15_ACTIVE_UNCHANGED_RESEARCH_ONLY_V2'
PINS = {
    'pair_core_asset_route_sizing_v2.py': 'aac866e856748d95c0551f8366576e65e2b30139c8b6ef8806d08f83b7982ef8',
    'minimal_student_quantity_seam_v1.py': 'af01af58c9222e9116f90444d84fbcefef88c8354e56e47f929c45b622c98050',
}


def once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def install(root):
    root = root.resolve()
    assert root.parent.as_posix()=='C:/BTC5M-worker/.tmp' and root.name.startswith('target_core_cycle_active_v8_')
    evidence = {}
    for name, expected in PINS.items():
        path = (root / 'tools' / name).resolve()
        assert path.parent == root / 'tools'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        assert before == expected, name
        source = path.read_text(encoding='utf-8')
        if name == 'pair_core_asset_route_sizing_v2.py':
            source = once(source, "'BTC': Decimal('18')", "'BTC': Decimal('15')")
            source = once(source, "    if route == 'PASSIVE':\n", "    if route == 'PASSIVE':\n        if asset == 'BTC' and q != Decimal('15'):\n            raise ValueError('isolated BTC Passive NEW requires the full15 ticket')\n")
            source = once(source, "CONTRACT_ID = 'PASSIVE_MIN_NOTIONAL1_ETH12_BTC18_ACTIVE_SEPARATE_V2'", 'CONTRACT_ID = ' + repr(CONTRACT))
            source = source.replace('BTC passive: submitted quantity >=18 and quoted notional >=1.',
                                    'Isolated BTC research: NEW quantity exactly15 and quoted notional >=1.')
        else:
            source = once(source, "(18. if asset == 'BTC' else 12.)", "(15. if asset == 'BTC' else 12.)")
        ast.parse(source)
        path.write_text(source, encoding='utf-8')
        evidence[name] = dict(before=before, after=hashlib.sha256(path.read_bytes()).hexdigest())
    return evidence


def instrument(source, replace):
    marker = 'theta=helper.theta_for_mask(initial_parameters(qref),10)'
    source = replace(source, marker, marker + ';theta=list(theta);theta[7]=math.log(15.)')
    marker = "  shutil.copy2(ADAPTER_STAGED,root/'tools/open_funding_native_active_adapter_v1.py')"
    return replace(source, marker, marker + "\n  result['sizing_installation']=_install_ticket_condition(root)")
