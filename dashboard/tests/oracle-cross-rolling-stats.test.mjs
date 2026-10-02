import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "../app");

function read(name) {
  return fs.readFileSync(path.join(app, name), "utf8");
}

test("Poly cards mount rolling 10-market stats", () => {
  const layout = read("layout.tsx");
  const stats = read("oracle-cross-rolling-stats.tsx");

  assert.match(layout, /import OracleCrossRollingStats from "\.\/oracle-cross-rolling-stats"/);
  assert.match(layout, /<OracleCrossRollingStats \/>/);

  for (const strategy of [
    "R_POLY_LEAD_ENTRY",
    "R_POLY_LEAD_EXIT",
    "R_POLY_GAP_SCALP",
  ]) {
    assert.match(stats, new RegExp(strategy));
  }

  assert.match(stats, /近 10 局平均收益/);
  assert.match(stats, /平均每場翻轉次數/);
  assert.match(stats, /rolling10AveragePnlUsdt/);
  assert.match(stats, /rolling10AverageReversals/);
  assert.match(stats, /完成且可評估市場 \{sample\}\/10/);
  assert.match(stats, /CHOP confirmed reversal/);
});
