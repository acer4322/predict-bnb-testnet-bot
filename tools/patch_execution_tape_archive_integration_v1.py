from pathlib import Path
p=Path('src/predict_bot/predict_wallet_maker_book_inference_collector.py')
s=p.read_text(encoding='utf-8')
# import archive helper
needle='from .predict_wallet_shadow_observer_v2 import normalize_match_leg\n'
repl='from .execution_tape_archive_v1 import archive_market_to_xz\nfrom .predict_wallet_shadow_observer_v2 import normalize_match_leg\n'
if repl not in s:
    if needle not in s: raise SystemExit('import needle missing')
    s=s.replace(needle,repl)
# archive dir constant
needle='DB_PATH = Path(os.environ.get("PREDICT_WALLET_MAKER_BOOK_DB", ROOT / "data" / DEFAULT_DB_NAME))\n'
repl=needle+'EXECUTION_TAPE_ARCHIVE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"\n'
if 'EXECUTION_TAPE_ARCHIVE_DIR' not in s:
    if needle not in s: raise SystemExit('db const missing')
    s=s.replace(needle,repl)
# manifest schema after matches index
needle='''                CREATE INDEX IF NOT EXISTS idx_execution_matches_market_time\n                    ON maker_execution_matches_v1(market_id,executed_at_ms);\n                """'''
repl='''                CREATE INDEX IF NOT EXISTS idx_execution_matches_market_time\n                    ON maker_execution_matches_v1(market_id,executed_at_ms);\n                CREATE TABLE IF NOT EXISTS maker_execution_archive_manifest_v1 (\n                    market_id INTEGER PRIMARY KEY,\n                    archive_path TEXT NOT NULL,\n                    archive_bytes INTEGER NOT NULL,\n                    l2_rows INTEGER NOT NULL,\n                    meta_rows INTEGER NOT NULL,\n                    match_rows INTEGER NOT NULL,\n                    archived_at_ms INTEGER NOT NULL,\n                    version TEXT NOT NULL\n                );\n                """'''
if 'CREATE TABLE IF NOT EXISTS maker_execution_archive_manifest_v1' not in s:
    if needle not in s: raise SystemExit('schema integration needle missing')
    s=s.replace(needle,repl)
# archive after raw matches commit
needle='''        if inserts:\n            with self.db_lock:\n                self.db.executemany(\n                    """INSERT OR IGNORE INTO maker_execution_matches_v1(\n                           match_key,market_id,settlement_id,transaction_hash,executed_at,executed_at_ms,\n                           amount_filled,price_executed,raw_json_z,fetched_at_ms\n                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""", inserts)\n                self.db.commit()\n        return len(inserts)'''
repl='''        if inserts:\n            with self.db_lock:\n                self.db.executemany(\n                    """INSERT OR IGNORE INTO maker_execution_matches_v1(\n                           match_key,market_id,settlement_id,transaction_hash,executed_at,executed_at_ms,\n                           amount_filled,price_executed,raw_json_z,fetched_at_ms\n                       ) VALUES (?,?,?,?,?,?,?,?,?,?)""", inserts)\n                self.db.commit()\n        try:\n            archive_market_to_xz(\n                self.db_path, int(market_id), EXECUTION_TAPE_ARCHIVE_DIR, overwrite=True\n            )\n        except Exception as exc:\n            self.last_error = f"execution archive {market_id}: {type(exc).__name__}: {str(exc)[:300]}"\n        return len(inserts)'''
if 'archive_market_to_xz(' not in s[s.find('def _backfill_market_matches_v1'):s.find('def _market_loop',s.find('def _backfill_market_matches_v1'))]:
    if needle not in s: raise SystemExit('backfill integration needle missing')
    s=s.replace(needle,repl)
# retention gate
needle='''                    self.db.execute("DELETE FROM maker_book_inference_updates WHERE received_at_ms<?", (cutoff,))\n                    self.db.execute(\n                        """DELETE FROM maker_book_inference_markets WHERE first_seen_ms<?\n                             AND market_id NOT IN (SELECT DISTINCT market_id FROM maker_book_inference_updates)""",\n                        (cutoff,),\n                    )'''
repl='''                    # Execution L2/meta may be pruned only after a durable per-market archive exists.\n                    self.db.execute(\n                        """DELETE FROM maker_book_inference_updates\n                            WHERE received_at_ms<?\n                              AND market_id IN (SELECT market_id FROM maker_execution_archive_manifest_v1)""",\n                        (cutoff,),\n                    )\n                    self.db.execute(\n                        """DELETE FROM maker_execution_orderbook_meta_v1\n                            WHERE received_at_ms<?\n                              AND market_id IN (SELECT market_id FROM maker_execution_archive_manifest_v1)""",\n                        (cutoff,),\n                    )\n                    self.db.execute(\n                        """DELETE FROM maker_book_inference_markets WHERE first_seen_ms<?\n                             AND market_id NOT IN (SELECT DISTINCT market_id FROM maker_book_inference_updates)\n                             AND market_id IN (SELECT market_id FROM maker_execution_archive_manifest_v1)""",\n                        (cutoff,),\n                    )'''
if 'Execution L2/meta may be pruned only after' not in s:
    if needle not in s: raise SystemExit('retention needle missing')
    s=s.replace(needle,repl)
# snapshot manifest counters
needle='''                       (SELECT COUNT(*) FROM maker_execution_matches_v1) match_rows,\n                       (SELECT COUNT(DISTINCT market_id) FROM maker_execution_matches_v1) match_markets,\n                       (SELECT MAX(executed_at_ms) FROM maker_execution_matches_v1) latest_match_ms\n                """'''
repl='''                       (SELECT COUNT(*) FROM maker_execution_matches_v1) match_rows,\n                       (SELECT COUNT(DISTINCT market_id) FROM maker_execution_matches_v1) match_markets,\n                       (SELECT MAX(executed_at_ms) FROM maker_execution_matches_v1) latest_match_ms,\n                       (SELECT COUNT(*) FROM maker_execution_archive_manifest_v1) archived_markets,\n                       (SELECT COALESCE(SUM(archive_bytes),0) FROM maker_execution_archive_manifest_v1) archive_bytes\n                """'''
if '(SELECT COUNT(*) FROM maker_execution_archive_manifest_v1) archived_markets' not in s:
    if needle not in s: raise SystemExit('snapshot manifest needle missing')
    s=s.replace(needle,repl)
p.write_text(s,encoding='utf-8')
print('patched archive integration')
