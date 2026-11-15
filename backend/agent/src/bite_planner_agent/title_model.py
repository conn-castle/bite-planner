"""Pinned, checksum-verified MiniLM assets. Fetch only during setup/build, never inference."""

import hashlib
import sys
from pathlib import Path
from urllib.request import urlopen

MODEL_ENV: str = "BITE_PLANNER_TITLE_MODEL_DIR"
MODEL_DIR: Path = Path("/opt/bite-planner-models/minilm")
REVISION: str = "d13954661f83248295ba75c1ed411eef3b7b936e"
ASSETS: dict[str, str] = {
    "config.json": "1b4d8e2a3988377ed8b519a31d8d31025a25f1c5f8606998e8014111438efcd7",
    "model.onnx": "bbd7b466f6d58e646fdc2bd5fd67b2f5e93c0b687011bd4548c420f7bd46f0c5",
    "special_tokens_map.json": "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
    "tokenizer.json": "851ca67100d372ca3ae031a6abd168f53489eebfd7d89523f35c5c9b4d372c3c",
    "tokenizer_config.json": "abda01c8c14c5151ae498aceb30db406d6b91242c394fd88d3b6fd5a63a101e6",
}


class TitleModelError(RuntimeError):
    """A required local asset or inference dependency is unavailable."""


def verify(directory: Path) -> None:
    for name, expected in ASSETS.items():
        path: Path = directory / name
        try:
            with path.open("rb") as stream:
                digest: str = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as error:
            raise TitleModelError(
                f"Cannot read meal title model asset {path}. Run the pinned model setup "
                "(make title-model), or rebuild the worker image."
            ) from error
        if digest != expected:
            raise TitleModelError(
                f"Meal title model checksum mismatch: {path}. Run make title-model "
                "to repair local assets, or rebuild the worker image."
            )


def fetch(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in ASSETS.items():
        path: Path = directory / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        url: str = f"https://huggingface.co/Qdrant/all-MiniLM-L6-v2-onnx/resolve/{REVISION}/{name}"
        with urlopen(url, timeout=120) as response:
            data: bytes = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise TitleModelError(f"Pinned model download checksum mismatch: {url}")
        path.write_bytes(data)
        path.chmod(0o644)
    verify(directory)


if __name__ == "__main__":
    fetch(Path(sys.argv[1]))
