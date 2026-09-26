"""控制台：页面、只读接口与四条工艺顺序的 HTTP 入口。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from rubbercure.console.api import ConsoleAPI
from rubbercure.console.pages import PAGE_TITLES, render_page
from rubbercure.console.server import ConsoleServer, probe_pages


def _request(url: str, method: str = "GET", payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            body = response.read()
            try:
                return response.status, json.loads(body.decode("utf-8"))
            except ValueError:
                return response.status, body.decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def test_probe_pages_reads_every_page(platform):
    server = ConsoleServer(platform)
    base_url = server.start()
    try:
        results = probe_pages(base_url, platform.config.pages)
        assert all(item["ok"] for item in results.values())
        assert "api/status" in results
    finally:
        server.stop()


def test_http_status_and_order_endpoints(platform):
    server = ConsoleServer(platform)
    base_url = server.start()
    try:
        status, payload = _request(f"{base_url}/api/status")
        assert status == 200
        assert payload["platform"] == "RubberCure"
        assert "fleet" in payload
        assert "topology" in payload
        assert "namespaces" in payload

        status, readiness = _request(f"{base_url}/api/readiness")
        assert status == 200
        assert len(readiness["idle_presses"]) == platform.fleet.size

        status, page = _request(f"{base_url}/")
        assert status == 200
        assert "控制台" in page

        status, page = _request(f"{base_url}/timer")
        assert status == 200
        assert PAGE_TITLES["timer"] in page

        status, result = _request(f"{base_url}/api/cure", "POST", {"mold_id": "http-mold"})
        assert status == 200
        assert result["ok"] is True
        assert result["report"]["violations"] == []

        status, result = _request(f"{base_url}/api/step", "POST", {"dt": 1.0})
        assert status == 200
        assert result["ok"] is True
        assert len(result["cycle"]["presses"]) == platform.fleet.size

        status, body = _request(f"{base_url}/api/does-not-exist")
        assert status == 404
        assert "not-found" in body
    finally:
        server.stop()


def test_console_api_actions_cover_maintenance(platform):
    api = ConsoleAPI(platform)
    assert api.alarms()["count"] == 0
    assert api.audit(limit=5)["entries"] is not None
    assert api.trend(window_s=60.0)["series"] == {}
    assert api.journal()["entries"] is not None

    platform.register_mold("api-mold", cavity_count=2)
    added = api.enqueue({"mold_id": "api-mold", "thickness_mm": 6.0})
    assert added["ok"] is True
    assert platform.scheduler.queue.entries()[0]["mold_id"] == "api-mold"

    remapped = api.remap({"mold_id": "api-mold", "sensors": {"0": "api-t0", "1": "api-t1"}})
    assert remapped["ok"] is True
    assert remapped["mapping"]["generation"] == 2
    assert remapped["mapping"]["sensors"]["0"] == "api-t0"

    retired = platform.retire_mold("api-mold", "控制台报废")
    assert retired["status"] == "retired"
    assert platform.scheduler.queue.entries() == []


def test_render_page_rejects_unknown_page(platform):
    try:
        render_page("nope", platform)
    except KeyError as error:
        assert "nope" in str(error)
    else:  # pragma: no cover - 未抛错即失败
        raise AssertionError("未知页面必须报错")
