"""Wire-format tests use synthetic bytes only and never open sockets."""

import struct
import unittest

from btdos.protocol import make_sdp_request, parse_sdp_response


def sdp_pdu(kind, tid, parameters):
    return struct.pack(">BHH", kind, tid, len(parameters)) + parameters


def search_response(tid=0x1234, total=2, handles=(0x10000, 0x10001), continuation=b""):
    parameters = struct.pack(">HH", total, len(handles))
    parameters += b"".join(struct.pack(">I", handle) for handle in handles)
    parameters += bytes([len(continuation)]) + continuation
    return sdp_pdu(3, tid, parameters)


class SdpProtocolTests(unittest.TestCase):
    def test_request_has_network_byte_order_and_no_continuation(self):
        packet = make_sdp_request(0x1234, 0x110B)
        self.assertEqual(packet[:5], bytes.fromhex("02 12 34 00 08"))
        self.assertEqual(packet[5:10], bytes.fromhex("35 03 19 11 0b"))
        self.assertEqual(packet[-1:], b"\x00")
        self.assertEqual(struct.unpack(">H", packet[10:12])[0], 16)

    def test_request_accepts_uuid16_and_transaction_id_boundaries(self):
        for tid in (0, 0xFFFF):
            for uuid in (0, 0xFFFF):
                with self.subTest(tid=tid, uuid=uuid):
                    packet = make_sdp_request(tid, uuid)
                    self.assertEqual(struct.unpack(">H", packet[1:3])[0], tid)

    def test_request_rejects_values_outside_uint16(self):
        for tid, uuid in ((-1, 0x110B), (0x10000, 0x110B), (1, -1), (1, 0x10000)):
            with self.subTest(tid=tid, uuid=uuid):
                with self.assertRaises((ValueError, struct.error)):
                    make_sdp_request(tid, uuid)

    def test_complete_service_search_response(self):
        parsed = parse_sdp_response(search_response(), 0x1234)
        self.assertEqual(parsed["pdu"], 3)
        self.assertEqual(parsed["total_records"], 2)
        self.assertEqual(parsed["returned_records"], 2)
        self.assertTrue(parsed["complete"])
        self.assertEqual(parsed["continuation_hex"], "")

    def test_empty_search_is_valid_complete_response(self):
        parsed = parse_sdp_response(search_response(total=0, handles=()), 0x1234)
        self.assertEqual(parsed["total_records"], 0)
        self.assertEqual(parsed["returned_records"], 0)
        self.assertTrue(parsed["complete"])

    def test_continuation_is_recorded_as_incomplete_not_silently_followed(self):
        parsed = parse_sdp_response(search_response(total=3, continuation=b"\xaa\xbb"), 0x1234)
        self.assertFalse(parsed["complete"])
        self.assertEqual(parsed["continuation_hex"], "aabb")

    def test_error_response_is_not_success(self):
        parsed = parse_sdp_response(sdp_pdu(1, 0x1234, b"\x00\x03"), 0x1234)
        self.assertEqual(parsed["pdu"], 1)
        self.assertEqual(parsed["error_code"], 3)

    def test_transaction_id_mismatch(self):
        with self.assertRaises(ValueError):
            parse_sdp_response(search_response(), 0x4321)

    def test_truncation_and_extra_bytes_are_rejected(self):
        packet = search_response()
        malformed = [packet[:n] for n in range(len(packet))]
        malformed.append(packet + b"\x00")
        for value in malformed:
            with self.subTest(data=value.hex()):
                with self.assertRaises(ValueError):
                    parse_sdp_response(value, 0x1234)

    def test_inconsistent_record_counts_are_rejected(self):
        malformed = (
            search_response(total=1),
            search_response(total=17, handles=()),
            search_response(total=3),
        )
        for value in malformed:
            with self.subTest(data=value.hex()):
                with self.assertRaises(ValueError):
                    parse_sdp_response(value, 0x1234)

    def test_wrong_pdu_and_invalid_error_length_are_rejected(self):
        for packet in (sdp_pdu(5, 0x1234, b""), sdp_pdu(1, 0x1234, b"\x00")):
            with self.subTest(packet=packet.hex()):
                with self.assertRaises(ValueError):
                    parse_sdp_response(packet, 0x1234)

    def test_inconsistent_continuation_length_is_rejected(self):
        packet = sdp_pdu(3, 0x1234, struct.pack(">HH", 0, 0) + b"\x02\xaa")
        with self.assertRaises(ValueError):
            parse_sdp_response(packet, 0x1234)


if __name__ == "__main__":
    unittest.main()
