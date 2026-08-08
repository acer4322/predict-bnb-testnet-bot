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

test("root layout mounts Poly signed quote canary", () => {
  const layout = read("layout.tsx");
  assert.match(layout, /PolyQuoteCanaryPanel/);
  assert.match(layout, /<PolyQuoteCanaryPanel\s*\/>/);
});

test("canary panel explains signed quote without place order", () => {
  const panel = read("poly-quote-canary-panel.tsx");
  assert.match(panel, /SIGNED GET-QUOTE CANARY/);
  assert.match(panel, /NO PLACE ORDER/);
  assert.match(panel, /place-order-bundle/);
  assert.match(panel, /PASS_SIMULATED_PLACE/);
  assert.match(panel, /quote_rtt_ms/);
  assert.match(panel, /signal_to_quote_response_ms/);
  assert.match(panel, /quote_coverage_ratio/);
  assert.match(panel, /quote_expiry_headroom_ms/);
  assert.match(panel, /simulated_entry_stake_usdt/);
  assert.match(panel, /configuredEntryStakesUsdt/);
  assert.match(panel, /sizingFetchOnSignalPath/);
  assert.match(panel, /R_POLY_LEAD_ENTRY/);
  assert.match(panel, /R_POLY_LEAD_EXIT/);
  assert.match(panel, /R_POLY_GAP_SCALP/);
});
