"""Проверяет очередь, CTC и сохранность результатов без загрузки моделей."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("phonetic_engine", ROOT / "src" / "phonetic_engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class PhoneticTests(unittest.TestCase):
    def test_ctc_repeated_phone_after_blank(self):
        vocabulary = {0: "<blk>", 1: "a", 2: "ː"}
        self.assertEqual(engine.decode_ctc([0, 1, 1, 0, 1, 2, 2], vocabulary)[0], ["a", "a", "ː"])

    def test_overlap_does_not_duplicate_or_drop_central_frames(self):
        class FakeRecognizer:
            vocabulary = {0: "<blk>", 1: "a", 2: "b"}
            blank_id = 0

            def predict(self, samples):
                return samples

        samples = [1] * 8 + [0] * 2 + [1] * 5 + [2] * 7 + [0] * 3 + [2] * 5
        with patch.object(engine, "SAMPLE_RATE", 10), patch.object(engine, "CHUNK_SECONDS", 1), patch.object(engine, "CONTEXT_SECONDS", 1):
            blocks = engine.transcribe(samples, FakeRecognizer())
        actual = [phone for _, _, phones in blocks for phone in phones]
        self.assertEqual(actual, ["a", "a", "b", "b"])
        self.assertEqual([(start, end) for start, end, _ in blocks], [(0, 1), (1, 2), (2, 3)])

    def test_model_suffix_collision_unicode_and_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "2\u200b.[молитва].wav"
            source.write_bytes(b"original")
            paths = [engine.save_report(source, "ZIPA", "ʃ a\n") for _ in range(3)]
            other = engine.save_report(source, "W2V2", "e l\n")
            self.assertEqual([path.name for path in paths], [
                "2\u200b.[молитва] (ZIPA).txt",
                "2\u200b.[молитва]_NEW (ZIPA).txt",
                "2\u200b.[молитва]_NEW_NEW (ZIPA).txt",
            ])
            self.assertEqual(other.name, "2\u200b.[молитва] (W2V2).txt")
            self.assertEqual(paths[0].read_bytes(), "ʃ a\n".encode("utf-8"))
            self.assertEqual(source.read_bytes(), b"original")

    def test_reuse_wav_without_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "1..m4a"
            wav = source.with_suffix(".wav")
            wav.write_bytes(b"existing")
            with patch.object(engine.subprocess, "run", side_effect=AssertionError("Conversion was invoked")):
                self.assertEqual(engine.prepare_wav(source, "ffmpeg"), wav)
                self.assertEqual(engine.prepare_wav(wav, "ffmpeg"), wav)
            self.assertEqual(wav.read_bytes(), b"existing")

    def test_failed_conversion_does_not_leave_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "failed.mp3"
            source.write_bytes(b"source")
            failure = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"invalid")
            with patch.object(engine.subprocess, "run", side_effect=failure):
                with self.assertRaises(subprocess.CalledProcessError):
                    engine.prepare_wav(source, "ffmpeg")
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_different_installed_revision_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "installation.json").write_text('{"repository":"r","revision":"old"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Ревизия"):
                engine.validate_installation(path, {"repository": "r", "revision": "new"})

    def test_batch_continues_after_error_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            bad, good = path / "bad.wav", path / "good.wav"
            bad.touch()
            good.touch()
            manifest = path / "batch.json"
            manifest.write_text(json.dumps([str(bad), str(good), str(good)]), encoding="utf-8")
            config = {"label": "ZIPA", "repository": "test", "revision": "test"}
            with (
                patch.object(sys, "argv", ["worker", "--engine", "zipa", "--runtime", directory, "--manifest", str(manifest)]),
                patch.object(engine, "load_config", return_value=config),
                patch.object(engine, "validate_installation"),
                patch.object(engine, "Recognizer"),
                patch.object(engine, "read_audio", side_effect=[ValueError("invalid"), [0] * 16000]),
                patch.object(engine, "transcribe", return_value=[(0, 1, ["a"])]),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(engine.main(), 1)
            self.assertEqual(stdout.getvalue().count("__GLOSSOLALIA_SAVED__"), 1)
            self.assertTrue((path / "good (ZIPA).txt").is_file())
            self.assertFalse((path / "bad (ZIPA).txt").exists())


if __name__ == "__main__":
    unittest.main()
