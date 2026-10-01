"""Exact, research-scratch-only transformations; no strategy changes."""
import hashlib
import json
from pathlib import Path

P = Path(__file__).resolve().parent


def once(source, old, new):
    assert source.count(old) == 1, (old[:100], source.count(old))
    return source.replace(old, new, 1)


def rust_clock(source):
    start = source.index('    fn goto<const WAIT_NEXT_FEED: bool>(')
    end = source.index('\nimpl<MD> Bot<MD> for Backtest<MD>', start)
    section = source[start:end]
    section = once(section, '        let mut timestamp = timestamp;',
                   '        let mut timestamp = timestamp;\n        let mut last_processed_ts = self.cur_ts;')
    section = once(section, '                    }\n                }\n                None => {\n                    return Ok(ElapseResult::EndOfData);',
                   '                    }\n                    last_processed_ts = last_processed_ts.max(ev.timestamp);\n                }\n                None => {\n                    // EOF can follow successful event processing; publish only its actual time.\n                    self.cur_ts = last_processed_ts;\n                    return Ok(ElapseResult::EndOfData);')
    return source[:start] + section + source[end:]


def python_clock(name, source):
    if name == 'hft244_research_owner_accounting_v1.py':
        a = source.index('def strict_advance_to(bt,target_ms):')
        b = source.index('\n\nclass PreviewReceiptAccess', a)
        source = source[:a] + 'from eof_runtime import advance_to as strict_advance_to\n' + source[b:]
        source = once(source, "    assert not self._receipt_invalid, 'invalid receipt integration state'",
                      "    assert not self._receipt_invalid, 'invalid receipt integration state'\n    from eof_runtime import check_process_time\n    check_process_time(self.bt,t)")
    elif name == 'minimal_student_native_system_plan_v1.py':
        source = once(source, '        def run_whole(self,base):\n',
                      '        def run_whole(self,base):\n            from eof_runtime import require_advance\n')
        source = once(source, "            base.ex.advance_to(self.bt,int(self.meta['firstReceivedMs']))",
                      "            require_advance(base.ex,self.bt,int(self.meta['firstReceivedMs']),'initial',None)")
        source = once(source, 't=int(u[1]);base.ex.advance_to(self.bt,t);self.process(t)',
                      "t=int(u[1]);require_advance(base.ex,self.bt,t,'source',i);self.process(t)")
        source = once(source, "end2=int(self.meta['lastReceivedMs']);base.ex.advance_to(self.bt,end2);self.process(end2)",
                      "end2=int(self.meta['lastReceivedMs']);require_advance(base.ex,self.bt,end2,'end2',None);self.process(end2)")
    elif name == 'open_funding_recovery_runtime_v3.py':
        source = once(source, '        def drain_queued_responses(self,base):\n',
                      '        def drain_queued_responses(self,base):\n            from eof_runtime import observe_drain\n')
        source = once(source, "observed=max(last_observed,(upper+999999)//1000000 if rc==1 else (actual+999999)//1000000)",
                      'observed=observe_drain(rc,cur,actual,upper)')
        source = once(source, "clock_kind='UPPER_BOUND_AFTER_EOF' if rc==1 else 'NATIVE_RESPONSE_TIME'",
                      "clock_kind='NATIVE_EVENT_TIME_AT_EOF' if rc==1 else 'NATIVE_RESPONSE_TIME'")
    else:
        raise ValueError(name)
    compile(source, name, 'exec')
    return source


def patch_scratch(root):
    root = Path(root).resolve()
    expected = json.loads((P / 'scratch_expected.json').read_text())
    assert root.parent == Path('C:/BTC5M-worker/.tmp').resolve()
    assert root.name.startswith('target_core_cycle_active_v8_c100_')
    rows = {}
    for name, old_sha in expected.items():
        path = root / 'tools' / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == old_sha, name
        patched = python_clock(name, path.read_text(encoding='utf-8'))
        path.write_text(patched, encoding='utf-8', newline='\n')
        rows[name] = dict(before=old_sha, after=hashlib.sha256(path.read_bytes()).hexdigest())
    (root / 'EOF_SCRATCH_PATCH.json').write_text(json.dumps(rows, indent=2))


def instrument_source(source, candidate):
    source = once(source, "BACKEND=Path(r'C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')",
                  'BACKEND=Path(' + repr(candidate['backend']) + ')')
    source = once(source, "NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'",
                  'NATIVE_SHA=' + repr(candidate['sha256']))
    assert "if root.exists():raise RuntimeError('CLOCK_SMOKE_WORKDIR_ALREADY_EXISTS')" in source
    source = once(source, "  sys.path.insert(0,str(root));os.chdir(root);binary=BACKEND/",
                  "  sys.path.insert(0,str(root));os.chdir(root);from patches import patch_scratch;patch_scratch(root);binary=BACKEND/")
    source = once(source, '  if sim is not None:\n   try:sim.close()',
                  '  from eof_runtime import finish\n  finish(out,sim,BACKEND,NATIVE_SHA)\n  if sim is not None:\n   try:sim.close()')
    return source
