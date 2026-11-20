"""여행 문서 검색 — 2회차 TripPilot 의 Vector DB(ChromaDB)를 읽는다 (심화 F3022)

컬렉션 (TripPilot 과 같은 이름 · 같은 메타데이터)
    travel_meta   여행지 메타데이터 500건 — 국가 · 테마 · 예산 · 베스트 시즌 · 요약        ids: TRIP_{id}
    travel_wiki   한국어 위키백과 청크 — doc_title · source_url · source_type             (TripPilot 에서 수집한 경우)

임베딩도 TripPilot 과 같게 맞춘다: text-embedding-3-small (GMS) → 실패하면 같은 1536차원 해시 벡터.
색인과 질의의 임베딩 방식이 다르면 검색 품질이 떨어지므로, 결과에 어떤 임베딩을 썼는지 함께 돌려준다.

    RAG_DB_DIR   ChromaDB 폴더. 기본 TripPlanner/data/chroma
                 2회차 폴더의 chroma_db 를 그대로 가리켜도 된다 (예: C:/Users/SSAFY/Desktop/TripPilot/chroma_db)
"""
import hashlib
import math
import os
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
RAG_DB_DIR = Path(os.getenv("RAG_DB_DIR") or ROOT / "data" / "chroma")
COLLECTIONS = ["travel_meta", "travel_wiki"]
EMBEDDING_MODEL = "text-embedding-3-small"
DIM = 1536


def fallback_vector(text: str, dim: int = DIM) -> list[float]:
    """TripPilot 의 TripPilotEmbeddings._fallback_vector 와 같은 계산 — 키가 없어도 같은 벡터가 나온다"""
    vec = [0.0] * dim
    clean = text.lower().strip()
    tokens = clean.split()
    features = list(tokens) + [clean[i:i + 3] for i in range(max(1, len(clean) - 2))]
    for feat in features:
        h = hashlib.sha256(feat.encode("utf-8")).digest()
        for j in range(0, 28, 4):
            idx = int.from_bytes(h[j:j + 2], "little") % dim
            sign = 1.0 if (h[j + 2] & 1) == 0 else -1.0
            weight = 1.0 + (h[j + 3] / 255.0)
            vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


LAST_EMBED_ERROR = ""   # 임베딩이 해시 벡터로 넘어간 이유 (키는 가려서 남김)


def embed(texts: list[str]) -> tuple[list[list[float]], str]:
    global LAST_EMBED_ERROR
    if not config.GMS_KEY:
        LAST_EMBED_ERROR = "GMS_KEY 없음"
    else:
        try:
            from langchain_openai import OpenAIEmbeddings
            e = OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=config.GMS_KEY, base_url=config.GMS_EMBED_BASE_URL,
                                 max_retries=1)
            return e.embed_documents(texts), "openai"
        except Exception as ex:
            from tools import scrub
            LAST_EMBED_ERROR = f"{type(ex).__name__}: {scrub(str(ex))[:200]}"
    return [fallback_vector(t) for t in texts], "fallback"


def client():
    import chromadb
    return chromadb.PersistentClient(path=str(RAG_DB_DIR))


def available_collections():
    """색인이 있는 컬렉션 이름과 건수. 폴더나 chromadb 가 없으면 빈 목록"""
    if not RAG_DB_DIR.exists():
        return []
    try:
        c = client()
        names = {col.name if hasattr(col, "name") else col for col in c.list_collections()}
        out = []
        for n in COLLECTIONS:
            if n in names:
                cnt = c.get_collection(n).count()
                if cnt:
                    out.append((n, cnt))
        return out
    except Exception:
        return []


def search(query: str, k: int = 4) -> dict:
    cols = available_collections()
    if not cols:
        return {"indexed": False}
    (qvec,), how = embed([query])
    c = client()
    hits = []
    for name, _ in cols:
        res = c.get_collection(name).query(query_embeddings=[qvec], n_results=k,
                                           include=["documents", "metadatas", "distances"])
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            meta = meta or {}
            if name == "travel_meta":
                hit = {"title": f'{meta.get("name")} ({meta.get("country")})', "source": "TripPilot 여행지 메타데이터",
                       "url": None}
            else:
                hit = {"title": meta.get("doc_title") or meta.get("dest_name"),
                       "source": meta.get("source_type") or "한국어 위키백과 (CC BY-SA 4.0)",
                       "url": meta.get("source_url")}
            hit.update({"collection": name, "snippet": (doc or "")[:320], "distance": round(float(dist), 4)})
            hits.append(hit)
    hits.sort(key=lambda h: h["distance"])
    return {"indexed": True, "embedding": how, "collections": dict(cols), "hits": hits[:k]}


def index_metadata(json_path: Path) -> int:
    """2회차 파이프라인 이식 — 여행지 메타데이터를 travel_meta 컬렉션에 색인 (TripPilot 과 같은 텍스트 · id 규칙)"""
    import json
    items = json.loads(Path(json_path).read_text(encoding="utf-8"))
    texts = [(f"[여행지 메타데이터] 여행지명: {it['name']} | 국가: {it['country']} | 대륙: {it['continent']} | "
              f"테마: {it['theme']} | 예산 수준: {it['budget_level']} | 추천 여행 시기(베스트 시즌): {it['best_season']} | "
              f"요약 설명: {it['summary']}") for it in items]
    metas = [{"trip_id": str(it["id"]), "name": it["name"], "country": it["country"], "continent": it["continent"],
              "theme": it["theme"], "budget_level": it["budget_level"], "best_season": it["best_season"]} for it in items]
    ids = [f"TRIP_{it['id']}" for it in items]
    RAG_DB_DIR.mkdir(parents=True, exist_ok=True)
    col = client().get_or_create_collection("travel_meta", metadata={"hnsw:space": "cosine"})
    how = None
    for i in range(0, len(texts), 64):
        vecs, how = embed(texts[i:i + 64])
        col.upsert(ids=ids[i:i + 64], documents=texts[i:i + 64], embeddings=vecs, metadatas=metas[i:i + 64])
    return col.count(), how
