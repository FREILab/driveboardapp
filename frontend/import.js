var import_name = "";

///////////////////////////////////////////////////////////////////////////////
///////////////////////////////////////////////////////////////////////////////

$(document).ready(function () {
  // file upload form
  $("#open_file_fld").change(function (e) {
    e.preventDefault();
    $("#open_btn").button("loading");
    var input = $("#open_file_fld").get(0);

    // file API check
    var browser_supports_file_api = true;
    if (typeof window.FileReader !== "function") {
      browser_supports_file_api = false;
    } else if (!input.files) {
      browser_supports_file_api = false;
    }

    // name the job before sending, the send is no longer deferred by a read
    import_name = import_basename($("#open_file_fld").val());

    // the file goes to the backend as it is on disk, reading it into a
    // string here only to hand it straight back out is pure overhead
    if (browser_supports_file_api) {
      if (input.files[0]) {
        sendToBackend(input.files[0]);
      } else {
        $().uxmessage("error", "No file was selected.");
      }
    } else {
      // fallback
      $().uxmessage("error", "Requires browser with File API support.");
    }

    // reset file input form field so change event also triggers again
    $("#open_file_fld").val("");
  });

  // file upload form with alignment
  // TODO: The normal and with-alignment variants can probably be refactored
  //       since they have lots of similar logic.
  $("#open_align_file_fld").change(function (e) {
    e.preventDefault();
    $("#open_align_btn").button("loading");
    var input = $("#open_align_file_fld").get(0);

    // file API check
    var browser_supports_file_api = true;
    if (typeof window.FileReader !== "function") {
      browser_supports_file_api = false;
    } else if (!input.files) {
      browser_supports_file_api = false;
    }

    import_name = import_basename($("#open_align_file_fld").val());

    if (browser_supports_file_api) {
      if (input.files[0]) {
        sendToBackendWithAlignment(input.files[0]);
      } else {
        $().uxmessage("error", "No file was selected.");
      }
    } else {
      // fallback
      $().uxmessage("error", "Requires browser with File API support.");
    }

    // reset file input form field so change event also triggers again
    $("#open_align_file_fld").val("");
  });

  function import_basename(file_fld) {
    // job name from a file input value, without path or extension
    file_fld = file_fld.slice(file_fld.lastIndexOf("\\") + 1) || file_fld; // drop windows path
    file_fld = file_fld.slice(file_fld.lastIndexOf("/") + 1) || file_fld; // drop unix path
    return file_fld.slice(0, file_fld.lastIndexOf(".")) || file_fld; // drop extension
  }

  function sendToBackend(job) {
    // job is the picked File, uploaded as is

    // notify parsing started
    $().uxmessage("notice", "parsing " + import_name + " ...");
    // large file note
    if (job.size > 102400) {
      $().uxmessage("notice", "Big file! May take a few minutes.");
    }

    // send to backend
    var load_request = {
      job: job,
      name: import_name,
      optimize: true,
      progressive: true,
      review_issues: true,
    };
    request_post({
      url: "/load",
      data: load_request,
      success: import_load_answer,
      error: import_load_failed,
      complete: function (data) {
        $("#open_btn").button("reset");
      },
    });
  }

  function sendToBackendWithAlignment(job) {
    // job is the picked File, uploaded as is

    // notify parsing started
    $().uxmessage("notice", "parsing " + import_name + " ...");
    // large file note
    if (job.size > 102400) {
      $().uxmessage("notice", "Big file! May take a few minutes.");
    }

    request_get({
      url:
        "http://" +
        app_config_main.alignment_host +
        ":" +
        app_config_main.alignment_port +
        "/align/" +
        encodeURI(window.location.hostname) +
        "/" +
        window.location.port,
      error: function (data) {
        $().uxmessage("error", "/align error.");
        $().uxmessage("error", data.responseText, false);
      },
      complete: function (data) {
        $("#open_align_btn").button("reset");
      },
      success: function (matrix) {
        // send to backend
        var load_request = {
          job: job,
          name: import_name,
          optimize: true,
          matrix: matrix,
          review_issues: true,
        };
        request_post({
          url: "/load",
          data: load_request,
          success: import_load_answer,
          error: import_load_failed,
        });
      },
    });
  }
}); // ready

function import_load_answer(result) {
  // an import with problems is held server side until the operator decides,
  // a clean one answers as /load always did
  var held = issues_hold_from(result);
  if (!held) {
    import_loaded(result);
    return;
  }
  issues_show(
    import_name,
    held.issues,
    function () {
      request_get({
        url: "/load_commit/" + held.hold,
        success: import_loaded,
        error: import_load_failed,
      });
    },
    function () {
      request_get({
        url: "/load_cancel/" + held.hold,
        success: function () {
          $().uxmessage("notice", "Import cancelled.");
        },
      });
    },
  );
}

function import_load_failed(data) {
  var fatal = issues_fatal_from(data);
  if (fatal) {
    issues_show_fatal(import_name, fatal);
    return;
  }
  $().uxmessage("error", "/load error.");
  $().uxmessage("error", data.responseText, false);
}

function import_loaded(result) {
  // a progressive load answers with the quick parse first and a token
  // to pick up the optimized job when it is ready
  var jobname = result && result.name ? result.name : result;
  $().uxmessage("notice", "Parsed " + jobname + ".");
  queue_update();
  import_open(jobname);
  if (result && result.pending) {
    request_get({
      url: "/load_result/" + result.pending,
      success: function (done) {
        $().uxmessage("notice", "Optimized " + done.name + ".");
        import_open(done.name);
      },
      error: function () {},
    });
  }
}

function import_open(jobname, from_library) {
  from_library = typeof from_library !== "undefined" ? from_library : false; // default to false
  // get job in dba format
  var url = "/get/" + jobname;
  if (from_library === true) {
    url = "/get_library/" + jobname;
  }
  request_get({
    url: url,
    success: function (job) {
      // alert(JSON.stringify(data))
      // $().uxmessage('notice', data)

      function jobhandler_done() {
        tools_addfill_init();
        jobhandler.render();
        jobhandler.draw();
      }

      jobhandler.set(job, jobname, jobhandler_done);

      // debug, show image, stats
      // if ('defs' in job) {
      //   for (var i=0; i<job.defs.length; i++) {
      //     var rasterdef = job.defs[i];
      //     if (rasterdef.kind == "image") {
      //       $('#log_content').prepend('<img src="'+rasterdef.data.src+'">')
      //       if ('data' in rasterdef) {
      //         $().uxmessage('notice'," data: " + rasterdef.data)
      //       } else {
      //         $().uxmessage('notice', "no raster data")
      //       }
      //     }
      //   }
      // }
    },
    error: function (data) {
      $().uxmessage("error", "/get error.");
      $().uxmessage("error", data.responseText, false);
    },
  });
}
