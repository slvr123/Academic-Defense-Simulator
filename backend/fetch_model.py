"""Download the pinned all-MiniLM-L6-v2 ONNX export and tokenizer, verifying sha256.

Runs as the Render build step (`python -m backend.fetch_model`) so the model ships
with the deploy instead of downloading on every cold start. Standard library only.
Revision and hashes are the ones the hosting spike measured
(docs/v2.0-hosting-spike-results.md).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

from backend.settings import Settings

REPO = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = {
    "onnx/model.onnx": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    "tokenizer.json": "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037",
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(filename: str, expected_sha256: str, model_dir: Path) -> Path:
    target = model_dir / Path(filename).name
    if target.exists() and sha256_of(target) == expected_sha256:
        return target
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{filename}"
    model_dir.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    urllib.request.urlretrieve(url, partial)
    actual = sha256_of(partial)
    if actual != expected_sha256:
        partial.unlink()
        raise RuntimeError(f"{filename}: sha256 {actual} does not match pinned {expected_sha256}")
    partial.replace(target)
    return target


def main() -> int:
    model_dir = Settings().model_dir
    for filename, expected in FILES.items():
        path = fetch(filename, expected, model_dir)
        print(f"{path.name}: {path.stat().st_size} bytes, sha256 verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
