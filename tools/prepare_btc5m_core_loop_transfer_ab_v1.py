"""Freeze four consumed markets and separate public inputs from offline labels."""
import gzip
import json
import shutil
import btc5m_direction_condition_v1 as direction
from prepare_btc5m_addition_growth_v1 import ROOT,R,PACKAGE,STEM,read,sha,dump_for

OUT=ROOT/'.lan_worker_v1/core_loop_transfer_ab_inputs_20260913_v1'
REPORT='BTC5M_CORE_LOOP_TRANSFER_AB_V1_20260913'
SOURCE=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1'
MARKETS=(2023438,2026817,2028352,2029246)


def main():
    assert not OUT.exists(),'Existing frozen input preparation: do not rebuild'
    old=read(SOURCE/'MANIFEST.json');candidate=read(R/(STEM+'_RESULT.json'))
    assert candidate['verification']=='PASS'
    assert tuple(m for m in old['markets'] if m!=2026085)==MARKETS
    # Cohort is every preexisting transfer5 market except the main training case.
    records=[];labels={};publics={}
    for mid in MARKETS:
        name=f'input_{mid}.json.gz';tape=f'tapes/{mid}.json.xz'
        assert sha(SOURCE/name)==old['files'][name]['sha256'] and sha(SOURCE/tape)==old['files'][tape]['sha256']
        s=read(SOURCE/name);market=s['market'];inv=dict(UP=0.,DOWN=0.);cost=0.
        for row in s['targetActions']:
            if row.get('quote_type')=='BID' and row.get('side') in inv and row['shares']>0:
                inv[row['side']]+=row['shares'];cost+=row['shares']*row['price']
        net=inv['UP']-inv['DOWN'];side='UP' if net>1e-8 else 'DOWN' if net<-1e-8 else None
        labels[str(mid)]=dict(side=side,final_inventory=inv,cost=cost,
            source_sha256=sha(SOURCE/name),meaning='Final observed Target BID net side, not settlement winner or proven private intent.')
        public=dict(market={k:market[k] for k in ('market_id','window_start_ms','window_end_ms','quality_status')},
            books=s['books'],public=s['public'],tape=dict(file=tape,sha256=sha(SOURCE/tape)),
            actor_input_contract='Current available public features/book, canonical OWN state and receipts only; tape is simulator input.')
        assert set(public)=={'market','books','public','tape','actor_input_contract'}
        assert all(k not in public for k in ('targetActions','targetParents','winner','oracle_side'))
        publics[mid]=public
        records.append(dict(market_id=mid,books=len(s['books']),public_snapshots=len(s['public']),
            window=[market['window_start_ms'],market['window_end_ms']],quality=market['quality_status'],
            source_sha256=sha(SOURCE/name),tape_sha256=sha(SOURCE/tape)))
    component=direction.self_test();OUT.mkdir();(OUT/'tapes').mkdir()
    for mid,public in publics.items():
        raw=json.dumps(public,separators=(',',':'),allow_nan=False).encode()
        (OUT/f'public_{mid}.json.gz').write_bytes(gzip.compress(raw,mtime=0))
        shutil.copy2(SOURCE/f'tapes/{mid}.json.xz',OUT/'tapes'/f'{mid}.json.xz')
    dump_for(REPORT,'OFFLINE_LABELS',dict(status='OFFLINE_SCORER_ONLY',labels=labels))
    manifest=dict(status='INPUTS_READY_NATIVE_PORT_PENDING',markets=records,
        selection='All four other markets in existing consumed transfer5, source order; not selected by this candidate outcome. No refit or outcome-based replacement.',
        candidate_package=str(PACKAGE.relative_to(ROOT)),candidate_manifest_sha256=sha(PACKAGE/'manifest.json'),
        candidate_result_sha256=sha(R/(STEM+'_RESULT.json')),source_manifest_sha256=sha(SOURCE/'MANIFEST.json'),
        consumption='Already consumed engineering/behavior markets, not unseen holdout. Later held-out evaluation must freeze policy and labels before use.',
        files={p.relative_to(OUT).as_posix():sha(p) for p in OUT.rglob('*') if p.is_file()},
        arms=dict(KNOWN_FINAL_DIRECTION='Supply only the offline final observed Target net side bit at start.',
            NO_DIRECTION='Reject Target label; flat remains directionless until first nonzero confirmed OUR net. Retain this side and use the same amplitude and growth/repair manager.'),
        shared=dict(theta='V32 frozen',passive_ticket=15.,active='Variable; same route rules',repair_cash_gates=False,
            capital_cap=None,clock_denominator=1487.,max_threads=4,initial_state='Same empty OWN state',fitting=0),
        direction_component=component,
        not_yet_native_ready=['Current demand/capacity/opportunity/commitment/coordination/maintenance modules still assume repair side DOWN.',
            'Money runner market argument and private sizing checkout assertion still pin 2026085.',
            'Side-aware routing must cover correct ask/depth, owner side/parent mapping, finite goals and pending reservations, plus unresolved initial direction.',
            'Move test Target scoring out of the actor process and pin existing training-derived theta/reference numerically.',
            'Prove original-UP port parity and mirrored side component legality before submitting four matched pairs.'],
        maximum_paired_native_jobs=8,new_cross_market_native_jobs=0,figures=0,
        evaluation=['All source frames, source/runner hashes, raw/canonical fill/cost closure and terminal reservations',
            'Own two-sided acquisition, recurring repair and reexposure response with pending preserved',
            'Direction agreement reported separately from within-direction repair; activity and frozen sizing shown',
            'Terminal two-branch geometry plus cost-normalized payoff, gain/loss, loss area and prefix-selected recovery',
            'Per-market paired results and aggregate dispersion; no single core score or isolated PnL promotion',
            'Same-side A/B equality is valid if the no-direction causal side matches the oracle; disagreement cases carry the information contrast.'])
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    assert all(sha(OUT/n)==h for n,h in manifest['files'].items())
    dump_for(REPORT,'PROTOCOL',manifest)
    print(json.dumps(dict(status=manifest['status'],markets=list(MARKETS),input_files=len(manifest['files']),
        direction_component=component['status'],native_jobs=0,manifest_sha256=sha(OUT/'MANIFEST.json'))))


if __name__=='__main__':main()
