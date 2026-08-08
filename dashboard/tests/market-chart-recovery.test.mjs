import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const app = path.resolve(here, "../app");

function read(relative) {
  return fs.readFileSync(path.join(app, relative), "utf8");
}

test("root layout mounts realtime chart recovery", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /MarketChartRecovery/);
  assert.match(layout, /<MarketChartRecovery\s*\/>/);
});

test("chart recovery samples realtime independently from statistics history", () => {
  const source = read("market-chart-recovery.tsx");
  assert.match(source, /8766\$\{path\}/);
  assert.match(source, /\/api\/realtime/);
  assert.match(source, /slice\(-360\)/);
  assert.match(source, /up_ask/);
  assert.match(source, /down_ask/);
  assert.match(source, /waiting-quotes/);
});

test("Poly overlay cannot leave the native chart hidden", () => {
  const source = read("market-chart-recovery.tsx");
  assert.match(source, /MutationObserver/);
  assert.match(source, /attributeFilter: \["style"\]/);
  assert.match(source, /canvas\.style\.visibility === "hidden"/);
  assert.match(source, /canvas\.style\.visibility = "visible"/);
});

test("null quotes remain null rather than becoming zero", () => {
  const source = read("market-chart-recovery.tsx");
  assert.match(source, /value == null \|\| value === ""/);
});
