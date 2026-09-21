"""APIC session handling: aaaLogin, the cookie, refresh, and 403-as-expiry.

The central test runs a real local HTTP server and a real httpx client: the
design relies on httpx's cookie jar carrying the session APIC sets, and a
mocked client cannot show that the cookie actually rides on later requests.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fabric_aiops.config import TargetConfig
from fabric_aiops.connection import FabricApiError, FabricConnection, _refresh_deadline
from fabric_aiops.platform import ACI

PASSWORD = "s3cret-Pw"  # nosec B105 — test fixture


def _login_body(token: str, refresh: str | None = "600") -> dict:
    attrs = {"token": token}
    if refresh is not None:
        attrs["refreshTimeoutSeconds"] = refresh
    return {"totalCount": "1", "imdata": [{"aaaLogin": {"attributes": attrs}}]}


def _error_body(text: str, code: str = "403") -> dict:
    return {"totalCount": "1", "imdata": [{"error": {"attributes": {"code": code, "text": text}}}]}


class _Apic(BaseHTTPRequestHandler):
    """Minimal APIC: session cookie, 403 on an expired/unknown session."""

    state: dict = {}

    def log_message(self, *args):  # silence test output
        pass

    def _send(self, status: int, body: dict, cookie: str | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", f"APIC-cookie={cookie}; path=/")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        st = self.state
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        st["logins"].append(body)
        attrs = body["aaaUser"]["attributes"]
        if attrs != {"name": "ro-user", "pwd": PASSWORD}:
            self._send(401, _error_body("Username or password is incorrect", "401"))
            return
        st["seq"] += 1
        st["valid"] = f"tok{st['seq']}"
        self._send(200, _login_body(st["valid"], st.get("refresh", "600")), st["valid"])

    def do_GET(self):  # noqa: N802
        st = self.state
        cookie = (self.headers.get("Cookie") or "").removeprefix("APIC-cookie=")
        st["gets"].append((self.path, cookie))
        if self.path.startswith("/api/aaaRefresh.json"):
            if cookie != st["valid"]:
                self._send(403, _error_body("Token was invalid (Error: Token timeout)"))
                return
            self._send(200, _login_body(cookie, st.get("refresh", "600")))
            return
        if st.get("deny") and self.path.startswith(st["deny"]):
            self._send(403, _error_body("user does not have read access"))
            return
        if cookie != st["valid"]:
            self._send(403, _error_body("Token was invalid (Error: Token timeout)"))
            return
        self._send(200, {"totalCount": "0", "imdata": []})


@pytest.fixture
def apic(monkeypatch):
    _Apic.state = {"logins": [], "gets": [], "seq": 0, "valid": None}
    server = HTTPServer(("127.0.0.1", 0), _Apic)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("FABRIC_APIC1_APIKEY", f"ro-user:{PASSWORD}")
    target = TargetConfig(
        name="apic1", platform=ACI, base_url=f"http://127.0.0.1:{server.server_port}"
    )
    conn = FabricConnection(target)
    yield conn, _Apic.state
    conn.close()
    server.shutdown()


def test_login_posts_aaauser_and_the_cookie_rides_on_reads(apic):
    conn, st = apic
    assert conn.get("/api/node/class/fvCEp.json") == {"totalCount": "0", "imdata": []}
    assert st["logins"] == [{"aaaUser": {"attributes": {"name": "ro-user", "pwd": PASSWORD}}}]
    assert st["gets"] == [("/api/node/class/fvCEp.json", "tok1")]
    conn.get("/api/node/class/fvBD.json")
    assert len(st["logins"]) == 1  # the session is reused, not re-opened per call


def test_expired_session_403_triggers_one_fresh_login_and_retry(apic, monkeypatch):
    conn, st = apic
    st["refresh"] = None  # no proactive refresh: exercise the reactive path
    conn.get("/api/node/class/fvCEp.json")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 120)  # session no longer fresh
    st["valid"] = "rotated-server-side"  # the session lapses on the APIC
    assert conn.get("/api/node/class/fvCEp.json")["imdata"] == []
    assert len(st["logins"]) == 2
    assert st["gets"][-1][1] == "tok2"


def test_a_403_in_a_fresh_session_is_a_privilege_error_without_a_second_login(apic):
    conn, st = apic
    st["deny"] = "/api/mo/uni/tn-secret"
    for _ in range(3):  # several denied objects in one trace
        with pytest.raises(FabricApiError) as exc:
            conn.get("/api/mo/uni/tn-secret.json")
        assert exc.value.status_code == 403
        assert "not an expired session" in str(exc.value)
        assert "does not have read access" in str(exc.value)
    assert len(st["logins"]) == 1  # no password login per denied object


def test_a_403_that_survives_a_relogin_of_an_old_session_is_a_privilege_error(
    apic, monkeypatch
):
    conn, st = apic
    st["refresh"] = None
    conn.get("/api/node/class/fvCEp.json")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 120)
    st["deny"] = "/api/mo/uni/tn-secret"
    with pytest.raises(FabricApiError, match="not an expired session"):
        conn.get("/api/mo/uni/tn-secret.json")
    assert len(st["logins"]) == 2  # exactly one retry, no login loop


def test_session_is_refreshed_at_half_its_lifetime(apic, monkeypatch):
    conn, st = apic
    st["refresh"] = "600"
    conn.get("/api/node/class/fvCEp.json")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 301)
    conn.get("/api/node/class/fvCEp.json")
    assert [p for p, _ in st["gets"]][1] == "/api/aaaRefresh.json"
    assert len(st["logins"]) == 1  # refreshed, not re-logged-in


def test_failed_refresh_degrades_to_a_new_login(apic, monkeypatch):
    conn, st = apic
    conn.get("/api/node/class/fvCEp.json")
    st["valid"] = "gone"
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 301)
    conn.get("/api/node/class/fvCEp.json")
    assert len(st["logins"]) == 2 and st["gets"][-1][1] == "tok2"


def test_wrong_password_names_apic_error_but_never_echoes_the_secret(apic, monkeypatch):
    conn, st = apic
    monkeypatch.setenv("FABRIC_APIC1_APIKEY", "ro-user:wrong-one")
    with pytest.raises(FabricApiError) as exc:
        conn.get("/api/node/class/fvCEp.json")
    assert "Username or password is incorrect" in str(exc.value)
    assert "wrong-one" not in str(exc.value)
    assert st["gets"] == []  # the read was never attempted


def test_secret_without_a_colon_is_refused_before_any_request(apic, monkeypatch):
    conn, st = apic
    monkeypatch.setenv("FABRIC_APIC1_APIKEY", "just-a-token")
    with pytest.raises(FabricApiError, match="username:password"):
        conn.get("/api/node/class/fvCEp.json")
    assert st["logins"] == [] and st["gets"] == []


def test_unknown_refresh_lifetime_is_not_zero():
    # Missing / junk / non-positive → no proactive refresh (reactive only),
    # instead of refreshing before every request.
    assert _refresh_deadline({}) is None
    assert _refresh_deadline({"refreshTimeoutSeconds": "abc"}) is None
    assert _refresh_deadline({"refreshTimeoutSeconds": "0"}) is None
    assert _refresh_deadline({"refreshTimeoutSeconds": "600"}) > time.monotonic()
