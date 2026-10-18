"""Download the pinned all-MiniLM-L6-v2 ONNX export and tokenizer into ./model.

Runs as the Render build step so the model ships with the deploy instead of
downloading on every cold start. Standard library only.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = ("onnx/model.onnx", "tokenizer.json")
MODEL_DIR = Path(__file__).parent / "model"


def fetch(filename: str) -> Path:
    target = MODEL_DIR / Path(filename).name
    if target.exists():
        return target
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{filename}"
    MODEL_DIR.mkdir(exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    urllib.request.urlretrieve(url, partial)
    partial.replace(target)
    return target


def main() -> None:
    for filename in FILES:
        path = fetch(filename)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{path.name}: {path.stat().st_size} bytes, sha256 {digest}")


if __name__ == "__main__":
    sys.exit(main())
