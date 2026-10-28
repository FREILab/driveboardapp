"use strict";

// The fill popup: what it lists, and which pxsize it lets through.

const test = require("node:test");
const assert = require("node:assert");
const { loadFrontend } = require("./harness");

function popup(colors, fills, pxsize) {
  const app = loadFrontend(["issues.js", "tools.js"]);
  return app.tools_addfill_html(colors, fills, pxsize);
}

test("added fills are listed with the pxsize each was made with", () => {
  const html = popup([], [{ color: "#c04040", pxsize: 0.2 }, { color: "#4040c0", pxsize: 0.1 }], "0.1");
  assert.ok(html.includes("#c04040 &nbsp; 0.2 mm"));
  assert.ok(html.includes("#4040c0 &nbsp; 0.1 mm"));
});

test("an empty job shows no placeholder text", () => {
  const html = popup([], [], "0.1");
  assert.ok(!html.includes("No job loaded."));
  assert.ok(!html.includes("None yet."));
  assert.ok(!html.includes("addfill_color"));
});

test("each colour button carries its item index, the popup has a close x", () => {
  const html = popup([["#ff0000", 3], ["#0000ff", 7]], [], "0.1");
  assert.ok(html.includes('data-idx="3"'));
  assert.ok(html.includes('data-idx="7"'));
  assert.ok(html.includes('id="addfill_close"'));
  assert.ok(html.includes('id="addfill_selection"'));
});

test("file colours and the typed pxsize are escaped", () => {
  const evil = '"><img src=x onerror=alert(1)>';
  const html = popup([[evil, 0]], [{ color: evil, pxsize: evil }], evil);
  assert.ok(!html.includes("<img"));
});

test("pxsize must be a positive number", () => {
  const app = loadFrontend(["issues.js", "tools.js"]);
  assert.equal(app.tools_addfill_pxsize("0.1"), 0.1);
  for (const bad of ["", "abc", "0", "-1", "Infinity"]) {
    assert.equal(app.tools_addfill_pxsize(bad), null, bad);
  }
});
