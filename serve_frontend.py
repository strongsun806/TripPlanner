"""화면 서버 (5500) — 로컬 개발용. 배포에서는 FastAPI 가 화면까지 함께 보여 주므로 쓰지 않는다

화면 서버 (5500) — python -m http.server 와 같지만 브라우저가 옛 파일을 붙잡지 않게 캐시를 끈다.

    python serve_frontend.py        →  http://localhost:5500
"""
import functools
import http.server
from pathlib import Path

PORT = 5500
ROOT = Path(__file__).resolve().parent / "frontend"


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCache, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("", PORT), handler) as httpd:
        print(f"화면: http://localhost:{PORT}  (끄려면 Ctrl+C)")
        httpd.serve_forever()
