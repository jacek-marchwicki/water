"""
Unit tests for WaterH BLE packet parsing.
"""
from __future__ import annotations

import unittest
from tests.base import IsolatedCollectorTestCase
from collector.collector import parse_pt_packets


class TestPacketParsing(IsolatedCollectorTestCase):
    def test_single_pt_packet_one_record(self):
        """Verify parsing a single PT packet with exactly one 13-byte sip record."""
        # Header (6 bytes): 'PT' (2B) + len (2B) + count (1B) + echo (1B)
        header = bytes.fromhex("5054000d0d06")
        # Record (13 bytes):
        # 2026-09-23 15:45:12, intake=300ml, tds=72ppm, temp=22.0C (220), padding=0
        # Year: 2026 - 2000 = 26 (0x1a)
        # Month: 09 (0x09), Day: 23 (0x17), Hour: 15 (0x0f), Min: 45 (0x2d), Sec: 12 (0x0c)
        # Intake: 300 = 0x012c
        # TDS: 72 = 0x0048
        # Temp: 220 = 0x00dc
        # Padding: 0x00
        rec = bytes.fromhex("1a09170f2d0c012c004800dc00")
        pkt = header + rec

        records, pt_bytes = parse_pt_packets([pkt])

        self.assertEqual(pt_bytes, 13)
        self.assertEqual(len(records), 1)
        sip = records[0]
        self.assertEqual(sip["timestamp"], "2026-09-23T15:45:12")
        self.assertEqual(sip["intake_ml"], 300)
        self.assertEqual(sip["tds"], 72)
        self.assertAlmostEqual(sip["temp_c"], 22.0)
        self.assertEqual(sip["raw"], "1a 09 17 0f 2d 0c 01 2c 00 48 00 dc 00")

    def test_multiple_records_in_single_packet(self):
        """Verify parsing multiple 13-byte records in one PT packet."""
        header = bytes.fromhex("5054001a1a06")
        rec1 = bytes.fromhex("1a09170a000000c8003200be00")  # 200ml, 50tds, 19.0C
        rec2 = bytes.fromhex("1a09170c1e00015e003700c800")  # 350ml, 55tds, 20.0C
        pkt = header + rec1 + rec2

        records, pt_bytes = parse_pt_packets([pkt])

        self.assertEqual(pt_bytes, 26)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["intake_ml"], 200)
        self.assertEqual(records[0]["tds"], 50)
        self.assertAlmostEqual(records[0]["temp_c"], 19.0)
        self.assertEqual(records[1]["intake_ml"], 350)
        self.assertEqual(records[1]["tds"], 55)
        self.assertAlmostEqual(records[1]["temp_c"], 20.0)

    def test_multi_packet_continuation_stream(self):
        """Verify parsing stream with initial PT packet followed by continuation packets."""
        # First packet: PT header (6 bytes) + 1 record (13 bytes)
        pkt1 = bytes.fromhex("505400270d06") + bytes.fromhex("1a09170800000096001e006400")  # 150ml
        # Continuation packet 1: 2-byte prefix + 1 record
        pkt2 = bytes.fromhex("0d06") + bytes.fromhex("1a091709000000fa0028009600")  # 250ml
        # Continuation packet 2: 2-byte prefix + 1 record
        pkt3 = bytes.fromhex("0d06") + bytes.fromhex("1a09170a00000190003200c800")  # 400ml

        records, pt_bytes = parse_pt_packets([pkt1, pkt2, pkt3])

        self.assertEqual(pt_bytes, 39)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["intake_ml"], 150)
        self.assertEqual(records[1]["intake_ml"], 250)
        self.assertEqual(records[2]["intake_ml"], 400)

    def test_empty_or_non_pt_packets(self):
        """Verify handling empty packet list or non-PT responses."""
        self.assertEqual(parse_pt_packets([]), ([], 0))

        # RP response packet should not be parsed as PT
        rp_packet = bytes.fromhex("52500027000164000000")
        self.assertEqual(parse_pt_packets([rp_packet]), ([], 0))

    def test_partial_trailing_bytes_discarded(self):
        """Verify trailing bytes shorter than 13 bytes are ignored."""
        header = bytes.fromhex("505400100d06")
        rec = bytes.fromhex("1a09170f2d0c012c004800dc00")  # 13 bytes
        extra = bytes.fromhex("1a0917")  # 3 extra bytes (< 13)
        pkt = header + rec + extra

        records, pt_bytes = parse_pt_packets([pkt])

        self.assertEqual(pt_bytes, 16)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["intake_ml"], 300)

    def test_invalid_date_fallback(self):
        """Verify invalid date/time fallback formatting string when datetime() raises ValueError."""
        header = bytes.fromhex("5054000d0d06")
        # Month: 13 (invalid!), Day: 32 (invalid!)
        # 1a = 26 (2026), 0d = 13 (month), 20 = 32 (day)
        rec = bytes.fromhex("1a0d200f2d0c012c004800dc00")
        pkt = header + rec

        records, pt_bytes = parse_pt_packets([pkt])

        self.assertEqual(len(records), 1)
        # Should hit the except ValueError branch and format manually
        self.assertEqual(records[0]["timestamp"], "2026-13-32T15:45:12")
        self.assertEqual(records[0]["intake_ml"], 300)


if __name__ == "__main__":
    unittest.main()
