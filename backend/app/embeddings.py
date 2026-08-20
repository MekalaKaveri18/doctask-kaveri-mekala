from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


def hash_embedding(text: str, dims: int = 64) -> list[float]:
    vec = [0.0] * dims
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.sha256(tok.encode()).digest()
        for i in range(dims):
            vec[i] += digest[i % 32] / 255.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def dump_vec(v: list[float]) -> str:
    return json.dumps(v)


def load_vec(s: str) -> list[float]:
    return json.loads(s or "[]")


def retrieve(chunks: list[Any], query: str, k: int = 5) -> list[Any]:
    q = hash_embedding(query)
    scored = []
    for ch in chunks:
        vec = load_vec(ch.embedding) if hasattr(ch, "embedding") else hash_embedding(ch.text)
        scored.append((cosine(q, vec), ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:k]]
