"""One offline, title-only comparison rule for every meal novelty boundary.

Titles match at cosine >= 0.80 or when their nonempty normalized names match.
No recipe facts, runtime downloads, or alternative models.
Imports remain lazy so the API needs neither inference dependencies nor assets.
"""

import os
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from bite_planner_agent.names import key
from bite_planner_agent.title_model import MODEL_DIR, MODEL_ENV, TitleModelError, verify

if TYPE_CHECKING:
    import numpy as np
    import onnxruntime as ort
    from numpy.typing import NDArray
    from tokenizers import Tokenizer

CUTOFF: float = 0.80


class TitleEncoder:
    def __init__(self, directory: Path) -> None:
        verify(directory)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise TitleModelError(
                "Meal title inference dependencies are missing. Install the agent's "
                "meal-similarity extra (make deps), or rebuild the worker image."
            ) from error
        try:
            ort.disable_telemetry_events()
            options: ort.SessionOptions = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            self.session: ort.InferenceSession = ort.InferenceSession(
                str(directory / "model.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self.tokenizer: Tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
            self.tokenizer.enable_truncation(max_length=256)
            self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            self.vectors: OrderedDict[str, NDArray[np.float32]] = OrderedDict()
        except Exception as error:
            raise TitleModelError(
                f"Cannot load meal title model from {directory}: {error}"
            ) from error

    def vector(self, title: str) -> NDArray[np.float32]:
        import numpy as np

        if title in self.vectors:
            return self.vectors[title]
        tokens = self.tokenizer.encode_batch([title])
        inputs: dict[str, NDArray[np.int64]] = {
            "input_ids": np.asarray([row.ids for row in tokens], dtype=np.int64),
            "attention_mask": np.asarray([row.attention_mask for row in tokens], dtype=np.int64),
            "token_type_ids": np.asarray([row.type_ids for row in tokens], dtype=np.int64),
        }
        try:
            hidden: NDArray[np.float32] = np.asarray(
                self.session.run(
                    None, {item.name: inputs[item.name] for item in self.session.get_inputs()}
                )[0],
                dtype=np.float32,
            )
            mask: NDArray[np.float32] = inputs["attention_mask"][..., None].astype(np.float32)
            pooled: NDArray[np.float32] = (hidden * mask).sum(axis=1) / mask.sum(axis=1)
            vector: NDArray[np.float32] = (pooled / np.linalg.norm(pooled, axis=1, keepdims=True))[
                0
            ]
            if not np.isfinite(vector).all():
                raise ValueError("nonfinite title embedding")
            self.vectors[title] = vector
            if len(self.vectors) > 2048:
                self.vectors.popitem(last=False)
            return vector
        except Exception as error:
            raise TitleModelError(f"Meal title inference failed for {title!r}: {error}") from error

    def matches(self, left: str, right: str) -> bool:
        normalized: str = key(left)
        return (bool(normalized) and normalized == key(right)) or (
            float(self.vector(left) @ self.vector(right)) >= CUTOFF
        )


@lru_cache(maxsize=1)
def _load(directory: Path) -> TitleEncoder:
    return TitleEncoder(directory)


def encoder() -> TitleEncoder:
    return _load(Path(os.environ.get(MODEL_ENV, str(MODEL_DIR))))
