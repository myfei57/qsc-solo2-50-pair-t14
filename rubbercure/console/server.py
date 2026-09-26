"""控制台 HTTP 服务。

用标准库 http.server 起多线程服务：GET 走只读接口与页面，POST 走四条工艺
顺序、周期推进与维护动作。启动探测函数会真实发起 HTTP 请求读取四个页面与
状态接口，用它代替"我认为服务起来了"的口头结论。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..app import Platform
from .api import ConsoleAPI
from .pages import PAGE_TITLES, render_page

_JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8"}
_HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    """把请求分发给 :class:`ConsoleAPI` 或页面渲染器。"""

    server_version = "rubbercure-console/1.0"
    api: ConsoleAPI
    platform: Platform

    def log_message(self, format: str, *args: object) -> None:  # pragma: no cover - 由服务开关控制
        if getattr(self.server, "quiet", True):
            return
        super().log_message(format, *args)

    def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, _JSON_HEADERS)

    def _send_html(self, html: str, status: int = 200) -> None:
        self._send(status, html.encode("utf-8"), _HTML_HEADERS)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - http.server 回调命名
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/status":
                self._send_json(self.api.status())
            elif parsed.path == "/api/trend":
                series = (query.get("series") or [None])[0]
                window = float((query.get("window_s") or ["600"])[0])
                self._send_json(self.api.trend(series=series, window_s=window))
            elif parsed.path == "/api/alarms":
                self._send_json(self.api.alarms())
            elif parsed.path == "/api/audit":
                limit = int((query.get("limit") or ["100"])[0])
                self._send_json(self.api.audit(limit=limit))
            elif parsed.path == "/api/journal":
                raw_segment = (query.get("segment") or [None])[0]
                segment = int(raw_segment) if raw_segment is not None else None
                limit = int((query.get("limit") or ["200"])[0])
                self._send_json(self.api.journal(segment=segment, limit=limit))
            elif parsed.path == "/api/readiness":
                self._send_json(self.api.readiness())
            elif parsed.path == "/":
                self._send_html(render_page("press", self.platform))
            elif parsed.path.strip("/") in PAGE_TITLES:
                self._send_html(render_page(parsed.path.strip("/"), self.platform))
            else:
                self._send_json({"error": "not-found", "path": parsed.path}, status=404)
        except (ValueError, KeyError) as error:
            self._send_json({"error": str(error)}, status=400)

    def do_POST(self) -> None:  # noqa: N802 - http.server 回调命名
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/cure":
                self._send_json(self.api.cure(self._read_json()))
            elif parsed.path == "/api/open":
                self._send_json(self.api.open_mold(self._read_json()))
            elif parsed.path == "/api/dispatch":
                self._send_json(self.api.dispatch(self._read_json()))
            elif parsed.path == "/api/stop":
                self._send_json(self.api.stop(self._read_json()))
            elif parsed.path == "/api/step":
                self._send_json(self.api.step(self._read_json()))
            elif parsed.path == "/api/enqueue":
                self._send_json(self.api.enqueue(self._read_json()))
            elif parsed.path == "/api/remap":
                self._send_json(self.api.remap(self._read_json()))
            elif parsed.path == "/api/retire":
                self._send_json(self.api.retire(self._read_json()))
            else:
                self._send_json({"error": "not-found", "path": parsed.path}, status=404)
        except (ValueError, KeyError) as error:
            self._send_json({"error": str(error)}, status=400)


class ConsoleServer:
    """控制台服务封装。"""

    def __init__(self, platform: Platform, host: str = "127.0.0.1", port: int = 0) -> None:
        self.platform = platform
        self.api = ConsoleAPI(platform)
        handler = type(
            "BoundConsoleHandler",
            (ConsoleRequestHandler,),
            {"api": self.api, "platform": platform},
        )
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._httpd.quiet = True
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> str:
        """后台启动服务，返回基地址。"""

        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


def probe_pages(base_url: str, pages: tuple[str, ...], timeout: float = 5.0) -> dict:
    """真实发起 HTTP 请求读取页面与状态接口。"""

    results: dict[str, dict] = {}
    targets = list(pages) + ["api/status"]
    for target in targets:
        path = "" if target == "press" else target
        url = f"{base_url}/{path}" if path else f"{base_url}/"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                body = response.read()
                results[target] = {
                    "status": response.status,
                    "bytes": len(body),
                    "ok": response.status == 200 and len(body) > 0,
                }
        except urllib.error.URLError as error:  # pragma: no cover - 依赖真实网络故障
            results[target] = {"status": 0, "bytes": 0, "ok": False, "error": str(error)}
    return results
