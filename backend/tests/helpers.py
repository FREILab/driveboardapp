"""Shared test helpers."""


class FakeSerialDevice:
    """Minimal stand-in for a pyserial device used in driveboard tests.

    ``read`` drains a preloaded byte queue (for exercising the RX parser);
    ``write`` records everything sent (for asserting TX-side output).
    """

    def __init__(self, rx=b""):
        self._rx = bytearray(rx)
        self.written = bytearray()
        self.closed = False

    def read(self, n):
        chunk = bytes(self._rx[:n])
        del self._rx[:n]
        return chunk

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def flushOutput(self):
        pass

    def flushInput(self):
        pass

    def close(self):
        self.closed = True


TEMPLATE_SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="20mm" viewBox="0 0 40 20">
  <text x="0" y="19" font-size="2">=param:v:1500:100:6000:Feed=</text>
  <text x="0" y="17" font-size="2">=pass1:{{round(v)}}:100:#ff0000=</text>
  <rect x="0" y="0" width="{{v/150}}" height="5" fill="none" stroke="#ff0000"/>
</svg>"""
