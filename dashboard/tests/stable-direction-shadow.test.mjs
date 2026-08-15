import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const pageUrl = new URL("../app/page.tsx", import.meta.url);
const layoutUrl = new URL("../app/layout.tsx", import.meta.url);

test("stable direction strategy is rendered by the native research card list", async () => {
  const page = await readFile(pageUrl, "utf8");
  assert.match(page, /R_MICROPRICE_CONFIRM_STABLE_DIRECTION/);
  assert.match(page, /Microprice Confirm · 穩定方向共識/);
  assert.match(page, /raw Ask 0\.60–<0\.90/);
  assert.match(page, /midpoint delta ≥0\.01/);
});

test("stable direction strategy is not mounted as a layout sidecar", async () => {
  const layout = await readFile(layoutUrl, "utf8");
  assert.doesNotMatch(layout, /StableDirection/);
  assert.doesNotMatch(layout, /STABLE_DIRECTION/);
});
