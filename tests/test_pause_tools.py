"""Проверяет классификацию пауз и сохранность транскрипции без тяжёлых зависимостей."""

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import pause_tools as pauses
import phonetic_engine as engine

spec = importlib.util.spec_from_file_location("repeat_index", ROOT / "scripts" / "build_repeat_index.py")
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)
CONFIG = dict(short_seconds=0.2, medium_seconds=0.6, long_seconds=1.5, speech_threshold=0.5, release_threshold=0.35, revision="test")


class PauseTests(unittest.TestCase):
    def test_threshold_boundaries(self):
        self.assertEqual([pauses.classify_pause(value, CONFIG) for value in (.199, .2, .599, .6, 1.499, 1.5)],
                         [None, "КОРОТКАЯ", "КОРОТКАЯ", "СРЕДНЯЯ", "СРЕДНЯЯ", "ДЛИННАЯ"])

    def test_three_lengths_and_hysteresis(self):
        probabilities = [0.9] * 20 + [0.1] * 10 + [0.9] * 20 + [0.1] * 25 + [0.9] * 20 + [0.1] * 60 + [0.9] * 20
        found, speech = pauses.pauses_from_probabilities(probabilities, len(probabilities) * 512, CONFIG)
        self.assertTrue(speech)
        self.assertEqual([row["kind"] for row in found], ["КОРОТКАЯ", "СРЕДНЯЯ", "ДЛИННАЯ"])
        self.assertEqual([row["duration"] for row in found], [.32, .8, 1.92])
        self.assertEqual(pauses.pauses_from_probabilities([.9] + [.4] * 50, 51 * 512, CONFIG)[0], [])

    def test_leading_trailing_and_complete_non_speech(self):
        found, speech = pauses.pauses_from_probabilities([.1] * 10 + [.9] * 10 + [.1] * 20, 40 * 512 - 200, CONFIG)
        self.assertTrue(speech)
        self.assertEqual(found[0]["start"], 0)
        self.assertEqual(found[-1]["end"], (40 * 512 - 200) / 16000)
        found, speech = pauses.pauses_from_probabilities([.1] * 10, 5000, CONFIG)
        self.assertFalse(speech)
        self.assertEqual((found[0]["start"], found[0]["end"]), (0, .3125))

    def test_invalid_probabilities_and_length_fail(self):
        for values, count in [([float("nan")], 512), ([1.2], 512), ([.1], 513)]:
            with self.assertRaises(ValueError):
                pauses.pauses_from_probabilities(values, count, CONFIG)

    def test_render_keeps_every_token_even_inside_a_pause(self):
        tokens = ["▁", "a", "ː", "n", "a"]
        gaps = [dict(kind="СРЕДНЯЯ", start=.2, end=1.0, duration=.8)]
        text = pauses.render_with_pauses(tokens, [0, .1, .3, .8, 1.1], gaps)
        self.assertEqual(re.sub(r"\[[^\]]+\]", "", text).split(), tokens)
        self.assertIn("[СРЕДНЯЯ ПАУЗА: 0.800 с; 0.200–1.000 с]", text)
        with self.assertRaises(ValueError):
            pauses.render_with_pauses(tokens, [0], gaps)

    def test_ctc_times_preserve_repetition_across_windows(self):
        class FakeRecognizer:
            vocabulary = {0: "<blk>", 1: "a", 2: "b"}
            blank_id = 0

            def predict(self, samples):
                return samples

        samples = [1] * 8 + [0] * 2 + [1] * 5 + [2] * 7 + [0] * 3 + [2] * 5
        times = []
        with patch.object(engine, "SAMPLE_RATE", 10), patch.object(engine, "CHUNK_SECONDS", 1), patch.object(engine, "CONTEXT_SECONDS", 1):
            blocks = engine.transcribe(samples, FakeRecognizer(), times)
        self.assertEqual([label for _, _, labels in blocks for label in labels], ["a", "a", "b", "b"])
        self.assertEqual(times, [.05, 1.05, 1.55, 2.55])

    def test_report_parser_does_not_build_words_across_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "one.wav"
            source.write_bytes(b"audio")
            report = source.with_suffix(".txt")
            config = dict(label="Allosaurus", repository="allosaurus/uni2005", revision="test")
            result = dict(config=CONFIG, speech_detected=True, pauses=[dict(kind="СРЕДНЯЯ", start=.2, end=1.0, duration=.8)])
            text = engine.render_report(source, source, config, 2, [(0, 2, list("abcdabcd"))], result,
                                        [0, .1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6])
            report.write_text(text, encoding="utf-8")
            reports, _ = index.collect_reports([Path(directory)])
            self.assertEqual(index.build_index(reports), [])
            self.assertEqual(len(reports[0]["segments"]), 2)

    def test_empty_report_is_valid_without_invented_phones(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "quiet.wav"
            source.write_bytes(b"audio")
            config = dict(label="ZIPA", repository="test", revision="test")
            result = dict(config=CONFIG, speech_detected=False, pauses=[dict(kind="ДЛИННАЯ", start=0, end=2, duration=2)])
            text = engine.render_report(source, source, config, 2, [(0, 2, [])], result, [])
            source.with_suffix(".txt").write_text(text, encoding="utf-8")
            reports, _ = index.collect_reports([Path(directory)])
            self.assertEqual(reports[0]["segments"], [])
            self.assertEqual(index.build_index(reports), [])
            self.assertIn("ДЛИННАЯ ПАУЗА", text)


if __name__ == "__main__":
    unittest.main()
