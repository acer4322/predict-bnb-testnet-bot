import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = fs.readFileSync(
  path.join(process.cwd(), "app", "poly-live-trajectory-dashboard.tsx"),
  "utf8",
);

test("live trajectory clears stale rollover display when market identity disappears", () => {
  assert.match(source, /if \(!slug \|\| marketId == null\) \{/);
  assert.match(source, /setMarketKey\(null\)/);
  assert.match(source, /setPoints\(\[\]\)/);
});

test("Poly lines require receipt timestamp and healthy continuity", () => {
  assert.match(source, /receivedMs != null/);
  assert.match(source, /ageMs <= MAX_RENDERABLE_POLY_AGE_MS/);
  assert.match(source, /continuity\?\.healthy !== false/);
  assert.match(source, /continuity\?\.gapActive !== true/);
  assert.match(source, /polyUp: polyFresh \?/);
  assert.match(source, /polyDown: polyFresh \?/);
});

test("trajectory visibly exposes receipt freshness", () => {
  assert.match(source, /Poly receipt/);
  assert.match(source, /FRESH/);
  assert.match(source, /NOT FRESH/);
});
