from pathlib import Path
p=Path('tools/hftbacktest_r4_p0_provenance_journal_v1.py')
s=p.read_text(encoding='utf-8')
needle='''             "keepProvenanceJournal":keep.get("provenanceJournal"),"reinsertProvenanceJournal":rein.get("provenanceJournal"),\n             "keepProvenanceSummary":keep.get("provenanceSummary"),"reinsertProvenanceSummary":rein.get("provenanceSummary"),\n             "provenanceStressEvents":rein.get("provenanceStressEvents"),\n'''
repl='''             "keepProvenanceJournal":keep.get("provenanceJournal"),"reinsertProvenanceJournal":rein.get("provenanceJournal"),\n             "keepProvenanceSummary":keep.get("provenanceSummary"),"reinsertProvenanceSummary":rein.get("provenanceSummary"),\n             "keepProvenanceResponsibilityState":keep.get("provenanceResponsibilityState"),"reinsertProvenanceResponsibilityState":rein.get("provenanceResponsibilityState"),\n             "keepFillLog":keep.get("fillLog"),"reinsertFillLog":rein.get("fillLog"),\n             "provenanceStressEvents":rein.get("provenanceStressEvents"),\n'''
if needle not in s: raise SystemExit('needle missing')
s=s.replace(needle,repl)
p.write_text(s,encoding='utf-8')
print('patched audit fields')
