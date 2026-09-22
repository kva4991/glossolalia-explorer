"""Загружает закреплённую ревизию модели и фиксирует состав локальной установки."""

import argparse
import hashlib
import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("zipa", "w2v2"), required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()

    # Системное хранилище Windows учитывает доверенные сертификаты без отключения TLS
    import truststore

    truststore.inject_into_ssl()
    from huggingface_hub import hf_hub_download

    project = Path(__file__).resolve().parents[1]
    config = json.loads((project / "engines.json").read_text(encoding="utf-8"))[args.engine]
    directory = args.runtime / args.engine / "model"
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in config["files"]:
        print(f"Загрузка {config['label']}: {name}", flush=True)
        path = hf_hub_download(
            repo_id=config["repository"], filename=name, revision=config["revision"],
            local_dir=directory, token=False,
        )
        with open(path, "rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()

    receipt = {
        "engine": args.engine,
        "repository": config["repository"],
        "revision": config["revision"],
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashes,
        "packages": {item.metadata["Name"]: item.version for item in importlib.metadata.distributions()},
    }
    (directory / "installation.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Модель сохранена: {directory}", flush=True)


if __name__ == "__main__":
    main()
