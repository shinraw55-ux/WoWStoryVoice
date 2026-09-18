import queue
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import beta_hardening
from companion import CHUNK_DATA_MAX, MAGIC, Reassembler, decode_chunk


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


class BetaHardeningTests(unittest.TestCase):
    def test_valid_wav_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp) / "good.wav"
            bad = Path(temp) / "bad.wav"
            sf.write(good, np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
            bad.write_bytes(b"not a wav")
            self.assertTrue(beta_hardening.is_valid_wav(good))
            self.assertFalse(beta_hardening.is_valid_wav(bad))

    def test_log_rotation_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "app.log"
            log.write_bytes(b"x" * 200)
            self.assertTrue(beta_hardening.rotate_log(log, max_bytes=100, backups=2))
            self.assertFalse(log.exists())
            self.assertTrue((Path(temp) / "app.log.1").exists())

    def test_cache_prunes_oldest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = []
            for i in range(4):
                path = root / f"{i}.wav"
                path.write_bytes(b"x" * 100)
                stamp = time.time() - (100 - i)
                path.touch()
                import os
                os.utime(path, (stamp, stamp))
                files.append(path)
            removed, removed_bytes = beta_hardening.prune_cache(root, max_bytes=250, max_files=10)
            self.assertEqual(removed, 2)
            self.assertEqual(removed_bytes, 200)
            self.assertFalse(files[0].exists())
            self.assertFalse(files[1].exists())
            self.assertTrue(files[2].exists())
            self.assertTrue(files[3].exists())

    def test_bounded_controller_drops_oldest_pending(self):
        class DummyState:
            def set(self, **kwargs):
                pass

        class DummyBase:
            def __init__(self):
                self.jobs = queue.Queue()
                self.state = DummyState()
                self.accepted = []

            def enqueue(self, kind, npc_guid, npc_name, text):
                item = (kind, npc_name, text)
                self.jobs.put(item)
                self.accepted.append(item)
                return item

        Controller = beta_hardening.make_bounded_controller(DummyBase, max_pending=3)
        controller = Controller()
        for i in range(5):
            controller.enqueue("gossip", "", f"NPC{i}", f"line {i}")
        self.assertEqual(controller.jobs.qsize(), 3)
        remaining = [controller.jobs.get_nowait() for _ in range(3)]
        self.assertEqual([item[1] for item in remaining], ["NPC2", "NPC3", "NPC4"])

    def test_transport_soak_reassembles_many_long_unicode_messages(self):
        r = Reassembler()
        for msg_id in range(1, 301):
            text = (f"Åäö quest {msg_id}. " * 20).strip()
            payload = (
                f"quest\x1fCreature-0-1-2-3-{1000 + msg_id}-AAAA\x1fNPC {msg_id}\x1f{text}"
            ).encode("utf-8")
            chunks = [payload[i:i + CHUNK_DATA_MAX] for i in range(0, len(payload), CHUNK_DATA_MAX)]
            message = None
            # Deliberately send chunks out of order and repeat one chunk to stress reassembly.
            order = list(range(len(chunks), 0, -1))
            order.append(order[-1])
            for index in order:
                decoded = decode_chunk(make_packet(msg_id, index, len(chunks), chunks[index - 1]))
                result = r.accept(decoded)
                if result is not None:
                    message = result
            self.assertIsNotNone(message)
            kind, guid, name, rebuilt = message
            self.assertEqual(kind, "quest")
            self.assertEqual(name, f"NPC {msg_id}")
            self.assertEqual(rebuilt, text)


if __name__ == "__main__":
    unittest.main()
