// Parameter dialog of a parametric library job. Labels come out of an SVG
// anyone may import, so they go through issues_escape.

function template_form_html(params) {
  var html = "";
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    html +=
      '<div class="form-group">' +
      "<label>" + issues_escape(p.label) + "</label>" +
      '<input type="number" class="form-control template_param"' +
      ' data-name="' + issues_escape(p.name) + '"' +
      ' min="' + issues_escape(p.min) + '" max="' + issues_escape(p.max) + '"' +
      ' value="' + issues_escape(p.default) + '" step="any">' +
      '<span class="help-block">' + issues_escape(p.min) + "–" + issues_escape(p.max) + "</span>" +
      "</div>";
  }
  return html;
}

function template_check_values(params, raw) {
  var values = {};
  for (var i = 0; i < params.length; i++) {
    var p = params[i];
    var text = String(raw[p.name] === undefined ? "" : raw[p.name]).trim();
    var v = text === "" ? NaN : Number(text);
    if (!isFinite(v) || v < p.min || v > p.max) {
      // uxmessage inserts its text as html, so the untrusted label is escaped here
      return { error: issues_escape(p.label) + ": use " + issues_escape(p.min) + "–" + issues_escape(p.max) };
    }
    values[p.name] = v;
  }
  return { values: values };
}

function template_open(jobname, template) {
  $("#template_title").text(jobname);
  $("#template_body").html(template_form_html(template.params));
  $("#template_ok_btn")
    .off("click")
    .on("click", function () {
      var raw = {};
      $(".template_param").each(function () {
        raw[$(this).data("name")] = $(this).val();
      });
      var checked = template_check_values(template.params, raw);
      if (checked.error) {
        $().uxmessage("error", checked.error);
        return;
      }
      $("#template_modal").modal("hide");
      import_name = jobname;
      request_post({
        url: "/load",
        data: {
          job: template.svg,
          name: jobname,
          optimize: true,
          progressive: true,
          review_issues: true,
          params: checked.values,
        },
        success: import_load_answer,
        error: import_load_failed,
      });
    });
  $("#template_modal").modal("show");
}
