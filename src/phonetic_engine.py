"""Локальная пакетная фонетическая транскрипция ZIPA и Wav2Vec2Phoneme."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pause_tools import PauseDetector, render_with_pauses
from language_profiles import load_profile, profile_report_lines, resolve_profile, select_token_ids

SAMPLE_RATE = 16000
CHUNK_SECONDS = 30
CONTEXT_SECONDS = 2


def load_config(engine):
    project = Path(__file__).resolve().parents[1]
    return json.loads((project / "engines.json").read_text(encoding="utf-8"))[engine]


def decode_ctc(ids, vocabulary, blank_id=0, previous=None, offsets=None):
    """Удаляет CTC-пустоты и соседние повторы, сохраняя повтор после пустоты."""
    tokens = []
    for offset, item in enumerate(ids):
        item = int(item)
        if item != previous and item != blank_id:
            token = vocabulary[item]
            if token not in ("<s>", "</s>", "<pad>", "<sos/eos>"):
                tokens.append(token)
                if offsets is not None:
                    offsets.append(offset)
        previous = item
    return tokens, previous


def prepare_wav(source, ffmpeg):
    """Переиспользует WAV, а новую конвертацию публикует только после успеха."""
    if source.suffix.lower() == ".wav":
        return source
    wav = source.with_suffix(".wav")
    if wav.exists():
        if not wav.is_file():
            raise ValueError(f"Путь WAV занят каталогом: {wav}")
        return wav

    handle, temporary_name = tempfile.mkstemp(prefix=".glossolalia-", suffix=".wav", dir=source.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-c:a", "pcm_s16le", str(temporary)],
            check=True, capture_output=True,
        )
        # В Windows rename не заменяет WAV, созданный другим процессом
        temporary.rename(wav)
    finally:
        temporary.unlink(missing_ok=True)
    return wav


def read_audio(wav, ffmpeg):
    """Декодирует рабочее моно 16 кГц в память, не меняя исходный WAV."""
    import numpy as np

    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
         "-i", str(wav), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "pipe:1"],
        check=True, capture_output=True,
    )
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if samples.size < SAMPLE_RATE // 10 or not np.isfinite(samples).all():
        raise ValueError("Аудио слишком короткое или содержит некорректные отсчёты")
    return samples


class Recognizer:
    """Загружает одну модель на всю очередь и возвращает последовательность CTC."""

    def __init__(self, engine, model_directory):
        import torch

        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.engine = engine
        self.blank_id = 0
        self.blocked_ids = []

        if engine == "zipa":
            import onnxruntime
            from lhotse.features.kaldi.extractors import Fbank, FbankConfig

            options = onnxruntime.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            self.model = onnxruntime.InferenceSession(
                str(model_directory / "model.onnx"), options,
                providers=["CPUExecutionProvider"],
            )
            self.extractor = Fbank(FbankConfig(num_filters=80, dither=0.0, snip_edges=False))
            self.vocabulary = {}
            for line in (model_directory / "tokens.txt").read_text(encoding="utf-8").splitlines():
                token, index = line.rsplit(maxsplit=1)
                self.vocabulary[int(index)] = token
        else:
            # Распознавание после установки не обращается к Hugging Face
            os.environ["HF_HUB_OFFLINE"] = "1"
            from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

            self.extractor = Wav2Vec2FeatureExtractor.from_pretrained(
                str(model_directory), local_files_only=True,
            )
            self.model = Wav2Vec2ForCTC.from_pretrained(
                str(model_directory), local_files_only=True,
            ).eval()
            self.blank_id = self.model.config.pad_token_id
            vocabulary = json.loads((model_directory / "vocab.json").read_text(encoding="utf-8"))
            self.vocabulary = {index: token for token, index in vocabulary.items()}

    def predict(self, samples):
        import numpy as np
        import torch

        if self.engine == "zipa":
            features = self.extractor.extract_batch(
                [torch.from_numpy(samples).unsqueeze(0)], sampling_rate=SAMPLE_RATE,
            )[0].unsqueeze(0)
            outputs = self.model.run(None, {
                "x": features.numpy(),
                "x_lens": np.array([features.shape[1]], dtype=np.int64),
            })
            scores = outputs[0][0]
            if len(outputs) > 1:
                scores = scores[:int(outputs[1][0])]
            return select_token_ids(scores, self.blocked_ids)

        inputs = self.extractor(samples, sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.inference_mode():
            scores = self.model(**inputs).logits[0]
            return select_token_ids(scores, self.blocked_ids).cpu().numpy()


def transcribe(samples, recognizer, token_times=None):
    """Оставляет центральные кадры перекрывающихся окон перед CTC-декодированием."""
    size = CHUNK_SECONDS * SAMPLE_RATE
    context = CONTEXT_SECONDS * SAMPLE_RATE
    previous = None
    blocks = []

    for start in range(0, len(samples), size):
        end = min(start + size, len(samples))
        left = max(0, start - context)
        right = min(len(samples), end + context)
        ids = recognizer.predict(samples[left:right])
        if not len(ids):
            raise ValueError("Модель вернула пустой массив кадров")

        # Привязка кадров к окну приблизительная и не является разметкой границ фонем
        first = round((start - left) * len(ids) / (right - left))
        last = round((end - left) * len(ids) / (right - left))
        offsets = []
        phones, previous = decode_ctc(
            ids[first:last], recognizer.vocabulary, recognizer.blank_id, previous, offsets,
        )
        if token_times is not None:
            token_times.extend((left + (first + offset + 0.5) * (right - left) / len(ids)) / SAMPLE_RATE for offset in offsets)
        blocks.append((start / SAMPLE_RATE, end / SAMPLE_RATE, phones))
    return blocks


def transcribe_allosaurus(samples, python, selection=None):
    """Запускает Allosaurus с универсальным или явно ограниченным набором меток."""
    import numpy as np
    import wave

    with tempfile.TemporaryDirectory(prefix="glossolalia-allosaurus-") as directory:
        wav = Path(directory) / "input.wav"
        output = Path(directory) / "phones.txt"
        language = "ipa"
        if selection is not None:
            inventory = Path(directory) / "inventory.txt"
            inventory.write_text("\n".join(selection["tokens"]) + "\n", encoding="utf-8")
            language = str(inventory)
        with wave.open(str(wav), "wb") as stream:
            stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            stream.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        subprocess.run(
            [str(python), "-B", "-X", "utf8", "-m", "allosaurus.run", "--model", "uni2005",
             "-i", str(wav), "--lang", language, "--timestamp=True", "--topk=1", "--output", str(output)],
            check=True, capture_output=True,
        )
        tokens, times = [], []
        for line in output.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            match = re.match(r"^(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\S+)", line)
            if not match:
                raise ValueError(f"Неизвестная строка таймстампов Allosaurus: {line[:100]}")
            times.append(float(match[1]))
            tokens.append(match[3])
    return [(0, len(samples) / SAMPLE_RATE, tokens)], times


def render_report(source, wav, config, duration, blocks, pause_result=None, token_times=None, selection=None):
    tokens = [phone for _, _, phones in blocks for phone in phones]
    mode = f"Окна: {CHUNK_SECONDS} с, контекст до {CONTEXT_SECONDS} с с каждой стороны"
    if config["label"] == "Allosaurus":
        inventory_name = "ограниченный набор" if selection else "универсальный IPA"
        mode = f"Режим: {inventory_name} Allosaurus, вся запись"

    lines = [
        f"ФОНЕТИЧЕСКАЯ ТРАНСКРИПЦИЯ — {config['label']}",
        "Формат отчёта: 2",
        f"Файл: {source}", f"WAV: {wav}",
        f"Модель: {config['repository']}", f"Ревизия: {config['revision']}",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Длительность: {duration:.3f} с",
        "Обработка: локально, CPU, моно 16 кГц в памяти; исходный WAV не изменён",
        mode,
        ("Выход: фонетические метки модели, ограниченные профилем, без словаря слов" if selection
         else "Выход: фонетические метки модели, без словаря слов и подсказки языка"),
        "Язык и перевод не установлены; фонетическая точность требует проверки",
        "Метки и диакритики сохранены; ▁ в ZIPA — метка границы из словаря модели",
    ]
    if selection is not None:
        lines.extend(profile_report_lines(selection))
    if pause_result is not None:
        pause_config = pause_result["config"]
        pauses = pause_result["pauses"]
        lines.extend([
            f"Паузы: Silero VAD @ {pause_config['revision']}; шаг 0.032 с",
            f"Пороги пауз: короткая от {pause_config['short_seconds']:g} с; "
            f"средняя от {pause_config['medium_seconds']:g} с; длинная от {pause_config['long_seconds']:g} с",
            "Паузы определены по аудио; место вставки между метками приблизительное",
            "Начальная и конечная паузы также показаны; метки моделей не удаляются",
        ])
        if not pause_result["speech_detected"]:
            lines.append("Речь детектором не обнаружена; это не доказательство отсутствия речи")
        disputed = sum(any(pause["start"] <= time < pause["end"] for pause in pauses) for time in token_times)
        if disputed:
            lines.append(f"Фонетических меток внутри интервалов пауз: {disputed}; сохранены для проверки на слух")
        lines.extend([
            "", "=== ТРАНСКРИПЦИЯ С ПАУЗАМИ ===",
            render_with_pauses(tokens, token_times, pauses) or "(нет фонетических меток)",
        ])
        if not pauses:
            lines.append("Паузы заданной длительности детектором не найдены")
    if not tokens:
        lines.insert(2, "Фонетические метки не выданы; это не доказательство тишины")
    lines.extend([
        "", "=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===",
        " ".join(tokens),
        "", "=== ЧАСТИ ЗАПИСИ ===",
        "Время обозначает окна обработки, а не границы слов или отдельных фонем",
    ])
    for start, end, phones in blocks:
        lines.extend([f"[{start:.2f}–{end:.2f} с]", " ".join(phones), ""])
    return "\n".join(lines) + "\n"


def save_report(source, label, text, profile_id=None):
    """Создаёт TXT без перезаписи, оставляя имя модели в конце названия."""
    stem = source.stem + ("_" + profile_id if profile_id else "")
    while True:
        output = source.with_name(f"{stem} ({label}).txt")
        try:
            stream = output.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            stem += "_NEW"
            continue

        try:
            with stream:
                stream.write(text)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return output


def validate_installation(directory, config):
    receipt = directory / "installation.json"
    if not receipt.is_file():
        raise ValueError("Не найдена квитанция установки; выполните Setup-PhoneticEngines.ps1")
    installed = json.loads(receipt.read_text(encoding="utf-8"))
    if installed.get("revision") != config["revision"] or installed.get("repository") != config["repository"]:
        raise ValueError("Ревизия модели отличается от engines.json; повторите установку")
    for name in config["files"]:
        if not (directory / name).is_file():
            raise ValueError(f"Не найден файл модели: {directory / name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("zipa", "w2v2", "allosaurus"), required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--profile", help="Идентификатор экспериментального набора из profiles/*.json")
    parser.add_argument("--allosaurus-python", type=Path, default=Path.home() / "allosaurus-env" / "Scripts" / "python.exe")
    args = parser.parse_args()

    profile = load_profile(args.profile) if args.profile else None
    config = load_config(args.engine)
    model_directory = args.runtime / args.engine / "model"
    if args.engine == "allosaurus":
        result = subprocess.run(
            [str(args.allosaurus_python), "-B", "-X", "utf8", "-c",
             "import importlib.metadata; print(importlib.metadata.version('allosaurus'))"],
            check=True, capture_output=True,
        )
        if result.stdout.decode("utf-8").strip() != "1.0.2":
            raise ValueError("Для сравнения требуется установленный Allosaurus 1.0.2")
    else:
        validate_installation(model_directory, config)
    print(f"Загрузка {config['label']} на CPU...", flush=True)
    recognizer = None if args.engine == "allosaurus" else Recognizer(args.engine, model_directory)
    selection = None
    if profile is not None:
        if args.engine == "allosaurus":
            inventory_result = subprocess.run(
                [str(args.allosaurus_python), "-B", "-X", "utf8", "-m", "allosaurus.bin.list_phone",
                 "--model", "uni2005", "--lang", "ipa"], check=True, capture_output=True,
            )
            phones = inventory_result.stdout.decode("utf-8").split()
            vocabulary = {index: token for index, token in enumerate(["<blk>"] + phones)}
            selection = resolve_profile(profile, args.engine, vocabulary)
        else:
            selection = resolve_profile(profile, args.engine, recognizer.vocabulary, recognizer.blank_id)
            recognizer.blocked_ids = selection["blocked_ids"]
        print("\n".join(profile_report_lines(selection)), flush=True)
    detector = PauseDetector(args.runtime)
    if args.check:
        import numpy as np

        # Короткий сигнал проверяет вычисления модели, а не только чтение её файлов
        samples = np.zeros(SAMPLE_RATE, dtype=np.float32)
        if args.engine == "allosaurus":
            transcribe_allosaurus(samples, args.allosaurus_python, selection)
        else:
            recognizer.predict(samples)
        detector.detect(samples)
        print(f"Проверка пройдена: {config['repository']} @ {config['revision']}", flush=True)
        return 0
    if args.manifest is None:
        parser.error("Укажите --manifest или --check")

    paths = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
        raise ValueError("Ожидался непустой список путей аудио")

    failures = 0
    for name in dict.fromkeys(paths):
        source = Path(name)
        try:
            source = source.resolve(strict=True)
            if not source.is_file():
                raise ValueError("Ожидался аудиофайл")
            print(f"Выбрана запись: {source}", flush=True)
            wav = prepare_wav(source, args.ffmpeg)
            print(f"Используется WAV: {wav}", flush=True)
            samples = read_audio(wav, args.ffmpeg)
            pause_result = detector.detect(samples)
            token_times = []
            if args.engine == "allosaurus":
                blocks, token_times = transcribe_allosaurus(samples, args.allosaurus_python, selection)
            else:
                blocks = transcribe(samples, recognizer, token_times)
            if selection is not None:
                allowed = set(selection["tokens"]) | ({"▁"} if args.engine == "zipa" else set())
                if any(token not in allowed for _, _, phones in blocks for token in phones):
                    raise ValueError("Модель выдала метку вне выбранного профиля")
            report = render_report(source, wav, config, len(samples) / SAMPLE_RATE, blocks, pause_result, token_times, selection)
            output = save_report(source, config["label"], report, args.profile)
            print(f"Готово: {output}", flush=True)
            print("__GLOSSOLALIA_SAVED__", flush=True)
        except Exception as error:
            failures += 1
            if isinstance(error, subprocess.CalledProcessError):
                detail = error.stderr.decode("utf-8", errors="replace").strip()
            else:
                detail = str(error)
            print(f"Ошибка файла {source}: {detail}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
