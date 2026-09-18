import unittest

from companion import (
    CHUNK_DATA_MAX,
    MAGIC,
    Reassembler,
    _voice_identity,
    clean_dialogue_text,
    decode_chunk,
    dialogue_dedupe_key,
    prosody_for_segment,
    split_dialogue,
)


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


def feed_payload(reassembler, msg_id, payload):
    chunks = [payload[i:i + CHUNK_DATA_MAX] for i in range(0, len(payload), CHUNK_DATA_MAX)] or [b""]
    message = None
    for index, data in enumerate(chunks, start=1):
        message = reassembler.accept(decode_chunk(make_packet(msg_id, index, len(chunks), data))) or message
    return message


class ProtocolTests(unittest.TestCase):
    def test_chunk_decode_and_utf8_reassembly(self):
        payload = (
            "quest\x1fCreature-0-1-2-3-448-0000ABCD\x1fHogger\x1f"
            + "Åäö – full dialogue. " * 80
        ).encode("utf-8")
        message = feed_payload(Reassembler(), 100, payload)
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

    def test_cross_source_duplicate_dialogue_is_suppressed(self):
        r = Reassembler()
        first = b"gossip\x1fCreature-0-1-2-3-448-AAAA\x1fHogger\x1fYou no take candle!"
        second = b"monster_say\x1fCreature-0-9-8-7-448-BBBB\x1fHogger\x1fYou no take candle!"
        self.assertIsNotNone(feed_payload(r, 30, first))
        self.assertIsNone(feed_payload(r, 31, second))

    def test_creature_voice_identity_ignores_spawn_uid(self):
        a = _voice_identity("Creature-0-1465-0-2105-448-000043F59F", "Hogger")
        b = _voice_identity("Creature-0-9999-0-9999-448-ABCDEF", "Hogger")
        self.assertEqual(a, "npc:448")
        self.assertEqual(a, b)

    def test_dialogue_dedupe_key_ignores_ui_markup(self):
        a = dialogue_dedupe_key("", "Narrator", "|cffffffffHello|r   world")
        b = dialogue_dedupe_key("", "Narrator", "Hello world")
        self.assertEqual(a, b)

    def test_clean_dialogue_preserves_hyperlink_visible_text(self):
        source = "|cffff0000Hello|r |Hitem:123|h[Magic Sword]|h |T123:16|t friend"
        self.assertEqual(clean_dialogue_text(source), "Hello [Magic Sword] friend")

    def test_split_dialogue_keeps_segments_bounded(self):
        text = ("This is a sentence. " * 80) + "Done!"
        segments = split_dialogue(text, limit=120)
        self.assertGreater(len(segments), 2)
        self.assertTrue(all(len(segment) <= 120 for segment in segments))
        self.assertTrue(segments[-1].endswith("!"))

    def test_prosody_is_conservative(self):
        speed, pause = prosody_for_segment("quest", "Where are you?")
        self.assertGreaterEqual(speed, 0.90)
        self.assertLessEqual(speed, 1.10)
        self.assertGreater(pause, 0)


if __name__ == "__main__":
    unittest.main()
