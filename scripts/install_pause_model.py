"""Загружает закреплённую модель пауз, проверяя TLS и SHA-256 до установки."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((Path(__file__).resolve().parents[1] / "pauses.json").read_text(encoding="utf-8"))
    directory = args.runtime / "vad"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "silero_vad.jit"
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == config["sha256"]:
        print(f"Модель пауз уже установлена: {target}")
        return

    import truststore

    truststore.inject_into_ssl()
    handle, name = tempfile.mkstemp(prefix=".silero-", suffix=".jit", dir=directory)
    os.close(handle)
    temporary = Path(name)
    try:
        with urllib.request.urlopen(config["url"], timeout=60) as response, temporary.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != config["sha256"]:
            raise ValueError("SHA-256 загрузки Silero VAD не совпал с pauses.json")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Модель пауз установлена: {target}")


if __name__ == "__main__":
    main()
