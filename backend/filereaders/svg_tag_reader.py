
import re
import math
import logging

from utilities import matrixMult, parseFloats

from svg_attribute_reader import SVGAttributeReader
from svg_path_reader import SVGPathReader

log = logging.getLogger("svg_reader")


class SVGTagReader:
    
    def __init__(self, tolerance):

        # init helper for attribute reading
        self._attribReader = SVGAttributeReader()
        # init helper for path handling
        self._pathReader = SVGPathReader(tolerance)

        self._handlers = {
            'g': self.g,
            'path': self.path,
            'polygon': self.polygon,
            'polyline': self.polyline,
            'rect': self.rect,
            'line': self.line,
            'circle': self.circle,
            'ellipse': self.ellipse,
            'image': self.image,
            'defs': self.defs,
            'style': self.style
        }


    def readTag(self, tag, node):
        """Read a tag.

        Any tag name that is in self._handlers will be handled.
        Similarly any attribute name in self._attribReader._handlers
        will be parsed. Both tag and attribute results are stored in
        node.

        Any path data is ultimately handled by 
        self._pathReader.addPath(...). For any  geometry that is not
        already in the 'd' attribute of a 'path' tag this class 
        converts it first to this format and then delegates it to 
        addPath(...).

        """
        tagName = self._getTag(tag)
        if tagName in self._handlers:
            log.debug("reading tag: " + tagName)
            # parse own attributes and overwrite in node
            for attr,value in tag.attrib.items():
                log.debug("considering attrib: " + attr)
                self._attribReader.readAttrib(node, attr, value)
            # accumulate transformations
            node['xformToWorld'] = matrixMult(node['xformToWorld'], node['xform'])
            # read tag
            self._handlers[tagName](node)


    def has_handler(self, tag):
        tagName = self._getTag(tag)
        return bool(tagName in self._handlers)
    
    
    def g(self, node):
        # http://www.w3.org/TR/SVG11/struct.html#Groups
        # has transform and style attributes
        pass


    def path(self, node):
        # http://www.w3.org/TR/SVG11/paths.html
        # has transform and style attributes
        d = node.get("d")
        self._pathReader.addPath(d, node) 


    def polygon(self, node):
        # http://www.w3.org/TR/SVG11/shapes.html#PolygonElement
        # has transform and style attributes
        d = ['M'] + node['points'] + ['z']
        node['points'] = None
        self._pathReader.addPath(d, node)      


    def polyline(self, node):
        # http://www.w3.org/TR/SVG11/shapes.html#PolylineElement
        # has transform and style attributes
        d = ['M'] + node['points']
        node['points'] = None
        self._pathReader.addPath(d, node)  


    def rect(self, node):
        # http://www.w3.org/TR/SVG11/shapes.html#RectElement
        # has transform and style attributes      
        w = node.get('width') or 0
        h = node.get('height') or 0
        x = node.get('x') or 0
        y = node.get('y') or 0
        rx = node.get('rx')
        ry = node.get('ry')
        if rx is None or ry is None:  # no rounded corners
            d = ['M', x, y, 'h', w, 'v', h, 'h', -w, 'z']
            self._pathReader.addPath(d, node)
        else:                         # rounded corners
            if 'ry' is None: ry = rx
            if rx < 0.0: rx *=-1
            if ry < 0.0: ry *=-1
            d = ['M', x+rx , y ,
                 'h', w-2*rx,
                 'c', rx, 0.0, rx, ry, rx, ry,
                 'v', h-ry,
                 'c', '0.0', ry, -rx, ry, -rx, ry,
                 'h', -w+2*rx,
                 'c', -rx, '0.0', -rx, -ry, -rx, -ry,
                 'v', -h+ry,
                 'c', '0.0','0.0','0.0', -ry, rx, -ry,
                 'z']
            self._pathReader.addPath(d, node)        


    def line(self, node):
        # http://www.w3.org/TR/SVG11/shapes.html#LineElement
        # has transform and style attributes
        x1 = node.get('x1') or 0
        y1 = node.get('y1') or 0
        x2 = node.get('x2') or 0
        y2 = node.get('y2') or 0      
        d = ['M', x1, y1, 'L', x2, y2]
        self._pathReader.addPath(d, node)        


    def circle(self, node):
        # http://www.w3.org/TR/SVG11/shapes.html#CircleElement
        # has transform and style attributes      
        r = node.get('r')
        cx = node.get('cx') or 0
        cy = node.get('cy') or 0
        if r > 0.0:
            d = ['M', cx-r, cy,                  
                 'A', r, r, 0, 0, 0, cx, cy+r,
                 'A', r, r, 0, 0, 0, cx+r, cy,
                 'A', r, r, 0, 0, 0, cx, cy-r,
                 'A', r, r, 0, 0, 0, cx-r, cy,
                 'Z']
            self._pathReader.addPath(d, node)


    def ellipse(self, node):
        # has transform and style attributes
        rx = node.get('rx')
        ry = node.get('ry')
        cx = node.get('cx') or 0
        cy = node.get('cy') or 0        
        if rx > 0.0 and ry > 0.0:
            d = ['M', cx-rx, cy,                  
                 'A', rx, ry, 0, 0, 0, cx, cy+ry,
                 'A', rx, ry, 0, 0, 0, cx+rx, cy,
                 'A', rx, ry, 0, 0, 0, cx, cy-ry,
                 'A', rx, ry, 0, 0, 0, cx-rx, cy,
                 'Z']          
            self._pathReader.addPath(d, node)
    


    def image(self, node):
        # not supported
        # has transform and style attributes
        log.warn("'image' tag is not supported, ignored")     


    def defs(self, node):
        # not supported
        # http://www.w3.org/TR/SVG11/struct.html#Head
        # has transform and style attributes      
        log.warn("'defs' tag is not supported, ignored")     

    def style(self, node):
        # not supported: embedded style sheets
        # http://www.w3.org/TR/SVG11/styling.html#StyleElement
        # instead presentation attributes and the 'style' attribute 
        log.warn("'style' tag is not supported, use presentation \
                      attributes or the style attribute instead")     




    def _getTag(self, domNode):
        """Get tag name without possible namespace prefix."""
        tag = domNode.tag
        return tag[tag.rfind('}')+1:]


