import unittest

from companion import CHUNK_DATA_MAX, MAGIC, Reassembler, _voice_identity, decode_chunk


def make_packet(msg_id, chunk_index, chunk_total, data):
    body = bytes([
        (msg_id >> 8) & 0xFF,
        msg_id & 0xFF,
        (chunk_index >> 8) & 0xFF,
        chunk_index & 0xFF,
        (chunk_total >> 8) & 0xFF,
        chunk_total & 0xFF,
        len(data),
    ]) + data
    return MAGIC + body + bytes([sum(body) % 256])


class ProtocolTests(unittest.TestCase):
    def test_chunk_decode_and_utf8_reassembly(self):
        payload = (
            "quest\x1fCreature-0-1-2-3-448-0000ABCD\x1fHogger\x1f"
            + "Åäö – full dialogue. " * 80
        ).encode("utf-8")
        chunks = [payload[i:i + CHUNK_DATA_MAX] for i in range(0, len(payload), CHUNK_DATA_MAX)]
        r = Reassembler()
        message = None
        for index, data in enumerate(chunks, start=1):
            message = r.accept(decode_chunk(make_packet(100, index, len(chunks), data))) or message
        self.assertIsNotNone(message)
        kind, guid, name, text = message
        self.assertEqual(kind, "quest")
        self.assertTrue(guid.startswith("Creature-"))
        self.assertEqual(name, "Hogger")
        self.assertIn("Åäö", text)

    def test_bad_checksum_is_rejected(self):
        packet = bytearray(make_packet(7, 1, 1, b"test"))
        packet[-1] ^= 0x01
        self.assertIsNone(decode_chunk(bytes(packet)))

    def test_duplicate_message_id_is_ignored_after_completion(self):
        data = b"test\x1f\x1fNarrator\x1fHello"
        decoded = decode_chunk(make_packet(22, 1, 1, data))
        r = Reassembler()
        self.assertIsNotNone(r.accept(decoded))
        self.assertIsNone(r.accept(decoded))

    def test_creature_voice_identity_ignores_spawn_uid(self):
        a = _voice_identity("Creature-0-1465-0-2105-448-000043F59F", "Hogger")
        b = _voice_identity("Creature-0-9999-0-9999-448-ABCDEF", "Hogger")
        self.assertEqual(a, "npc:448")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
