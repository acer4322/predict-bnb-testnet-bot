"""Create a new pinned package; never mutate V48--V52 or dispatch native here."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / '.lan_worker_v1/v52_1_public_signed_exposure_smoke_20260914_v2'
PACKAGE = ROOT / '.lan_worker_v1/public_direction_v53_20260914_v1'
STEM = 'BTC5M_PUBLIC_DIRECTION_V53_20260914'
R = ROOT / 'data/research'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def once(s, old, new):
    assert s.count(old) == 1, (old, s.count(old))
    return s.replace(old, new, 1)


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def prepare():
    assert not (PACKAGE/'manifest.json').exists(), 'Frozen package exists; do not rebuild'
    parent = json.loads((PARENT/'manifest.json').read_text())
    assert sha(PARENT/'manifest.json') == 'd9600a0ac19f8e2048deae8b39d04d5f214585713b6fa8a314ab4c49e2a5eed8'
    assert all(sha(PARENT/n) == h for n,h in parent['files'].items())
    PACKAGE.mkdir(exist_ok=True)
    for name in parent['files']:
        dest = PACKAGE/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PARENT/name, dest)
    shutil.copy2(ROOT/'tools/btc5m_public_direction_v53.py', PACKAGE/'public_direction.py')
    p = PACKAGE/'money_runner.py'
    s = p.read_text(encoding='utf-8')
    start, end = s.index('class ExposureIntent:'), s.index('\ndef self_test():')
    s = s[:start] + 'from public_direction import PublicExposureIntent as ExposureIntent\n\n' + s[end:]
    s = once(s, "choices=('LEGACY','INVENTORY','REPAIR_GRACE'), default='LEGACY'", "choices=('PUBLIC_MARKET',), default='PUBLIC_MARKET'")
    s = once(s, "assert args.direction_rule=='LEGACY' or args.mode=='NO_DIRECTION'", "assert args.direction_rule=='PUBLIC_MARKET' and args.mode=='NO_DIRECTION'")
    s = s.replace("amplitude_mode='FIRST_CONFIRMED_OWN_HELD'", "amplitude_mode='CURRENT_PUBLIC_BOOK'")
    s = s.replace("purpose='ORACLE_FIXED_DIRECTION_REPAIR_DIAGNOSTIC'", "purpose='PUBLIC_DIRECTION_UNLATCH_RESEARCH_V53'")
    s = s.replace("numeric_parameters_frozen=True", "numeric_parameters_frozen=True,direction_authority='CURRENT_PUBLIC_BOOK',own_risk_feedback='ADDITION_GROWTH_AND_GROWTH_HOLD'")
    s = s.replace("general_finite_active_count=len(producer.general_finite_active.submissions)", "general_finite_active_count=len(producer.bridge.all_submissions(producer,'general_finite_active'))")
    s = s.replace('Structural consumed-market transfer. Known arm has final Target direction only; no-direction uses first confirmed OWN net. Dynamic arms use dual neutral opening and current confirmed OWN inventory; physical repair memory. No test Target path in actor process.',
                  'Consumed-market engineering test. Current public book sets direction and amplitude each frame after one neutral opening plan; OWN inventory controls downstream risk only. Public proxy is not learned Target belief. No Target/winner input. Tail ADD restriction remains.')
    p.write_text(s, encoding='utf-8')
    p = PACKAGE/'direction_bridge.py'
    s = p.read_text(encoding='utf-8')
    s = once(s, 'from roles_runtime import roles', 'from roles_runtime import roles\nfrom public_direction import observe_public')
    s = once(s, "PARTS = ('demand', 'addition_growth', 'growth_hold', 'opportunity', 'commitment_repair', 'coordination')", "PARTS = ('demand', 'addition_growth', 'growth_hold', 'opportunity', 'commitment_repair', 'coordination', 'general_finite_active')")
    s = once(s, '        selected, self.guard, reason = choose_direction(inv, previous, self.rule, increments, self.guard)',
        "        if self.rule == 'PUBLIC_MARKET':\n            signal = observe_public(frame, producer.theta, previous)\n            selected = signal['side'] if self.decisions else None\n            reason = signal['reason'] if self.decisions else 'DUAL_NEUTRAL_OPENING_ONCE'\n            self.guard = None\n            roles.public_signal = signal\n        else:\n            selected, self.guard, reason = choose_direction(inv, previous, self.rule, increments, self.guard)")
    s = once(s, '        self.decisions.append(row)', "        if self.rule == 'PUBLIC_MARKET':\n            row['public_signal'] = dict(roles.public_signal)\n        self.decisions.append(row)")
    s = once(s, "        category = 'opportunity'", "        if part == 'general_finite_active':\n            if counts['total'] >= 5:\n                return operations\n            return service.apply(frame, producer, operations, validate, crossing)\n        category = 'opportunity'")
    s = once(s, "        mapping = dict(demand_rows=", "        mapping = dict(general_finite_active_rows=('general_finite_active','rows'), general_finite_active_submissions=('general_finite_active','submissions'), demand_rows=")
    s = once(s, "for part in ('opportunity', 'commitment_repair', 'coordination'):", "for part in ('opportunity', 'commitment_repair', 'coordination', 'general_finite_active'):")
    s = once(s, "for part in ('opportunity', 'coordination', 'commitment_repair'):", "for part in ('opportunity', 'coordination', 'commitment_repair', 'general_finite_active'):")
    p.write_text(s, encoding='utf-8')
    p = PACKAGE/'general_finite_active.py'
    s = p.read_text(encoding='utf-8')
    start = s.index('        existing_active = (')
    end = s.index("        if row['eligible'] and existing_active", start)
    s = s[:start] + "        existing_active = producer.bridge.counts(operations)['total']\n" + s[end:]
    p.write_text(s, encoding='utf-8')
    # Keep parent provenance but replace stale acceptance/authority descriptors.
    m = deepcopy(parent)
    m.update(version=STEM, parent_manifest_sha256=sha(PARENT/'manifest.json'), maximum_native_jobs=5,
        market_count=5, paired_markets=[2020778,2020760,2032652,2021302,2021100],
        hypothesis='Unlatch role authority and amplitude together: current physical public book sets direction before own receipt orientation; confirmed own state only controls downstream growth and repair. Scope GeneralFiniteActive memory by physical bank and enforce a shared global cap.',
        direction_authority='CURRENT_PUBLIC_BOOK', amplitude_reference='CURRENT_PUBLIC_BOOK_NOT_HELD',
        coefficient_tuning=False, model_fits=0, parameter_search=False,
        limitations=['Public mid/depth proxy, not learned Target direction.', 'Five consumed markets; not a generalization or live acceptance test.', 'Historical tail new-ADD cutoff remains.'],
        evaluation='Engineering: direction changes with current book despite old majority; physical desired/NEW/receipts follow; old pending reservations and repair memory persist. Economics: actual-winner and both conditional PnLs separately; no +2/-1 graduation metric.')
    m['files'] = {n:sha(PACKAGE/n) for n in parent['files']}
    m['files']['public_direction.py'] = sha(PACKAGE/'public_direction.py')
    dump(PACKAGE/'manifest.json', m)
    changed = [n for n,h in parent['files'].items() if m['files'][n] != h]
    protocol = dict(status='PINNED_BEFORE_NATIVE', version=STEM, parent_manifest_sha256=sha(PARENT/'manifest.json'),
        manifest_sha256=sha(PACKAGE/'manifest.json'), changed_files=changed+['public_direction.py'],
        hypothesis=m['hypothesis'], acceptance=m['evaluation'], maximum_native_jobs=5, max_threads=4,
        markets=m['paired_markets'], local_native_jobs=0, model_fits=0, parameter_sweep=0,
        dedup={'V48_V49':'Inventory-majority role plus held positive amplitude self-reinforces the first fill.',
               'V51':'Signed own feedback balances inventory; does not supply independent direction.',
               'V52':'Coefficients alone did not restore executable public terms.',
               'V52_1':'Public terms restored but role still inventory based and amplitude still first-fill held; five completed returns inspected.'},
        unchanged='Passive15/minimum $1, canonical execution and cash-off, public input tapes, qref and theta from V52.1, growth/repair algorithms and tail cutoff. Never reset pending owners on a role switch.',
        risk='New public direction proxy is a hypothesis. Finite Active physical bank integration is necessary when roles can frequently switch; caps stay global.')
    dump(R/(STEM+'_PROTOCOL.json'), protocol)
    jobs=[]
    for mid in m['paired_markets']:
        jobs.append(dict(job_id=f'fixed15-core-loop-{mid}-public-direction-v53-20260914-v1',market=mid,rule='PUBLIC_MARKET',mode='NO_DIRECTION',
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py','--market-id',str(mid),
                  '--mode','NO_DIRECTION','--direction-rule','PUBLIC_MARKET','--money-mode','PARALLEL_PAYOFF_ZERO',
                  '--demand-mode','AUTO_REPAIR','--retention','0','--opportunity-mode','ONE_ACTIVE'],cwd='.',max_threads=4))
    dump(R/(STEM+'_WAVE.json'), dict(jobs=jobs,sequential=True))
    print(json.dumps(dict(status='PREPARED', changed=changed,manifest_sha256=sha(PACKAGE/'manifest.json'))))


if __name__ == '__main__':
    prepare()
