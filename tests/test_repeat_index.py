"""Проверяет достоверность счётчиков повторов и сохранность исходных данных."""

import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("repeat_index", ROOT / "scripts" / "build_repeat_index.py")
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


def report_text(source, blocks, engine="ZIPA", date="2026-09-22T19:00:00+03:00"):
    full = " ".join(labels for _, _, labels in blocks)
    chunks = "\n".join(f"[{start:.2f}–{end:.2f} с]\n{labels}" for start, end, labels in blocks)
    return (
        f"ФОНЕТИЧЕСКАЯ ТРАНСКРИПЦИЯ — {engine}\nФайл: {source}\nWAV: {source}\n"
        f"Модель: test\nРевизия: a\nДата: {date}\n"
        f"=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===\n{full}\n=== ЧАСТИ ЗАПИСИ ===\n{chunks}\n"
    )


class RepeatTests(unittest.TestCase):
    def test_profile_does_not_replace_unrestricted_result(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original = self.make_report(folder, "one", [(0, 3, "a b c d")])
            limited = folder / "one_author-semitic (ZIPA).txt"
            limited.write_text(original.read_text(encoding="utf-8").replace(
                "Ревизия: a", "Ревизия: a\nПрофиль распознавания: author-semitic"), encoding="utf-8")
            self.assertIsNone(index.parse_report(limited))
            reports, _ = index.collect_reports([folder])
            self.assertEqual([report["report"] for report in reports], [str(original.resolve())])

    def make_report(self, folder, name, blocks, engine="ZIPA", date="2026-09-22T19:00:00+03:00"):
        source = folder / (name + ".wav")
        source.write_bytes(name.encode("utf-8"))
        path = folder / (name + ".txt")
        path.write_text(report_text(source, blocks, engine, date), encoding="utf-8")
        return path

    def test_latest_run_and_copied_audio_are_not_new_occurrences(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = self.make_report(folder, "2\u200b.", [(0, 3, "a b c d")])
            second = self.make_report(folder, "copy", [(0, 3, "e f g h")], date="2026-09-22T18:00:01+02:00")
            second.with_suffix(".wav").write_bytes(first.with_suffix(".wav").read_bytes())
            reports, skipped = index.collect_reports([folder, folder])
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["report"], str(second.resolve()))
            self.assertEqual(skipped, [str(first.resolve())])
            self.assertEqual(index.build_index(reports), [])

    def test_models_and_revisions_do_not_multiply_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = self.make_report(folder, "one", [(0, 3, "a b c d")])
            second = self.make_report(folder, "two", [(0, 3, "a b c d")], engine="W2V2")
            reports, _ = index.collect_reports([folder])
            self.assertEqual(index.build_index(reports), [])
            second.write_text(second.read_text(encoding="utf-8").replace("W2V2", "ZIPA").replace("Ревизия: a", "Ревизия: b"), encoding="utf-8")
            reports, _ = index.collect_reports([folder])
            self.assertEqual(index.build_index(reports), [])
            second.write_text(second.read_text(encoding="utf-8").replace("Ревизия: b", "Ревизия: a"), encoding="utf-8")
            reports, _ = index.collect_reports([folder])
            candidate = index.build_index(reports)[0]
            self.assertEqual((candidate["recording_count"], candidate["occurrence_count"]), (2, 2))

    def test_overlap_and_single_recording_repetition(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.make_report(folder, "one", [(0, 3, "a a a a a a a a")])
            reports, _ = index.collect_reports([folder])
            candidate = index.build_index(reports)[0]
            self.assertEqual(candidate["labels"], ["a"] * 4)
            self.assertEqual(candidate["occurrence_count"], 2)
            self.assertEqual(index.build_index(reports, min_recordings=2), [])

    def test_window_boundary_keeps_sequence_and_boundary_marker_splits_it(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = self.make_report(folder, "one", [(0, 30, "▁ a b"), (30, 40, "c d ▁ a b c d")])
            reports, _ = index.collect_reports([folder])
            candidate = index.build_index(reports)[0]
            self.assertEqual(candidate["labels"], ["a", "b", "c", "d"])
            first = candidate["occurrences"][0]
            self.assertEqual((first["start"], first["end"], first["token_start"]), (0, 40, 2))
            self.assertEqual(first["time_kind"], "processing_window")
            path.write_text(report_text(path.with_suffix(".wav"), [(0, 3, "a b ▁ c d a b ▁ c d")]), encoding="utf-8")
            reports, _ = index.collect_reports([folder])
            self.assertEqual(index.build_index(reports), [])

    def test_no_silent_folding_of_vowels_length_or_diacritics(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            self.make_report(folder, "one", [(0, 3, "a l o m ▁ a l ɔ m ▁ a l̪ o m ▁ a l oː m")])
            reports, _ = index.collect_reports([folder])
            self.assertEqual(index.build_index(reports), [])

    def test_allosaurus_only_ipa_and_no_cross_pause_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "one.wav"
            source.write_bytes(b"audio")
            path = folder / "one.txt"
            body = "\n".join(f"[{number / 10}s] {label} (p=0.9)" for number, label in enumerate("abcd"))
            path.write_text(
                f"=== КОМБИНИРОВАННЫЙ ФОНЕТИЧЕСКИЙ АНАЛИЗ ===\nФайл: {source}\nДата: 2026-09-22 19:00:00\n"
                f"ЯЗЫК: IPA (ipa)\n{body}\n -> a b c d\n"
                "[1s] a (p=0.9)\n[1.1s] b (p=0.9)\n -> a b\n"
                "[2s] c (p=0.9)\n[2.1s] d (p=0.9)\n -> c d\n"
                f"ЯЗЫК: Hebrew (heb)\n{body}\n -> a b c d\n",
                encoding="utf-8-sig",
            )
            reports, _ = index.collect_reports([folder])
            self.assertEqual(len(reports[0]["segments"]), 3)
            self.assertEqual(index.build_index(reports), [])
            path.write_text(path.read_text(encoding="utf-8-sig").replace(str(source), str(source.with_suffix(".m4a"))), encoding="utf-8-sig")
            reports, _ = index.collect_reports([folder])
            self.assertEqual(reports[0]["source"], str(source.resolve()))

    def test_inconsistent_report_and_missing_audio_fail_visibly(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = self.make_report(folder, "one", [(0, 3, "a b c d")])
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("a b c d", "a b c e", 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "не совпала"):
                index.collect_reports([folder])
            path.write_text(text.replace(str(path.with_suffix(".wav")), str(folder / "missing.wav")), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "отсутствует"):
                index.collect_reports([folder])

    def test_new_output_preserves_old_results_and_rejects_project(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            result = dict(created_at="test", recording_count=0, reports=[], candidates=[])
            with patch.object(index, "datetime") as clock:
                clock.now.return_value = datetime(2026, 9, 22, 19)
                first = index.save_result(result, folder)
                original = (first / "index.json").read_bytes()
                second = index.save_result(result, folder)
            self.assertNotEqual(first, second)
            self.assertEqual(second.name, first.name + "_NEW")
            self.assertEqual((first / "index.json").read_bytes(), original)
            with patch.object(index, "PROJECT", folder):
                with self.assertRaisesRegex(ValueError, "вне репозитория"):
                    index.save_result(result, folder / "results")


if __name__ == "__main__":
    unittest.main()
