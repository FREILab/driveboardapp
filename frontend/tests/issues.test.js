"use strict";

// The import issues dialog: what file-controlled text becomes in the markup,
// and how a failed /load is told apart from a crash that is not ours.

const test = require("node:test");
const assert = require("node:assert");
const { loadFrontend } = require("./harness");

function row(over) {
  return Object.assign(
    {
      problem: "Invalid colour #12",
      where: 'rect "frame"',
      result: "Cut in black",
      kind: "changed",
      fix: "Set a valid colour",
      count: 1,
    },
    over,
  );
}

test("file-controlled text is escaped in every cell", () => {
  const app = loadFrontend(["issues.js"]);
  const evil = "<img src=x onerror=alert(1)>";
  const html = app.issues_rows_html([row({ problem: evil, where: evil, result: evil, fix: evil })]);
  assert.ok(html.includes("&lt;img"));
  assert.ok(!html.includes("<img"));
});

test("escape covers the five html characters", () => {
  const app = loadFrontend(["issues.js"]);
  assert.equal(app.issues_escape(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("a repeated problem shows its count, a single one does not", () => {
  const app = loadFrontend(["issues.js"]);
  assert.ok(app.issues_rows_html([row({ count: 2 })]).includes("×2"));
  assert.ok(!app.issues_rows_html([row({ count: 1 })]).includes("×"));
});

test("the result tag carries the kind class", () => {
  const app = loadFrontend(["issues.js"]);
  const html = app.issues_rows_html([row({ kind: "dropped", result: "Not cut" })]);
  assert.ok(html.includes("issue-dropped"));
  assert.ok(html.includes("Not cut"));
});

test("an unknown kind cannot inject a class", () => {
  const app = loadFrontend(["issues.js"]);
  const html = app.issues_rows_html([row({ kind: 'x" onclick="y' })]);
  assert.ok(!html.includes('onclick="y'));
});

test("one row per issue, location only when known", () => {
  const app = loadFrontend(["issues.js"]);
  const html = app.issues_rows_html([row(), row({ where: null })]);
  assert.equal(html.split("<tr>").length - 1, 2);
  assert.equal(html.split('class="where"').length - 1, 1);
});

test("fatal body is picked out of a json answer", () => {
  const app = loadFrontend(["issues.js"]);
  const fatal = app.issues_fatal_from({
    responseText: '{"fatal":{"problem":"P","where":null,"fix":"F"}}',
  });
  assert.equal(fatal.problem, "P");
  assert.equal(fatal.fix, "F");
});

test("anything that is not a fatal answer is null", () => {
  const app = loadFrontend(["issues.js"]);
  assert.equal(app.issues_fatal_from({ responseText: "Invalid file type." }), null);
  assert.equal(app.issues_fatal_from({ responseText: "<html>500</html>" }), null);
  assert.equal(app.issues_fatal_from({ responseText: '{"other":1}' }), null);
  assert.equal(app.issues_fatal_from({ responseText: "null" }), null);
  assert.equal(app.issues_fatal_from({ responseText: '{"fatal":"x"}' }), null);
  assert.equal(app.issues_fatal_from({}), null);
  assert.equal(app.issues_fatal_from(undefined), null);
});

test("the dialog is titled with the name and the row count and wired to the buttons", () => {
  const app = loadFrontend(["issues.js"]);
  const calls = [];
  app.issues_show("a.svg", [row(), row()], () => calls.push("load"), () => calls.push("cancel"));
  assert.equal(app.dom.el("#import_issues_title").html, "<strong>a.svg</strong> – 2 problems");
  assert.deepEqual(app.dom.el("#import_issues_modal").modals.slice(-1), ["show"]);
  app.dom.el("#import_issues_cancel").handlers.click({});
  assert.deepEqual(calls, ["cancel"]);
  app.issues_show("a.svg", [row()], () => calls.push("load"), () => calls.push("cancel"));
  app.dom.el("#import_issues_load").handlers.click({});
  assert.deepEqual(calls, ["cancel", "load"]);
});

test("a single problem is not pluralised", () => {
  const app = loadFrontend(["issues.js"]);
  app.issues_show("a.svg", [row()], () => {}, () => {});
  assert.equal(app.dom.el("#import_issues_title").html, "<strong>a.svg</strong> – 1 problem");
});

test("the fatal dialog shows problem, where and fix", () => {
  const app = loadFrontend(["issues.js"]);
  app.issues_show_fatal("platte.dxf", { problem: "Bad <b>", where: null, fix: "Export again" });
  assert.equal(app.dom.el("#import_fatal_title").html, "<strong>platte.dxf</strong> – cannot import");
  assert.deepEqual(app.dom.el("#import_fatal_modal").modals.slice(-1), ["show"]);
  const body = app.dom.el("#import_fatal_body").html;
  assert.ok(body.includes("Bad &lt;b&gt;"));
  assert.ok(body.includes("Fix:"));
  assert.ok(body.includes("Export again"));
  assert.ok(!body.includes('class="where"'));
});

test("a held import is told from a clean answer, object or json string", () => {
  const app = loadFrontend(["issues.js"]);
  const held = { hold: "abc", name: "a", issues: [row()] };
  assert.equal(app.issues_hold_from(held).hold, "abc");
  assert.equal(app.issues_hold_from(JSON.stringify(held)).hold, "abc");
  assert.equal(app.issues_hold_from("job_name"), null);
  assert.equal(app.issues_hold_from("{broken"), null);
  assert.equal(app.issues_hold_from({ name: "a", pending: "t" }), null);
  assert.equal(app.issues_hold_from(null), null);
});

test("the file name in a title is escaped", () => {
  const app = loadFrontend(["issues.js"]);
  app.issues_show("<i>x</i>", [row()], () => {}, () => {});
  assert.ok(!app.dom.el("#import_issues_title").html.includes("<i>"));
  app.issues_show_fatal("<i>x</i>", { problem: "P", where: null, fix: "F" });
  assert.ok(!app.dom.el("#import_fatal_title").html.includes("<i>"));
});

function click(app, id) {
  app.dom.el(id).handlers.click({});
}

test("only the first click decides, whichever button it is", () => {
  const app = loadFrontend(["issues.js"]);
  const calls = [];
  app.issues_show("a", [row()], () => calls.push("load"), () => calls.push("cancel"));
  click(app, "#import_issues_load");
  click(app, "#import_issues_load");
  click(app, "#import_issues_cancel");
  assert.deepEqual(calls, ["load"]);
});

test("a fresh dialog decides again", () => {
  const app = loadFrontend(["issues.js"]);
  const calls = [];
  app.issues_show("a", [row()], () => calls.push("load1"), () => calls.push("cancel1"));
  click(app, "#import_issues_load");
  app.issues_show("b", [row()], () => calls.push("load2"), () => calls.push("cancel2"));
  click(app, "#import_issues_cancel");
  click(app, "#import_issues_load");
  assert.deepEqual(calls, ["load1", "cancel2"]);
});

function importApp() {
  const app = loadFrontend(["issues.js", "import.js"], { document: {}, queue_update() {} });
  const seen = [];
  app.import_loaded = (r) => seen.push(["loaded", r]);
  app.issues_show = (name, issues) => seen.push(["show", issues.length]);
  app.issues_show_fatal = (name, f) => seen.push(["fatal", f.problem]);
  return { app, seen };
}

test("a load answer goes to the dialog, the queue or the fatal dialog as it should", () => {
  const { app, seen } = importApp();
  app.import_load_answer("clean");
  app.import_load_answer({ hold: "h", name: "a", issues: [row(), row()] });
  app.import_load_failed({ responseText: '{"fatal":{"problem":"P","where":null,"fix":"F"}}' });
  assert.deepEqual(seen, [["loaded", "clean"], ["show", 2], ["fatal", "P"]]);
});

test("a failure that is not a fatal answer stays a toast", () => {
  const { app, seen } = importApp();
  app.import_load_failed({ responseText: "Invalid file type." });
  assert.deepEqual(seen, []);
  assert.equal(app.dom.messages.length, 2);
  assert.equal(app.dom.messages[1].text, "Invalid file type.");
});
