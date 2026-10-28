"use strict";

// The parameter dialog of a parametric library job: the form it shows and
// which values it lets through to /load.

const test = require("node:test");
const assert = require("node:assert");
const { loadFrontend } = require("./harness");

const app = loadFrontend(["issues.js", "template.js"]);
const P = [{ name: "vmin", default: 1500, min: 100, max: 6000, label: "Vorschub min" }];

test("form shows label, default and range", () => {
  const html = app.template_form_html(P);
  assert.ok(html.includes("Vorschub min") && html.includes('value="1500"'));
  assert.ok(html.includes('min="100"') && html.includes('max="6000"') && html.includes('data-name="vmin"'));
});

test("template_form_html escapes labels", () => {
  const html = app.template_form_html([{ ...P[0], label: '<img src=x onerror="alert(1)">' }]);
  assert.ok(!html.includes("<img"));
});

// objects made inside the vm context have another Object.prototype
const plain = (x) => JSON.parse(JSON.stringify(x));

test("values are numbers within range", () => {
  assert.deepStrictEqual(plain(app.template_check_values(P, { vmin: "1200" })), { values: { vmin: 1200 } });
  assert.deepStrictEqual(plain(app.template_check_values(P, { vmin: "99" })), { error: "Vorschub min: use 100–6000" });
  assert.deepStrictEqual(plain(app.template_check_values(P, { vmin: "" })), { error: "Vorschub min: use 100–6000" });
});
