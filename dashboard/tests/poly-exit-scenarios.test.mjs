import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const panel = fs.readFileSync(path.resolve(here, "../app/poly-execution-canary-panel.tsx"), "utf8");

test("exit panel distinguishes observed depth from optimistic no-depth bound", () => {
  assert.match(panel, /FIRST_LEVEL_DEPTH/);
  assert.match(panel, /UPPER_BOUND_NO_DEPTH/);
  assert.match(panel, /無深度：樂觀上限/);
  assert.match(panel, /完全賣不掉/);
});

test("exit panel exposes legacy conversion and official settlement outcomes", () => {
  assert.match(panel, /sim_legacy_exit_migrated/);
  assert.match(panel, /sim_official_winner/);
  assert.match(panel, /sim_best_final_pnl_usdt/);
  assert.match(panel, /sim_no_sell_final_pnl_usdt/);
});
