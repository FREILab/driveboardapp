
// module to handle design data
// converts between boundry representation and gcode
// creates previews



DataHandler = {

  paths_by_color : {},
  rasters : [],
  passes : [],
  stats : {},


  clear : function() {
    this.paths_by_color = {};
    this.rasters = [];
    this.passes = [];
    this.stats = {};
  },

  isEmpty : function() {
    return (Object.keys(this.paths_by_color).length == 0 && 
            this.rasters.length == 0);
  },




  // readers //////////////////////////////////

  setByPaths : function(paths_by_color) {
    // read boundaries
    // {'#000000':[[[x,y],[x,y], ..],[], ..], '#ffffff':[..]}
    this.clear();
    for (var color in paths_by_color) {
      var paths_src = paths_by_color[color];
      this.paths_by_color[color] = [];
      var paths = this.paths_by_color[color];
      for (var i=0; i<paths_src.length; i++) {
        var path = [];
        paths.push(path);
        var path_src = paths_src[i];
        for (var p=0; p<path_src.length; p++) {
          path.push([path_src[p][0], path_src[p][1]]);
        }
      }
    }
    // also calculate stats
    this.calculateBasicStats();
  },

  setByGcode : function(gcode) {
    // Read limited Gcode
    // G0, G00, G1, G01, G4, G04
    // G90, G91 (absolute, relative)
    // S, F, P
    // M0, M2, M3, M4, M5, M6
    // M80, M81, M82, M83, M84, M85
    // this.calculateBasicStats();
  },

  setByJson : function(strdata) {
    // read internal format
    // {'passes':{'colors':['#000000',..], 'feedrate':450, 'intensity':100},
    //  'paths_by_color':{'#000000':[[[x,y],[x,y], ..],[], ..], '#ffffff':[..]}
    //  'rasters':[{},{},...]
    // }
    this.clear();
    var data = JSON.parse(strdata);
    this.passes = data['passes'];
    this.paths_by_color = data['paths_by_color'];
    this.rasters = data['rasters'];
    if ('stats' in data) {
      this.stats = data['stats'];
    } else {
      this.calculateBasicStats();
    }
  },

  addRasters : function(rasters) {
    // rasters has this format:
    // [{'pos':[x,y], 'size':[w,h], 'size_px':[w,h], 'image':data}, {}, ...]
    for (var i=0; i<rasters.length; i++) {
      // maybe do some sanity checks here
      this.rasters.push(rasters[i]);
    }
    // also calculate stats
    this.calculateBasicStats();
  },


  // writers //////////////////////////////////

  getJson : function(exclude_colors) {
    // write internal format
    // exclude_colors is optional
    var paths_by_color = this.paths_by_color;
    var rasters = this.rasters;
    if (!(exclude_colors === undefined)) {
      paths_by_color = {};
      for (var color in this.paths_by_color) {
        if (!(color in exclude_colors)) {
          paths_by_color[color] = this.paths_by_color[color];
        }
      }
    }
    var data = {'passes': this.passes,
                'paths_by_color': paths_by_color,
                'rasters': this.rasters}
    return JSON.stringify(data);
  },

  getGcode : function() {
    // write machinable gcode, organize by passes, rasters first
    // header
    var glist = [];
    glist.push("G90\nM80\n");
    glist.push("G0F"+app_settings.max_seek_speed+"\n");
    // rasters
    for (var k=0; k<this.rasters.length; k++) {
      var raster = this.rasters[k];

      // raster Data
      //////////////
      // G8 P0.1
      // G8 X50
      // G8 N
      // G8 D<data>
      // G8 D<raster data encoded in ascii>
      // G8 N
      // G8 D<data>
      // ...

      // G8 P0.1 sets the dimensions of one 'dot'. It's the space reserved for
      // one data pixel, one character in the raster data. The technical minimum
      // is 0.034mm (based on the minimum step distance) but for best results
      // this should reflect the focus diameter of the setup.

      // G8 X50 defines the direction of the raster data and the offset. So 'X'
      // means data will be interpreted as x-axis lines. The offset is necessary
      // to achieve constant speed during engraving. It's the distance used for
      // accelerating the head (and also decelerating).

      // G8 D<data> sends the actual data. Likewise lines will be concatenated
      // until a 'G8 N' arrives. Currently line length is limited to 80
      // characters. The actual data is encoded into the extended ascii range
      // ([128,255]). Each character is a dot. The new raster line marker also
      // resets the head to the next line which is 0.1mm (or whatever was defined
      // with G8 Px) under the next
      ///////////////

      var x1 = raster.pos[0];
      var y1 = raster.pos[1];
      var width = raster.size_mm[0];
      var height = raster.size_mm[1];
      var pixwidth = raster.size_px[0];
      var pixheight = raster.size_px[1];
      var image = raster.image;

      glist.push("G1F"+app_settings.raster_feedrate+"\n");
      glist.push("G0X"+x1.toFixed(app_settings.num_digits)+"Y"+y1.toFixed(app_settings.num_digits)+"\n");
      glist.push("G8P"+app_settings.kerf.toFixed(app_settings.num_digits+2)+"\n");
      glist.push("G8X"+app_settings.raster_offset.toFixed(app_settings.num_digits)+"\n");
      glist.push("G8N\n");

      var p = 0;
      var pp = 0;
      var linechars = app_settings.raster_linechars
      var nfull = Math.floor(pixwidth/linechars)
      var partialchars = pixwidth % linechars

      for (var l=0; l<pixheight; l++) {  // raster lines
        // full command lines
        for (var f=0; f<nfull; f++) {
          pp = p+linechars;
          glist.push("G8D" + image.slice(p,pp) + "\n");
          p = pp;
        }
        // one partial command line
        pp = p+partialchars;
        glist.push("G8D" + image.slice(p,p+partialchars) + "\n");
        p = pp;
        // next raster line
        glist.push("G8N\n");
      }
    }
    // passes
    for (var i=0; i<this.passes.length; i++) {
      var pass = this.passes[i];
      var colors = pass['colors'];
      var feedrate = this.mapConstrainFeedrate(pass['feedrate']);
      var intensity = this.mapConstrainIntesity(pass['intensity']);
      glist.push("G1F"+feedrate+"\nS"+intensity+"\n");
      for (var c=0; c<colors.length; c++) {
        var color = colors[c];
        // Paths
        var paths = this.paths_by_color[color];
        for (var k=0; k<paths.length; k++) {
          var path = paths[k];
          if (path.length > 0) {
            var vertex = 0;
            var x = path[vertex][0];
            var y = path[vertex][1];
            glist.push("G0X"+x.toFixed(app_settings.num_digits)+
                         "Y"+y.toFixed(app_settings.num_digits)+"\n");
            for (vertex=1; vertex<path.length; vertex++) {
              var x = path[vertex][0];
              var y = path[vertex][1];
              glist.push("G1X"+x.toFixed(app_settings.num_digits)+
                           "Y"+y.toFixed(app_settings.num_digits)+"\n");
            }
          }
        }
      }
    }
    // footer
    glist.push("M81\nS0\nG0X0Y0F"+app_settings.max_seek_speed+"\n");
    // alert(JSON.stringify(glist.join('')))
    return glist.join('');
  },

  getBboxGcode : function() {
    if (!('_all_' in this.stats)) {
      this.calculateBasicStats();
    }
    var bbox = this.stats['_all_']['bbox'];
    var glist = [];
    glist.push("G90\n");
    glist.push("G0F"+app_settings.max_seek_speed+"\n");
    glist.push("G00X"+bbox[0].toFixed(3)+"Y"+bbox[1].toFixed(3)+"\n");
    glist.push("G00X"+bbox[2].toFixed(3)+"Y"+bbox[1].toFixed(3)+"\n");
    glist.push("G00X"+bbox[2].toFixed(3)+"Y"+bbox[3].toFixed(3)+"\n");
    glist.push("G00X"+bbox[0].toFixed(3)+"Y"+bbox[3].toFixed(3)+"\n");
    glist.push("G00X"+bbox[0].toFixed(3)+"Y"+bbox[1].toFixed(3)+"\n");
    glist.push("G0X0Y0F"+app_settings.max_seek_speed+"\n");
    return glist.join('');
  },



  // rendering //////////////////////////////////


  draw : function (canvas, scale, exclude_colors, exclude_rasters) {
    // draw rasters and paths
    // exclude_colors, exclude_rasters is optional
    canvas.background('#ffffff');
    canvas.noFill();
    var x_prev = 0;
    var y_prev = 0;
    // rasters
    if (exclude_rasters === undefined || exclude_rasters !== true) {
      for (var k=0; k<this.rasters.length; k++) {
        var raster = this.rasters[k];

        var x1 = raster.pos[0]*scale;
        var y1 = raster.pos[1]*scale;
        var width = raster.size_mm[0]*scale;
        var height = raster.size_mm[1]*scale;
        var pixwidth = raster.size_px[0];
        var pixheight = raster.size_px[1];
        var image = raster.image;

        var ppmmX = pixwidth / width;
        var ppmmY = pixheight / height;
        var offset = app_settings.raster_offset;

        canvas.stroke('#aaaaaa');
        canvas.line(x_prev, y_prev, x1-offset, y);
        for (var y = y1; y < y1 + height; y++) {
          var line = Math.round(ppmmY * (y-y1)) * pixwidth;
          for (var x=x1; x < x1 + width; x++) {
            var pixel = Math.round(line + (x - x1) * ppmmX);
            // convert pixel value from extended ascii to hex: [128,255] -> [0-ff]
            // hexpx = ((image[pixel].charCodeAt()-128)*2).toString(16)

            // convert pixel value from extended ascii to hex: [33,118] -> [0-ff]
            hexpx = ((image[pixel].charCodeAt()-33)*3).toString(16)
            canvas.stroke('#'+hexpx+hexpx+hexpx);
            canvas.line(x, y, x+1, y);
          }
          canvas.stroke('#aaaaaa');
          canvas.line(x1 + width, y, x1 + width + offset, y);
          canvas.line(x1 - offset, y, x1, y);
        }

        x_prev = x1 + width + offset;
        y_prev = y1 + height;
      }
    }
    // paths
    for (var color in this.paths_by_color) {
      if (exclude_colors === undefined || !(color in exclude_colors)) {
        var paths = this.paths_by_color[color];
        for (var k=0; k<paths.length; k++) {
          var path = paths[k];
          if (path.length > 0) {
            var x = path[0][0]*scale;
            var y = path[0][1]*scale;
            canvas.stroke('#aaaaaa');
            canvas.line(x_prev, y_prev, x, y);
            x_prev = x;
            y_prev = y;
            canvas.stroke(color);
            for (vertex=1; vertex<path.length; vertex++) {
              var x = path[vertex][0]*scale;
              var y = path[vertex][1]*scale;
              canvas.line(x_prev, y_prev, x, y);
              x_prev = x;
              y_prev = y;
            }
          }
        }
      }
    }
  },

  draw_bboxes : function (canvas, scale) {
    // draw with bboxes by color
    // only include colors that are in passe
    var bbox_combined = [Infinity, Infinity, 0, 0];

    function drawbb(stats) {
      var xmin = stat['bbox'][0]*scale;
      var ymin = stat['bbox'][1]*scale;
      var xmax = stat['bbox'][2]*scale;
      var ymax = stat['bbox'][3]*scale;
      canvas.stroke('#dddddd');
      canvas.line(xmin,ymin,xmin,ymax);
      canvas.line(xmin,ymax,xmax,ymax);
      canvas.line(xmax,ymax,xmax,ymin);
      canvas.line(xmax,ymin,xmin,ymin);
      this.bboxExpand(bbox_combined, xmin, ymin);
      this.bboxExpand(bbox_combined, xmax, ymax); 
    }

    // rasters
    if ('rasters' in this.stats) {
      drawbb(this.stats['rasters']);
    }
    // for all job colors
    for (var color in this.getPassesColors()) {
      drawbb(this.stats[color]);
    }
    // draw global bbox
    xmin = bbox_combined[0];
    ymin = bbox_combined[1];
    xmax = bbox_combined[2];
    ymax = bbox_combined[3];
    canvas.stroke('#dddddd');
    canvas.line(xmin,ymin,xmin,ymax);
    canvas.line(xmin,ymax,xmax,ymax);
    canvas.line(xmax,ymax,xmax,ymin);
    canvas.line(xmax,ymin,xmin,ymin);
  },



  // passes and colors //////////////////////////

  addPass : function(mapping) {
    // this describes in what order colors are written
    // and also what intensity and feedrate is used
    // mapping: {'colors':colors, 'feedrate':feedrate, 'intensity':intensity}
    this.passes.push(mapping);
  },

  setPassesFromLasertags : function(lasertags) {
    // lasertags come in this format
    // (pass_num, feedrate, units, intensity, units, color1, color2, ..., color6)
    // [(12, 2550, '', 100, '%', ':#fff000', ':#ababab', ':#ccc999', '', '', ''), ...]
    this.passes = [];
    for (var i=0; i<lasertags.length; i++) {
      var vals = lasertags[i];
      if (vals.length == 11) {
        var pass = vals[0];
        var feedrate = vals[1];
        var intensity = vals[3];
        if (typeof(pass) === 'number' && pass > 0) {
          //make sure to have enough pass widgets
          var passes_to_create = pass - this.passes.length
          if (passes_to_create >= 1) {
            for (var k=0; k<passes_to_create; k++) {
              this.passes.push({'colors':[], 'feedrate':1200, 'intensity':10})
            }
          }
          pass = pass-1;  // convert to zero-indexed
          // feedrate
          if (feedrate != '' && typeof(feedrate) === 'number') {
            this.passes[pass]['feedrate'] = feedrate;
          }
          // intensity
          if (intensity != '' && typeof(intensity) === 'number') {
            this.passes[pass]['intensity'] = intensity;
          }
          // colors
          for (var ii=5; ii<vals.length; ii++) {
            var col = vals[ii];
            if (col.slice(0,1) == '#') {
              this.passes[pass]['colors'].push(col);
            }
          }
        } else {
          $().uxmessage('error', "invalid lasertag (pass number)");
        }
      } else {
        $().uxmessage('error', "invalid lasertag (num of args)");
      }
    }
  },

  getPasses : function() {
    return this.passes;
  },

  hasPasses : function() {
    if (this.passes.length > 0) {return true}
    else {return false}
  },

  clearPasses : function() {
    this.passes = [];
  },

  getPassesColors : function() {
    var all_colors = {};
    for (var i=0; i<this.passes.length; i++) {
      var mapping = this.passes[i];
      var colors = mapping['colors'];
      for (var c=0; c<colors.length; c++) {
        var color = colors[c];
        all_colors[color] = true;
      }
    }
    return all_colors;
  },

  getAllColors : function() {
    // return list of colors
    return Object.keys(this.paths_by_color);
  },

  getColorOrder : function() {
      var color_order = {};
      var color_count = 0;
      for (var color in this.paths_by_color) {
        color_order[color] = color_count;
        color_count++;
      }
      return color_order
  },


  // stats //////////////////////////////////////

  calculateBasicStats : function() {
    // calculate bounding boxes and path lengths
    // for each color and also for '_all_'
    // bbox and length only account for feed lines
    // saves results in this.stats like so:
    // {'_all_':{'bbox':[xmin,ymin,xmax,ymax], 'length':numeral}, '#ffffff':{}, ..}

    var x_prev = 0;
    var y_prev = 0;
    var path_length_all = 0;
    var bbox_all = [Infinity, Infinity, 0, 0];
    var stats = {};

    // paths
    for (var color in this.paths_by_color) {
      var path_lenths_color = 0;
      var bbox_color = [Infinity, Infinity, 0, 0];
      var paths = this.paths_by_color[color];
      for (var k=0; k<paths.length; k++) {
        var path = paths[k];
        if (path.length > 1) {
          var x = path[0][0];
          var y = path[0][1];
          this.bboxExpand(bbox_color, x, y);
          x_prev = x;
          y_prev = y;
          for (vertex=1; vertex<path.length; vertex++) {
            var x = path[vertex][0];
            var y = path[vertex][1];
            path_lenths_color +=
              Math.sqrt((x-x_prev)*(x-x_prev)+(y-y_prev)*(y-y_prev));
            this.bboxExpand(bbox_color, x, y);
            x_prev = x;
            y_prev = y;
          }
        }
      }
      stats[color] = {
        'bbox':bbox_color,
        'length':path_lenths_color
      }
      // add to total also
      path_length_all += path_lenths_color;
      this.bboxExpand(bbox_all, bbox_color[0], bbox_color[1]);
      this.bboxExpand(bbox_all, bbox_color[2], bbox_color[3]);
    }

    // rasters
    if (this.rasters.length > 0) {
      var length_rasters = 0;
      var bbox_raster = [Infinity, Infinity, 0, 0];
      for (var k=0; k<this.rasters.length; k++) {
        var raster = this.rasters[k];
        this.bboxExpand(bbox_raster, 
                        raster.pos[0] - app_settings.raster_offset,
                        raster.pos[1]);
        this.bboxExpand(bbox_raster, 
                        raster.pos[0] + raster.size_mm[0] + app_settings.raster_offset, 
                        raster.pos[1] + raster.size_mm[1]);
        length_rasters += (2*app_settings.raster_offset + raster.size_mm[0]) * raster.size_px[1];
      }
      stats['rasters'] = {
        'bbox':bbox_raster,
        'length':length_rasters
      }
      // add to total also
      this.bboxExpand(bbox_all, bbox_raster[0], bbox_raster[1]);
      this.bboxExpand(bbox_all, bbox_raster[2], bbox_raster[3]);
    }

    // store in object var
    stats['_all_'] = {
      'bbox':bbox_all,
      'length':path_length_all
    }
    this.stats = stats;
  },


  bboxExpand : function(bbox, x, y) {
    if (x < bbox[0]) {bbox[0] = x;}
    else if (x > bbox[2]) {bbox[2] = x;}
    if (y < bbox[1]) {bbox[1] = y;}
    else if (y > bbox[3]) {bbox[3] = y;}
  },

  getJobPathLength : function() {
    var total_length = 0;
    for (var color in this.getPassesColors()) {
      stat = this.stats[color];
      total_length += stat['length'];
    }
    return total_length;
  },

  getJobBbox : function() {
    var total_bbox = [Infinity, Infinity, 0, 0];
    for (var color in this.getPassesColors()) {
      stat = this.stats[color];
      this.bboxExpand(total_bbox, stat['bbox'][0], stat['bbox'][1]);
      this.bboxExpand(total_bbox, stat['bbox'][2], stat['bbox'][3]);
    }
    return total_bbox;
  },


  // path optimizations /////////////////////////

  segmentizeLongLines : function() {
    var x_prev = 0;
    var y_prev = 0;
    var d2 = 0;
    var length_limit = app_settings.max_segment_length;
    var length_limit2 = length_limit*length_limit;

    var lerp = function(x0, y0, x1, y1, t) {
      return [x0*(1-t)+x1*t, y0*(1-t)+y1*t];
    }

    for (var color in this.paths_by_color) {
      var paths = this.paths_by_color[color];
      for (var k=0; k<paths.length; k++) {
        var path = paths[k];
        if (path.length > 1) {
          var new_path = [];
          var copy_from = 0;
          var x = path[0][0];
          var y = path[0][1];
          // ignore seek lines for now
          x_prev = x;
          y_prev = y;
          for (vertex=1; vertex<path.length; vertex++) {
            var x = path[vertex][0];
            var y = path[vertex][1];
            d2 = (x-x_prev)*(x-x_prev) + (y-y_prev)*(y-y_prev);
            // check length for each feed line
            if (d2 > length_limit2) {
              // copy previous verts
              for (var n=copy_from; n<vertex; n++) {
                new_path.push(path[n]);
              }
              // add lerp verts
              var t_step = 1/(Math.sqrt(d2)/length_limit);
              for(var t=t_step; t<0.99; t+=t_step) {
                new_path.push(lerp(x_prev, y_prev, x, y, t));
              }
              copy_from = vertex;
            }
            x_prev = x;
            y_prev = y;
          }
          if (new_path.length > 0) {
            // add any rest verts from path
            for (var p=copy_from; p<path.length; p++) {
              new_path.push(path[p]);
            }
            copy_from = 0;
            paths[k] = new_path;
          }
        }
      }
    }
  },


  // auxilliary /////////////////////////////////

  mapConstrainFeedrate : function(rate) {
    rate = parseInt(rate);
    if (rate < .1) {
      rate = .1;
      $().uxmessage('warning', "Feedrate constrained to 0.1");
    } else if (rate > 24000) {
      rate = 24000;
      $().uxmessage('warning', "Feedrate constrained to 24000");
    }
    return rate.toString();
  },

  mapConstrainIntesity : function(intens) {
    intens = parseInt(intens);
    if (intens < 0) {
      intens = 0;
      $().uxmessage('warning', "Intensity constrained to 0");
    } else if (intens > 100) {
      intens = 100;
      $().uxmessage('warning', "Intensity constrained to 100");
    }
    //map to 255 for now until we change the backend
    return Math.round(intens * 2.55).toString();
  },

}