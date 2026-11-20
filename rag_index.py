"""여행 문서 색인 만들기 (심화 F3022) — 2회차 TripPilot 의 메타데이터 색인 파이프라인을 이식했다

실행 (TripPlanner 폴더에서):
    python rag_index.py              data/rag/travel_metadata.json(500건) → data/chroma 의 travel_meta 컬렉션
    python rag_index.py --check      색인 상태와 시험 검색 한 번

2회차 폴더에 이미 만든 chroma_db 가 있으면 색인을 다시 만들 필요 없이 .env 에
    RAG_DB_DIR=C:/Users/SSAFY/Desktop/TripPilot/chroma_db
를 넣으면 travel_meta 와 travel_wiki(위키백과 청크)를 함께 검색한다.
"""
import sys
from pathlib import Path

from app import rag

SOURCE = Path(__file__).resolve().parent / "data" / "rag" / "travel_metadata.json"


def check():
    cols = rag.available_collections()
    print(f"색인 폴더: {rag.RAG_DB_DIR}")
    if not cols:
        print("색인 없음 → search_travel_docs 는 NO_INDEX 로 답합니다.")
        return
    for name, n in cols:
        print(f"  {name}: {n}건")
    res = rag.search("가을에 가기 좋은 일본 미식 여행지", 3)
    print(f"시험 검색 (임베딩: {res['embedding']})")
    for h in res["hits"]:
        print(f"  {h['distance']:.3f}  {h['title']}  — {h['source']}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check()
    else:
        n, how = rag.index_metadata(SOURCE)
        print(f"travel_meta 색인 완료: {n}건 (임베딩: {how})")
        if how == "fallback":
            from app import rag as _rag
            print("※ 임베딩 호출이 실패해 해시 벡터로 색인했습니다 (TripPilot 과 같은 대체 방식, 검색은 그대로 동작).")
            print("   이유:", _rag.LAST_EMBED_ERROR or "알 수 없음")
            print("   GMS 의 OpenAI 쪽 사용량이 다 된 경우 이렇게 됩니다. Claude 는 임베딩 API 가 없어 대신 쓸 수 없습니다.")
        check()
