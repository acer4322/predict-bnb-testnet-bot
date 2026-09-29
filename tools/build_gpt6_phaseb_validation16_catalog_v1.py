"""Chronology-only legal-seam catalog in existing Phase-A validation markets."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile


def mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--model-dir', required=True)
    ap.add_argument('--source-dir', default='..')
    ap.add_argument('--validator', required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    src = Path(a.source_dir)
    e1 = mod('catalog_e1', src/'run_gpt6_e1_v3b_one_shot_native_repair_smoke3.py')
    shadow = mod('catalog_shadow', src/'analyze_gpt6_e1_v3b_quantity_coverage_shadow_v2.py')
    world = mod('catalog_world', src/'train_management_v1_current_v3b_execution_world.py')
    md = Path(a.model_dir)
    report = json.loads((md/'result.json').read_text())
    training = [json.loads(x) for x in (md/'training_rows.jsonl').read_text().splitlines() if x.strip()]
    ordered = sorted({(r['windowEndMs'], r['marketId']) for r in training})
    mids = [m for _, m in ordered[report['trainMarkets']:report['trainMarkets']+16]]
    outdir = Path(os.environ['BTC5M_LAN_RESULT_DIR'])

    class Scan(world.TrainingTraceSim):
        # Observe actual native submit boundaries using the proven training class.
        # This avoids the unnecessary multiple-inheritance/all-receipt scanner.
        _pure_first_pair_candidate = shadow.E1QuantityCoverageShadow._pure_first_pair_candidate
        _coverage_for_repair_side = shadow.E1QuantityCoverageShadow._coverage_for_repair_side

        def __init__(self, tape):
            super().__init__(tape)
            self.catalog_events = []
            self.worstFloor = 0.0

        def process(self, t):
            super().process(t)
            self.worstFloor = min(self.worstFloor, float(self._physical_floor()))

        def _submit_role(self, t, side, role, p, q, proj, source):
            events = []
            if role == 'SATELLITE_EXPAND' and self._end_ms-int(t)>180000:
                for repair_side in ('UP','DOWN'):
                    cov = self._coverage_for_repair_side(repair_side)
                    cand = self._pure_first_pair_candidate(repair_side)
                    if cand is not None and cov['residualUnclaimedQty']+1e-9 >= cand['qty']:
                        events.append({'t':int(t),'baselineSide':side,'baselineCandidate':{'side':side,'price':p,'qty':q},'repairSide':repair_side,'repairCandidate':cand,'repairRole':'ECONOMIC_CORE' if self._core_for_side(repair_side) is None else 'SATELLITE_REPAIR','eligibleReplacement':len(self.slot_key)<self.max_slots,'anyPendingActiveBefore':self.q_pending_active is not None,**cov})
            ok = super()._submit_role(t,side,role,p,q,proj,source)
            if ok:
                self.catalog_events.extend(events)
            return ok

    catalog = {'version':'GPT6_PHASEB_VALIDATION16_LEGAL_CATALOG_V1','selectedMarkets':mids,'rows':[],'coverage':[]}
    reference = {'rows':[]}
    with tempfile.TemporaryDirectory(prefix='phaseb_val16_') as td:
        with zipfile.ZipFile(a.bundle) as z:
            co = {int(r['marketId']):r for r in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:
                z.extract(f'tapes/{mid}.json.xz',td)
        for mid in mids:
            print(json.dumps({'catalogStart':mid}),flush=True)
            sim = Scan(Path(td)/'tapes'/f'{mid}.json.xz')
            try:
                r = sim.run_qty('__UNSCORED__')
                r['worstIntramarketFloor'] = sim.worstFloor
                new = sim.finalize_labels()
                old = [x for x in training if x['marketId']==mid]
                compare_fields = world.STATE+world.ACTION+['role','side','route']+[f'{k}{h}s' for k in ('anyFill','fillQty','repairPayQty','overflowQty','cancelReq','terminal') for h in (3,5)]
                import numpy as np
                assert len(new)==len(old), (mid,'native row count mismatch')
                for nr, prior in zip(new,old):
                    for key in compare_fields:
                        same = np.isclose(nr[key],prior[key],rtol=1e-11,atol=1e-9) if isinstance(nr[key],(float,int)) else nr[key]==prior[key]
                        assert same, (mid,nr['key'],key,'native parity mismatch')
                native = [x for x in training if x['marketId']==mid and x['role']=='SATELLITE_EXPAND']
                es = []
                for ev in sim.catalog_events:
                    if not ev['eligibleReplacement'] or ev['anyPendingActiveBefore']:
                        continue
                    c = ev['baselineCandidate']
                    if not any(x['t']==ev['t'] and x['side']==c['side'] and abs(x['price']-c['price'])<1e-12 and abs(x['qty']-c['qty'])<1e-9 for x in native):
                        continue
                    es.append(ev)
                es.sort(key=lambda x:(x['t'],x['repairSide']))
                if es:
                    ev = es[0]
                    spec = {'t':ev['t'],'favoredSide':ev['baselineSide'],
                            'ctrl':{**ev['baselineCandidate'],'role':'SATELLITE_EXPAND'},
                            'trt':{**ev['repairCandidate'],'role':ev['repairRole']}}
                    catalog['rows'].append({'marketId':mid,'spec':spec,'selectionState':ev})
                    reference['rows'].append({'marketId':mid,'control':{'metrics':e1.metrics(r,co[mid]['winner'],spec['favoredSide'])}})
                catalog['coverage'].append({'marketId':mid,'eligibleClocks':len(es),'selected':bool(es),'ledgerViolations':r['quantityLedgerSummary']['invariantViolations']})
            finally:
                sim.close()
            print(json.dumps({'catalogMarket':mid,'eligible':len(es),'selected':bool(es)}),flush=True)
    (outdir/'catalog.json').write_text(json.dumps(catalog,indent=2))
    (outdir/'native_reference.json').write_text(json.dumps(reference,indent=2))
    if not catalog['rows']:
        (outdir/'result.json').write_text(json.dumps({'status':'NO_LEGAL_SEAMS','catalog':catalog}))
        return
    validator = mod('phaseb_validator',a.validator)
    sys.argv = [a.validator,'--bundle',a.bundle,'--model-dir',a.model_dir,'--reference',str(outdir/'native_reference.json'),'--source-dir',a.source_dir,'--spec-file',str(outdir/'catalog.json'),'--output','AUTO']
    validator.main()


if __name__ == '__main__':
    main()
