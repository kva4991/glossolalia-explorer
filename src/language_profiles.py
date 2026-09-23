"""Проверяет пользовательские наборы и ограничивает метки до CTC-декодирования."""

import hashlib
import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ENGINES = ("allosaurus", "zipa", "w2v2")
SERVICE_TOKENS = {"<blk>", "<pad>", "<s>", "</s>", "<sos/eos>", "<unk>", "▁"}


def valid_tokens(tokens):
    return (isinstance(tokens, list) and bool(tokens)
            and all(isinstance(token, str) and token and not any(char.isspace() for char in token)
                    and token not in SERVICE_TOKENS for token in tokens)
            and len(tokens) == len(set(tokens)))


def load_profile(identifier):
    """Читает только JSON указанного профиля внутри каталога проекта."""
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
        raise ValueError("Некорректный идентификатор профиля")
    path = PROJECT / "profiles" / (identifier + ".json")
    raw = path.read_bytes()
    profile = json.loads(raw.decode("utf-8"))
    if (profile.get("schema_version") != 1 or profile.get("id") != identifier
            or profile.get("status") != "experimental-unofficial"
            or type(profile.get("version")) is not int or profile["version"] < 1
            or not isinstance(profile.get("name"), str) or not profile["name"].strip()
            or "\n" in profile["name"] or "\r" in profile["name"]
            or not valid_tokens(profile.get("phones"))):
        raise ValueError("Некорректная структура экспериментального профиля")

    aliases = profile.get("aliases", {})
    tokenizations = profile.get("tokenizations", {})
    if not isinstance(aliases, dict) or not isinstance(tokenizations, dict):
        raise ValueError("Некорректные варианты меток профиля")
    mappings = [aliases]
    for engine, mapping in tokenizations.items():
        if engine not in ENGINES or not isinstance(mapping, dict):
            raise ValueError("Неизвестный движок в профиле")
        mappings.append(mapping)
    for mapping in mappings:
        if any(phone not in profile["phones"] or not valid_tokens(tokens) for phone, tokens in mapping.items()):
            raise ValueError("Некорректное представление фонемы в профиле")
    profile.update(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    return profile


def resolve_profile(profile, engine, vocabulary, blank_id=0):
    """Сверяет точные метки, явные варианты записи и доступность составных представлений."""
    if engine not in ENGINES or blank_id not in vocabulary:
        raise ValueError("Неизвестный движок или отсутствует CTC-пустота")
    available = set(vocabulary.values()) - SERVICE_TOKENS
    supported, unsupported, conversions, tokens = [], [], [], []
    for phone in profile["phones"]:
        matches = [token for token in [phone] + profile.get("aliases", {}).get(phone, []) if token in available]
        if not matches:
            pieces = profile.get("tokenizations", {}).get(engine, {}).get(phone, [])
            if pieces and all(token in available for token in pieces):
                matches = pieces
        if not matches:
            unsupported.append(phone)
            continue
        supported.append(phone)
        if matches != [phone]:
            conversions.append(dict(phone=phone, tokens=matches))
        for token in matches:
            if token not in tokens:
                tokens.append(token)
    if not tokens:
        raise ValueError("Ни одна метка профиля не поддерживается моделью")

    # CTC-пустота и граница ZIPA сохраняют служебную роль при ограничении звуков
    allowed = {blank_id}
    allowed.update(index for index, token in vocabulary.items() if token in tokens or (engine == "zipa" and token == "▁"))
    return dict(profile=profile, engine=engine, supported=supported, unsupported=unsupported,
                conversions=conversions, tokens=tokens, allowed_ids=sorted(allowed),
                blocked_ids=sorted(set(vocabulary) - allowed))


def select_token_ids(scores, blocked_ids):
    """Отключает запрещённые метки перед argmax, не удаляя их из готовой транскрипции."""
    if blocked_ids:
        scores[:, blocked_ids] = float("-inf")
    return scores.argmax(axis=-1)


def profile_report_lines(selection):
    profile = selection["profile"]
    conversions = "; ".join(item["phone"] + " → " + " ".join(item["tokens"]) for item in selection["conversions"])
    return [
        f"Профиль распознавания: {profile['id']}",
        f"Название профиля: {profile['name']}; версия {profile['version']}",
        "ЭКСПЕРИМЕНТАЛЬНЫЙ, НЕОФИЦИАЛЬНЫЙ ПРОФИЛЬ",
        f"Файл профиля: {profile['path']}", f"SHA-256 профиля: {profile['sha256']}",
        f"Форм в профиле: {len(profile['phones'])}; доступно представлений: {len(selection['supported'])}",
        "Не поддерживаются без замены: " + (" ".join(selection["unsupported"]) or "нет"),
        "Варианты и составные представления: " + (conversions or "не требуются"),
        "Разрешённые фонетические метки модели: " + " ".join(selection["tokens"]),
        "Ограничение применено до выбора меток; ожидаемые слова и правила замены не применяются",
        "Составные представления разрешают отдельные метки, но не задают их порядок или позицию",
        "Результат обусловлен выбранным набором; совпадение с ним не подтверждает язык записи",
    ]
