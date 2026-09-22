"""Локальная пакетная фонетическая транскрипция ZIPA и Wav2Vec2Phoneme."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SAMPLE_RATE = 16000
CHUNK_SECONDS = 30
CONTEXT_SECONDS = 2


def load_config(engine):
    project = Path(__file__).resolve().parents[1]
    return json.loads((project / "engines.json").read_text(encoding="utf-8"))[engine]


def decode_ctc(ids, vocabulary, blank_id=0, previous=None):
    """Удаляет CTC-пустоты и соседние повторы, сохраняя повтор после пустоты."""
    tokens = []
    for item in ids:
        item = int(item)
        if item != previous and item != blank_id:
            token = vocabulary[item]
            if token not in ("<s>", "</s>", "<pad>", "<sos/eos>"):
                tokens.append(token)
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
            return scores.argmax(axis=-1)

        inputs = self.extractor(samples, sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.inference_mode():
            return self.model(**inputs).logits[0].argmax(dim=-1).cpu().numpy()


def transcribe(samples, recognizer):
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
        phones, previous = decode_ctc(
            ids[first:last], recognizer.vocabulary, recognizer.blank_id, previous,
        )
        blocks.append((start / SAMPLE_RATE, end / SAMPLE_RATE, phones))
    return blocks


def render_report(source, wav, config, duration, blocks):
    tokens = [phone for _, _, phones in blocks for phone in phones]
    lines = [
        f"ФОНЕТИЧЕСКАЯ ТРАНСКРИПЦИЯ — {config['label']}",
        f"Файл: {source}", f"WAV: {wav}",
        f"Модель: {config['repository']}", f"Ревизия: {config['revision']}",
        f"Дата: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Длительность: {duration:.3f} с",
        "Обработка: локально, CPU, моно 16 кГц в памяти; исходный WAV не изменён",
        f"Окна: {CHUNK_SECONDS} с, контекст до {CONTEXT_SECONDS} с с каждой стороны",
        "Выход: фонетические метки модели, без словаря слов и подсказки языка",
        "Язык и перевод не установлены; фонетическая точность требует проверки",
        "Метки и диакритики сохранены; ▁ в ZIPA — метка границы из словаря модели",
        "", "=== ПОЛНАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ===",
        " ".join(tokens) if tokens else "Фонетические метки не выданы; это не доказательство тишины",
        "", "=== ЧАСТИ ЗАПИСИ ===",
        "Время обозначает окна обработки, а не границы слов или отдельных фонем",
    ]
    for start, end, phones in blocks:
        lines.extend([f"[{start:.2f}–{end:.2f} с]", " ".join(phones) or "(нет меток)", ""])
    return "\n".join(lines) + "\n"


def save_report(source, label, text):
    """Создаёт TXT без перезаписи, оставляя имя модели в конце названия."""
    stem = source.stem
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
    parser.add_argument("--engine", choices=("zipa", "w2v2"), required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    config = load_config(args.engine)
    model_directory = args.runtime / args.engine / "model"
    validate_installation(model_directory, config)
    print(f"Загрузка {config['label']} на CPU...", flush=True)
    recognizer = Recognizer(args.engine, model_directory)
    if args.check:
        import numpy as np

        # Короткий сигнал проверяет вычисления модели, а не только чтение её файлов
        recognizer.predict(np.zeros(SAMPLE_RATE, dtype=np.float32))
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
            blocks = transcribe(samples, recognizer)
            report = render_report(source, wav, config, len(samples) / SAMPLE_RATE, blocks)
            output = save_report(source, config["label"], report)
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
