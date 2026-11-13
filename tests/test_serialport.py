"""COM port (pyserial): transport and the protocol on top of it.  python -m unittest tests.test_serialport"""
import unittest

from tvxlink.protocol import PromaxLink
from tvxlink.serialport import SerialPortLink, list_ports
from tvxlink.simulator import SimulatedExplorer


class FakeSerial:
    """Behaves like serial.Serial, with the meter simulator inside."""

    def __init__(self):
        self.sim = SimulatedExplorer()
        self.buf = bytearray()
        self.timeout = 0.2
        self.dtr = self.rts = False

    def write(self, data):
        self.sim.write(data)

    def flush(self):
        pass

    def _pull(self):
        while True:
            chunk = self.sim.read()
            if not chunk:
                break
            self.buf += chunk

    @property
    def in_waiting(self):
        self._pull()
        return len(self.buf)

    def read(self, n=1):
        self._pull()
        out, self.buf = bytes(self.buf[:n]), self.buf[n:]
        return out

    def reset_input_buffer(self):
        self.buf.clear()

    def close(self):
        pass


class SerialTest(unittest.TestCase):
    def test_loopback(self):
        link = SerialPortLink("loop://")
        link.write(b"*?NA\r")
        self.assertEqual(link.read(timeout_ms=100), b"*?NA\r")
        self.assertEqual(link.read(timeout_ms=50), b"")
        link.close()

    def test_protocol_over_serial(self):
        import serial
        orig = serial.serial_for_url
        serial.serial_for_url = lambda *a, **k: FakeSerial()
        try:
            link = SerialPortLink("COM9")
        finally:
            serial.serial_for_url = orig
        p = PromaxLink(link)
        self.assertIn("SIMULATOR", p.query("*?NA"))
        self.assertTrue(p.send("*FR1721000").ack)

    def test_list_ports_runs(self):
        self.assertIsInstance(list_ports(), list)


if __name__ == "__main__":
    unittest.main()
