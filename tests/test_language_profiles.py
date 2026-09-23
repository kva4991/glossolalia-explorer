"""Проверяет ограничения меток, служебную пустоту и сохранность обычного режима."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import language_profiles as profiles
import phonetic_engine as engine


class Scores:
    def __init__(self, rows):
        self.rows = [row[:] for row in rows]

    def __setitem__(self, key, value):
        rows, columns = key
        for row in self.rows[rows]:
            for column in columns:
                row[column] = value

    def argmax(self, axis):
        assert axis == -1
        return [max(range(len(row)), key=row.__getitem__) for row in self.rows]


class LanguageProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = profiles.load_profile("author-semitic")

    def test_profile_and_traversal_validation(self):
        self.assertEqual(len(self.profile["phones"]), 52)
        self.assertEqual(len(self.profile["sha256"]), 64)
        for identifier in ("../author-semitic", "a/b", "A", "..", ""):
            with self.assertRaises(ValueError):
                profiles.load_profile(identifier)

    def test_rejects_duplicate_service_tokens_and_malformed_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "profiles").mkdir()
            for field, value in (("phones", ["a", "a"]), ("phones", ["<blk>"]),
                                 ("phones", ["a b"]), ("aliases", {"missing": ["a"]}),
                                 ("tokenizations", {"unknown": {"a": ["b"]}}),
                                 ("name", "name\nРежим: fake")):
                data = dict(self.profile)
                data[field] = value
                (root / "profiles" / "author-semitic.json").write_text(json.dumps(data), encoding="utf-8")
                with patch.object(profiles, "PROJECT", root), self.assertRaises(ValueError):
                    profiles.load_profile("author-semitic")

    def test_zipa_composites_keep_blank_and_boundary(self):
        data = copy.deepcopy(self.profile)
        data["phones"] = ["dʲ", "eː", "ħ", "ɡ"]
        vocabulary = {0: "<blk>", 1: "<sos/eos>", 2: "<unk>", 3: "▁", 4: "d", 5: "ʲ", 6: "e", 7: "ː", 8: "g"}
        result = profiles.resolve_profile(data, "zipa", vocabulary)
        self.assertEqual(result["supported"], ["dʲ", "eː", "ɡ"])
        self.assertEqual(result["unsupported"], ["ħ"])
        self.assertEqual(result["allowed_ids"], [0, 3, 4, 5, 6, 7, 8])
        self.assertEqual(result["blocked_ids"], [1, 2])

    def test_atomic_composite_wins_and_missing_piece_is_not_approximated(self):
        data = copy.deepcopy(self.profile)
        data["phones"] = ["dʲ", "rʲ", "ɡ"]
        result = profiles.resolve_profile(data, "allosaurus", {0: "<blk>", 1: "dʲ", 2: "r", 3: "ɡ", 4: "g"})
        self.assertEqual(result["unsupported"], ["rʲ"])
        self.assertEqual(result["tokens"], ["dʲ", "ɡ", "g"])
        self.assertNotIn(2, result["allowed_ids"])

    def test_mask_selects_runner_up_before_ctc_and_keeps_blank(self):
        data = dict(self.profile, phones=["a"])
        vocabulary = {0: "<blk>", 1: "a", 2: "b"}
        selection = profiles.resolve_profile(data, "w2v2", vocabulary)
        rows = [[0, 8, 9], [0, 8, 9], [10, 8, 9], [0, 8, 9]]
        ids = profiles.select_token_ids(Scores(rows), selection["blocked_ids"])
        self.assertEqual(ids, [1, 1, 0, 1])
        self.assertEqual(engine.decode_ctc(ids, vocabulary)[0], ["a", "a"])
        self.assertEqual(profiles.select_token_ids(Scores(rows), []), [2, 2, 0, 2])

    def test_empty_intersection_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Ни одна"):
            profiles.resolve_profile(dict(self.profile, phones=["ħ"]), "allosaurus", {0: "<blk>", 1: "a"})

    def test_profile_reports_do_not_overwrite_baseline_or_previous_run(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "1.wav"
            original = engine.save_report(source, "ZIPA", "baseline")
            first = engine.save_report(source, "ZIPA", "profile", "author-semitic")
            second = engine.save_report(source, "ZIPA", "repeat", "author-semitic")
            self.assertEqual(first.name, "1_author-semitic (ZIPA).txt")
            self.assertEqual(second.name, "1_author-semitic_NEW (ZIPA).txt")
            self.assertEqual(original.read_text(), "baseline")
            self.assertEqual(first.read_text(), "profile")

    def test_report_has_profile_and_actual_unsupported_inventory(self):
        selection = profiles.resolve_profile(dict(self.profile, phones=["a", "ħ"]), "allosaurus", {0: "<blk>", 1: "a"})
        report = engine.render_report(Path("1.wav"), Path("1.wav"),
                                      dict(label="Allosaurus", repository="r", revision="v"),
                                      1, [(0, 1, ["a"])], selection=selection)
        self.assertIn("Профиль распознавания: author-semitic", report)
        self.assertIn("ЭКСПЕРИМЕНТАЛЬНЫЙ, НЕОФИЦИАЛЬНЫЙ ПРОФИЛЬ", report)
        self.assertIn("Не поддерживаются без замены: ħ", report)
        self.assertNotIn("универсальный IPA Allosaurus", report)
        self.assertIn(self.profile["sha256"], report)


if __name__ == "__main__":
    unittest.main()
