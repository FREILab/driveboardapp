// Import issues: the dialog shown before a file with problems is queued, and
// the one shown when it cannot be imported at all. Everything in an issue
// (element ids, colours, font names, the file name) comes out of the file, so
// every value goes through issues_escape before it reaches the document.

function issues_escape(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

var ISSUES_MAX_ROWS = 20;

function issues_rows_html(issues) {
  var kinds = { changed: 1, replaced: 1, dropped: 1 };
  // what the laser will not cut first, so the cap never hides it; the sort
  // is stable, so rows keep their order within a kind
  function rank_of(issue) {
    return issue.kind == "dropped" ? 0 : issue.kind == "replaced" ? 1 : 2;
  }
  var ordered = issues
    .map(function (issue, i) {
      return [rank_of(issue), i, issue];
    })
    .sort(function (a, b) {
      return a[0] - b[0] || a[1] - b[1];
    })
    .map(function (t) {
      return t[2];
    });
  var more = ordered.length - ISSUES_MAX_ROWS;
  var rows = ordered
    .slice(0, ISSUES_MAX_ROWS)
    .map(function (issue) {
      var kind = kinds[issue.kind] ? issue.kind : "changed";
      var count = issue.count > 1 ? " ×" + Number(issue.count) : "";
      var where = issue.where
        ? ' <span class="where">' + issues_escape(issue.where) + "</span>"
        : "";
      return (
        "<tr><td>" +
        issues_escape(issue.problem) +
        count +
        where +
        '</td><td><span class="issue-tag issue-' +
        kind +
        '">' +
        issues_escape(issue.result) +
        "</span></td><td>" +
        issues_escape(issue.fix) +
        "</td></tr>"
      );
    })
    .join("");
  if (more > 0) {
    rows += '<tr><td colspan="3">+' + more + " more</td></tr>";
  }
  return rows;
}

function issues_title(name, rest) {
  return "<strong>" + issues_escape(name) + "</strong> – " + rest;
}

function issues_show(name, issues, on_load, on_cancel) {
  var n = issues.length;
  $("#import_issues_title").html(
    issues_title(name, n + (n == 1 ? " problem" : " problems")),
  );
  $("#import_issues_rows").html(issues_rows_html(issues));
  var decided = false; // the buttons stay clickable while the modal fades out
  function decide(callback) {
    return function () {
      if (decided) {
        return;
      }
      decided = true;
      $("#import_issues_modal").modal("hide");
      callback();
    };
  }
  $("#import_issues_cancel").off("click").on("click", decide(on_cancel));
  $("#import_issues_load").off("click").on("click", decide(on_load));
  $("#import_issues_modal").modal("show");
}

function issues_show_fatal(name, fatal) {
  $("#import_fatal_title").html(issues_title(name, "cannot import"));
  var where = fatal.where
    ? ' <span class="where">' + issues_escape(fatal.where) + "</span>"
    : "";
  $("#import_fatal_body").html(
    "<p><strong>" +
      issues_escape(fatal.problem) +
      "</strong>" +
      where +
      "</p><p><strong>Fix:</strong> " +
      issues_escape(fatal.fix) +
      "</p>",
  );
  $("#import_fatal_modal").modal("show");
}

function issues_fatal_from(xhr) {
  // the fatal part of a failed /load answer, null for any other failure
  try {
    var body = JSON.parse(xhr.responseText);
    var fatal = body && body.fatal;
    return fatal && typeof fatal === "object" ? fatal : null;
  } catch (e) {
    return null;
  }
}

function issues_hold_from(result) {
  // the held import in a /load answer, null for a clean one; the answer is an
  // object once jQuery has parsed it, but a string must not be trusted to be
  // either a name or json, so a json one is parsed here
  if (typeof result === "string" && result.charAt(0) == "{") {
    try {
      result = JSON.parse(result);
    } catch (e) {
      return null;
    }
  }
  if (result && typeof result === "object" && result.hold && Array.isArray(result.issues)) {
    return result;
  }
  return null;
}
