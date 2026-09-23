"""Создаёт локальный указатель точных повторов в фонетических отчётах."""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def header(text, name, default=None):
    match = re.search(rf"(?m)^{re.escape(name)}: (.+)$", text)
    return match.group(1).strip() if match else default


def parse_report(path):
    text = path.read_text(encoding="utf-8-sig")
    # Ограниченный набор не должен заменять независимый результат той же акустической модели
    if header(text, "Профиль распознавания"):
        return None
    first = text.splitlines()[0] if text else ""
    segments = []
    position = 0
    if first.startswith("=== КОМБИНИРОВАННЫЙ ФОНЕТИЧЕСКИЙ АНАЛИЗ"):
        engine = "Allosaurus"
        match = re.search(r"(?m)^ЯЗЫК: .* \(ipa\)\s*$", text)
        if not match:
            raise ValueError(f"Нет универсального IPA: {path}")
        body = re.split(r"(?m)^ЯЗЫК:", text[match.end():], maxsplit=1)[0]
        current = []
        for line in body.splitlines():
            phone = re.match(r"^\[([\d.,]+)s\] (\S+) \(p=", line)
            if phone:
                position += 1
                time = float(phone[1].replace(",", "."))
                current.append(dict(label=phone[2], position=position, start=time, end=time))
            elif line.strip().startswith("->") and current:
                segments.append(current)
                current = []
        if current:
            segments.append(current)
        time_kind = "model_phone_marks"
    elif first.startswith("ФОНЕТИЧЕСКАЯ ТРАНСКРИПЦИЯ — "):
        engine = first.rsplit(" — ", 1)[1]
        if engine not in ("ZIPA", "W2V2", "Allosaurus"):
            return None
        if "=== ЧАСТИ ЗАПИСИ ===" not in text or "=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===" not in text:
            raise ValueError(f"Неполный отчёт: {path}")

        full, body = text.split("=== ЧАСТИ ЗАПИСИ ===", 1)
        full = full.split("=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===", 1)[1].split()
        blocks = re.split(r"\[([\d.]+)–([\d.]+) с\]", body)
        current = []
        observed = []
        for index in range(1, len(blocks), 3):
            start, end = map(float, blocks[index:index + 2])
            for label in blocks[index + 2].split():
                position += 1
                observed.append(label)
                if label == "▁":
                    if current:
                        segments.append(current)
                        current = []
                    continue
                current.append(dict(label=label, position=position, start=start, end=end))
        if current:
            segments.append(current)
        if observed != full:
            raise ValueError(f"Полная последовательность не совпала с окнами: {path}")
        if "=== ТРАНСКРИПЦИЯ С ПАУЗАМИ ===" in text and full:
            annotated = text.split("=== ТРАНСКРИПЦИЯ С ПАУЗАМИ ===", 1)[1].split("=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===", 1)[0]
            annotated = annotated.replace("Паузы заданной длительности детектором не найдены", "")
            parts = re.split(r"\[(?:КОРОТКАЯ|СРЕДНЯЯ|ДЛИННАЯ) ПАУЗА: [\d.]+ с; [\d.]+–[\d.]+ с\]", annotated)
            if [label for part in parts for label in part.split()] != full:
                raise ValueError(f"Последовательность с паузами отличается от исходной: {path}")
            boundaries = []
            consumed = 0
            for part in parts[:-1]:
                consumed += len(part.split())
                boundaries.append(consumed)
            split_segments = []
            for segment in segments:
                current = []
                for token in segment:
                    if current and any(current[-1]["position"] <= boundary < token["position"] for boundary in boundaries):
                        split_segments.append(current)
                        current = []
                    current.append(token)
                if current:
                    split_segments.append(current)
            segments = split_segments
        time_kind = "processing_window"
    else:
        return None

    if not segments and not first.startswith("ФОНЕТИЧЕСКАЯ ТРАНСКРИПЦИЯ — "):
        raise ValueError(f"Нет фонетических меток: {path}")
    source = header(text, "WAV") or header(text, "Файл")
    if not source:
        raise ValueError(f"Нет исходного файла: {path}")
    source = Path(source)
    if not source.is_absolute():
        source = path.parent / source
    source = source.resolve()
    original_source = source
    if engine == "Allosaurus":
        # Оболочка Allosaurus распознаёт соседний WAV даже при выборе исходного M4A
        source = source.with_suffix(".wav")

    return dict(
        engine=engine, model=header(text, "Модель"), revision=header(text, "Ревизия"),
        date=header(text, "Дата", ""), report=str(path.resolve()),
        report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source=str(source), original_source=str(original_source),
        segments=segments, time_kind=time_kind,
    )


def collect_reports(directories):
    selected = {}
    hashes = {}
    skipped = []
    for path in sorted({p.resolve() for directory in directories for p in directory.glob("*.txt")}):
        report = parse_report(path)
        if report is None:
            continue
        source = Path(report["source"])
        if not source.is_file():
            raise ValueError(f"Исходник для сверки повторов отсутствует: {source}")
        if source not in hashes:
            with source.open("rb") as stream:
                hashes[source] = hashlib.file_digest(stream, "sha256").hexdigest()
        report["recording_id"] = hashes[source]

        # Копии аудио и повторные запуски модели не должны увеличивать число записей
        key = (report["recording_id"], report["engine"])
        date = datetime.fromisoformat(report["date"]).astimezone().timestamp()
        rank = (date, path.stat().st_mtime_ns, str(path))
        previous = selected.get(key)
        if previous is None or rank > previous[0]:
            if previous:
                skipped.append(previous[1]["report"])
            selected[key] = (rank, report)
        else:
            skipped.append(str(path))

    return [item[1] for item in selected.values()], sorted(skipped)


def build_index(reports, min_length=4, max_length=8, min_recordings=1):
    groups = defaultdict(list)
    for report in reports:
        for segment in report["segments"]:
            for length in range(min_length, max_length + 1):
                for offset in range(len(segment) - length + 1):
                    tokens = segment[offset:offset + length]
                    labels = tuple(token["label"] for token in tokens)
                    key = (report["engine"], report["model"], report["revision"], labels)
                    occurrence = dict(
                        recording_id=report["recording_id"], source=report["source"],
                        report=report["report"], token_start=tokens[0]["position"],
                        token_end=tokens[-1]["position"], start=tokens[0]["start"],
                        end=tokens[-1]["end"], time_kind=report["time_kind"],
                    )
                    groups[key].append(occurrence)

    candidates = []
    for key, occurrences in groups.items():
        kept = []
        last_end = {}
        for occurrence in sorted(occurrences, key=lambda row: (row["recording_id"], row["token_start"])):
            recording = occurrence["recording_id"]
            if occurrence["token_start"] > last_end.get(recording, 0):
                kept.append(occurrence)
                last_end[recording] = occurrence["token_end"]
        recordings = len({row["recording_id"] for row in kept})
        if len(kept) < 2 or recordings < min_recordings:
            continue

        fingerprint = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
        candidates.append(dict(
            id="REP-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16],
            engine=key[0], model=key[1], revision=key[2], labels=list(key[3]),
            recording_count=recordings, occurrence_count=len(kept), occurrences=kept,
        ))
    return sorted(candidates, key=lambda row: (
        -row["recording_count"], -len(row["labels"]), -row["occurrence_count"], row["engine"], row["labels"],
    ))


def render_markdown(result):
    lines = [
        "# Локальный указатель повторов", "", f"Создан: {result['created_at']}", "",
        f"Исходных записей по SHA-256: {result['recording_count']}; отчётов: {len(result['reports'])}; "
        f"точных повторяющихся последовательностей: {len(result['candidates'])}.", "",
        "Это повторы меток одного движка и ревизии. Границы слов, перевод и язык не установлены.",
        "Числа меток включают отдельные диакритики и не равны числам фонем. "
        "Повторы внутри одной записи включены; перекрывающиеся вхождения одного сочетания не суммируются.",
        "Короткие вложенные сочетания остаются отдельными строками: число строк не равно числу слов.",
        "Allosaurus: только универсальный IPA, без пересечения блоков. ZIPA: маркер ▁ разделяет поиск. "
        "Время ZIPA/W2V2 — окна обработки, не границы слова. Позиции меток начинаются с 1; ▁ учитывается в нумерации.",
        "Полные пути, хеши и все вхождения находятся в index.json рядом с этим файлом. "
        "Этот машинный отчёт остаётся локально; гипотезы ведутся отдельно в документации проекта.", "",
    ]
    for candidate in result["candidates"]:
        lines.extend([
            f"## {candidate['id']} — {candidate['engine']}", "",
            "`" + " ".join(candidate["labels"]) + "`", "",
            f"Записей: {candidate['recording_count']}; неперекрывающихся вхождений: {candidate['occurrence_count']}.", "",
        ])
        for row in candidate["occurrences"]:
            kind = "метки модели" if row["time_kind"] == "model_phone_marks" else "окно обработки"
            lines.append(f"- {Path(row['source']).name}: {kind} {row['start']:.2f}–{row['end']:.2f} с; "
                         f"позиции {row['token_start']}–{row['token_end']}; {Path(row['report']).name}")
        lines.append("")
    return "\n".join(lines) + "\n"


def save_result(result, output):
    output = output.resolve()
    if output.is_relative_to(PROJECT):
        raise ValueError("Полный указатель нужно сохранять вне репозитория проекта")
    output.mkdir(parents=True, exist_ok=True)
    name = "словарь-повторов-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    while True:
        directory = output / name
        try:
            directory.mkdir()
            break
        except FileExistsError:
            name += "_NEW"

    (directory / "index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "ПОВТОРЫ.md").write_text(render_markdown(result), encoding="utf-8")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+", help="Папки с TXT и исходными аудио, без рекурсивного обхода")
    parser.add_argument("--output", type=Path, help="Куда создать новую папку результата; по умолчанию первая входная папка")
    parser.add_argument("--min-length", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=8)
    parser.add_argument("--min-recordings", type=int, default=1)
    args = parser.parse_args()
    if not 2 <= args.min_length <= args.max_length <= 20 or args.min_recordings < 1:
        parser.error("Нужны длины 2 <= min <= max <= 20 и min-recordings >= 1")

    try:
        for directory in args.directories:
            if not directory.is_dir():
                raise ValueError(f"Нет папки: {directory}")
        reports, skipped = collect_reports(args.directories)
        if not reports:
            raise ValueError("Распознаваемых отчётов не найдено")

        result = dict(
            schema_version=1, created_at=datetime.now().astimezone().isoformat(),
            parameters=dict(min_length=args.min_length, max_length=args.max_length, min_recordings=args.min_recordings),
            recording_count=len({row["recording_id"] for row in reports}),
            reports=[{key: value for key, value in row.items() if key != "segments"} for row in reports],
            superseded_reports=skipped,
            candidates=build_index(reports, args.min_length, args.max_length, args.min_recordings),
        )
        directory = save_result(result, args.output or args.directories[0])
        print(f"✓ Указатель сохранён: {directory}")
        print(f"Записей: {result['recording_count']}; отчётов: {len(reports)}; сочетаний: {len(result['candidates'])}")
        return 0
    except (OSError, ValueError) as error:
        print(f"Ошибка: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
