"""Находит паузы по аудио и вставляет их в фонетическую последовательность."""

import hashlib
import json
import math
from pathlib import Path

SAMPLE_RATE = 16000
FRAME_SIZE = 512


def load_pause_config():
    path = Path(__file__).resolve().parents[1] / "pauses.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    durations = [config[key] for key in ("short_seconds", "medium_seconds", "long_seconds")]
    if not all(math.isfinite(value) for value in durations) or not 0 < durations[0] < durations[1] < durations[2]:
        raise ValueError("Пороги пауз должны возрастать: 0 < короткая < средняя < длинная")
    if not 0 <= config["release_threshold"] < config["speech_threshold"] <= 1:
        raise ValueError("Некорректные пороги речевой активности")
    return config


def classify_pause(duration, config):
    if duration < config["short_seconds"]:
        return None
    if duration < config["medium_seconds"]:
        return "КОРОТКАЯ"
    if duration < config["long_seconds"]:
        return "СРЕДНЯЯ"
    return "ДЛИННАЯ"


def pauses_from_probabilities(probabilities, sample_count, config):
    expected = (sample_count + FRAME_SIZE - 1) // FRAME_SIZE
    if len(probabilities) != expected or sample_count <= 0:
        raise ValueError("Число кадров VAD не совпало с длиной аудио")

    speaking = False
    silence_start = 0
    pauses = []
    speech_detected = False
    for index, probability in enumerate(probabilities):
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("VAD вернул некорректную вероятность")
        start = index * FRAME_SIZE
        if not speaking and probability >= config["speech_threshold"]:
            duration = (start - silence_start) / SAMPLE_RATE
            kind = classify_pause(duration, config)
            if kind:
                pauses.append(dict(start=silence_start / SAMPLE_RATE, end=start / SAMPLE_RATE, duration=duration, kind=kind))
            speaking = True
            speech_detected = True
        elif speaking and probability < config["release_threshold"]:
            speaking = False
            silence_start = start

    if not speaking:
        duration = (sample_count - silence_start) / SAMPLE_RATE
        kind = classify_pause(duration, config)
        if kind:
            pauses.append(dict(start=silence_start / SAMPLE_RATE, end=sample_count / SAMPLE_RATE, duration=duration, kind=kind))
    return pauses, speech_detected


class PauseDetector:
    """Исполняет закреплённую Silero VAD локально, со сбросом состояния для каждого файла."""

    def __init__(self, runtime):
        self.config = load_pause_config()
        path = Path(runtime) / "vad" / "silero_vad.jit"
        if not path.is_file():
            raise ValueError("Модель пауз не установлена: выполните Setup-Pauses.ps1")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != self.config["sha256"]:
            raise ValueError("SHA-256 модели пауз не совпал с pauses.json; повторите Setup-Pauses.ps1")

        import torch

        self.model = torch.jit.load(str(path), map_location="cpu").eval()

    def detect(self, samples):
        import torch

        self.model.reset_states()
        probabilities = []
        with torch.inference_mode():
            for start in range(0, len(samples), FRAME_SIZE):
                chunk = torch.as_tensor(samples[start:start + FRAME_SIZE], dtype=torch.float32)
                if len(chunk) < FRAME_SIZE:
                    chunk = torch.nn.functional.pad(chunk, (0, FRAME_SIZE - len(chunk)))
                probabilities.append(float(self.model(chunk, SAMPLE_RATE).item()))
        pauses, speech_detected = pauses_from_probabilities(probabilities, len(samples), self.config)
        return dict(pauses=pauses, speech_detected=speech_detected, config=self.config)


def render_with_pauses(tokens, times, pauses):
    if len(tokens) != len(times) or any(not math.isfinite(time) for time in times):
        raise ValueError("Фонетические метки и их время не согласованы")
    if times != sorted(times):
        raise ValueError("Временные метки фонем идут не по порядку")

    parts = []
    position = 0
    for pause in pauses:
        # Середина интервала задаёт приблизительное место вставки, не удаляя спорные метки модели
        middle = (pause["start"] + pause["end"]) / 2
        while position < len(tokens) and times[position] < middle:
            parts.append(tokens[position])
            position += 1
        parts.append(f"\n[{pause['kind']} ПАУЗА: {pause['duration']:.3f} с; "
                     f"{pause['start']:.3f}–{pause['end']:.3f} с]\n")
    parts.extend(tokens[position:])
    return " ".join(parts).strip()
