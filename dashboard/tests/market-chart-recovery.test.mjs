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

test("root layout mounts market chart fallback coordinator", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /MarketChartRecovery/);
  assert.match(layout, /<MarketChartRecovery\s*\/>/);
});

test("fallback coordinator checks both synchronized sources every second", () => {
  const source = read("market-chart-recovery.tsx");
  assert.match(source, /\/api\/oracle-cross-market/);
  assert.match(source, /\/api\/realtime/);
  assert.match(source, /Promise\.all/);
  assert.match(source, /setInterval\(\(\) => void refreshHealth\(\), 1000\)/);
  assert.match(source, /POLY_MAX_DISPLAY_AGE_MS = 2500/);
});

test("exactly one market chart painter is visible at a time", () => {
  const source = read("market-chart-recovery.tsx");
  assert.match(source, /canvas\.market-chart/);
  assert.match(source, /canvas\.synchronized-market-chart-overlay/);
  assert.match(source, /base\.style\.visibility = healthy \? "hidden" : "visible"/);
  assert.match(source, /overlay\.style\.visibility = healthy \? "visible" : "hidden"/);
  assert.match(source, /native-market-chart/);
  assert.match(source, /synchronized-overlay/);
});

test("fallback coordinator never draws into either canvas", () => {
  const source = read("market-chart-recovery.tsx");
  assert.doesNotMatch(source, /getContext\s*\(/);
  assert.doesNotMatch(source, /clearRect\s*\(/);
  assert.doesNotMatch(source, /lineTo\s*\(/);
  assert.doesNotMatch(source, /\.stroke\s*\(/);
});
