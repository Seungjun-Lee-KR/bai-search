"""문장 임베딩(의미 검색용 벡터).

EMBED_BACKEND 환경변수로 고릅니다.
- local  (기본): BAAI/bge-m3 모델을 이 컴퓨터에서 돌립니다. 원문이 외부로 나가지 않아
                 공공기관 보안 측면에서 권장합니다. `pip install -e '.[local]'` 필요.
- voyage       : Voyage AI API(voyage-3.5). 원문이 외부로 전송됩니다. VOYAGE_API_KEY 필요.
- hash         : 모델 없이 글자 n-gram을 해싱한 벡터. 의미 이해는 못 하지만 테스트·오프라인용.
모든 방식이 1024차원이라 DB를 바꾸지 않고 교체할 수 있습니다(단, 교체 후 `bai embed --all` 재실행).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from functools import lru_cache
from typing import Literal, Protocol

DIM = 1024
Kind = Literal["document", "query"]


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], kind: Kind) -> list[list[float]]: ...


class HashEmbedder:
    """글자 2~3-gram 해싱. 같은 단어가 많이 겹칠수록 가까워집니다."""

    name = "hash"

    def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        v = [0.0] * DIM
        s = re.sub(r"\s+", " ", text)
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                g = s[i:i + n]
                if " " in g:
                    continue
                h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "little")
                v[h % DIM] += 1.0 if (h >> 63) == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


class LocalEmbedder:
    name = "local"

    def __init__(self, model: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model or os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
        self.model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        vecs = self.model.encode(texts, normalize_embeddings=True, batch_size=8)
        return [list(map(float, v)) for v in vecs]


class VoyageEmbedder:
    name = "voyage"

    def __init__(self, model: str | None = None):
        import voyageai

        self.client = voyageai.Client()
        self.model_name = model or os.environ.get("EMBED_MODEL", "voyage-3.5")

    def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            r = self.client.embed(texts[i:i + 64], model=self.model_name, input_type=kind)
            out.extend(r.embeddings)
        return out


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    backend = os.environ.get("EMBED_BACKEND", "local")
    if backend == "hash":
        return HashEmbedder()
    if backend == "voyage":
        return VoyageEmbedder()
    if backend == "local":
        return LocalEmbedder()
    raise ValueError(f"알 수 없는 EMBED_BACKEND: {backend}")


def to_pgvector(v: list[float]) -> str:
    if len(v) != DIM:
        raise ValueError(f"임베딩 차원이 {len(v)}입니다. {DIM}차원 모델을 쓰세요.")
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ---------------------------------------------------------------- 청크 분할
CHUNK_CHARS = 700


def chunk_case(title: str, body: str, action_text: str | None = None) -> list[str]:
    """문단 경계를 따라 약 700자씩 자르고, 각 조각 앞에 제목을 붙입니다.
    앞 조각의 마지막 문단을 다음 조각에 한 번 더 넣어 문맥이 끊기지 않게 합니다."""
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        if cur and size + len(p) > CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur = [cur[-1]] if len(cur[-1]) < CHUNK_CHARS // 2 else []
            size = sum(len(x) for x in cur)
        cur.append(p)
        size += len(p)
    if cur:
        chunks.append("\n".join(cur))
    if action_text:
        chunks.append("조치할 사항: " + action_text)
    return [f"제목: {title}\n{c}" for c in chunks]
