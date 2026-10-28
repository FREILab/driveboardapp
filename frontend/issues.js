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

function issues_rows_html(issues) {
  var kinds = { changed: 1, replaced: 1, dropped: 1 };
  return issues
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
  function decide(callback) {
    return function () {
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
