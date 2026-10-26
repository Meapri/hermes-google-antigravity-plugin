"""Antigravity OAuth: runtime credential resolution + interactive Google login.
Mirrors the qwen-oauth/nous pattern so google-antigravity is a first-class
oauth_external provider. Auth details confirmed against the agy CLI binary."""
from __future__ import annotations
import base64, hashlib, http.server, json, os, secrets, threading, time
import urllib.parse, urllib.request, urllib.error
from typing import Any, Dict

BASE_URL = "https://cloudcode-pa.googleapis.com"
_AUTH = "https://accounts.google.com/o/oauth2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
_EXTERNAL_REDIRECT = "https://antigravity.google/oauth-callback"
_LOCAL_PORT = 51121
_LOCAL_REDIRECT = "http://localhost:51121/auth/callback"
_SCOPES = ("https://www.googleapis.com/auth/cloud-platform "
           "https://www.googleapis.com/auth/userinfo.email "
           "https://www.googleapis.com/auth/userinfo.profile "
           "https://www.googleapis.com/auth/cclog "
           "https://www.googleapis.com/auth/experimentsandconfigs openid")


def _home():
    return os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes")


def _cred_file():
    return os.path.join(_home(), "auth", "google_antigravity.json")


def _client_file():
    return os.path.join(_home(), "auth", "google_antigravity_client.json")


def resolve_antigravity_runtime_credentials(*, force_refresh=False, **kw) -> Dict[str, Any]:
    from agent.antigravity_cloudcode import AntigravityClient
    from agent.antigravity_cloudcode_config import Settings
    cr = AntigravityClient(Settings.from_env())._valid_credentials()
    access = getattr(cr, "access_token", "") or ""
    if not access:
        try:
            from hermes_cli.auth import AuthError
            raise AuthError("Antigravity OAuth token missing. Run 'hermes auth add google-antigravity'.",
                            provider="google-antigravity", code="antigravity_token_missing")
        except ImportError:
            raise RuntimeError("antigravity token missing")
    return {"provider": "google-antigravity", "base_url": BASE_URL, "api_key": access,
            "source": "antigravity-oauth", "auth_file": _cred_file()}


def _pkce():
    v = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


def _save(r) -> Dict[str, Any]:
    acc = r.get("access_token")
    if not acc:
        raise RuntimeError("no access token: " + json.dumps(r)[:200])
    out = {"access": acc, "refresh": r.get("refresh_token", ""),
           "expires": int((time.time() + int(r.get("expires_in", 3600))) * 1000), "email": ""}
    try:
        old = json.load(open(_cred_file(), encoding="utf-8"))
        if old.get("email"):
            out["email"] = old["email"]
    except Exception:
        pass
    os.makedirs(os.path.dirname(_cred_file()), exist_ok=True)
    json.dump(out, open(_cred_file(), "w", encoding="utf-8"), indent=2)
    os.chmod(_cred_file(), 0o600)
    return {"access_token": acc, "refresh_token": out["refresh"], "expires_at": out["expires"]}


def run_antigravity_login(*, use_local_server=True, input_fn=input, print_fn=print) -> Dict[str, Any]:
    d = json.load(open(_client_file(), encoding="utf-8"))
    cid, csec = d["client_id"], d["client_secret"]
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    box = {}
    server = None
    redirect = _LOCAL_REDIRECT if use_local_server else _EXTERNAL_REDIRECT
    if use_local_server:
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_GET(self):
                pr = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                box["code"] = (pr.get("code") or [""])[0]
                box["state"] = (pr.get("state") or [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Antigravity login complete. You can close this tab.</h2>")
        try:
            server = http.server.HTTPServer(("127.0.0.1", _LOCAL_PORT), H)
            threading.Thread(target=server.handle_request, daemon=True).start()
        except OSError:
            server = None
            redirect = _EXTERNAL_REDIRECT
    params = {"access_type": "offline", "client_id": cid, "code_challenge": challenge,
              "code_challenge_method": "S256", "prompt": "consent", "redirect_uri": redirect,
              "response_type": "code", "scope": _SCOPES, "state": state}
    url = _AUTH + "?" + urllib.parse.urlencode(params)
    print_fn("\n[1] Open this URL in a browser and sign in with Google:\n\n" + url + "\n")
    if server is not None:
        print_fn("[2] After signing in, the browser redirects to a http://localhost:51121/... URL.")
        print_fn("    * Local (or you first ran  ssh -L 51121:localhost:51121 <host>): the page")
        print_fn("      loads and the code is captured automatically -- just wait.")
        print_fn("    * Remote/headless with no tunnel: the page fails to load. That is fine --")
        print_fn("      copy the FULL redirected URL from the address bar (or the code=... value)")
        print_fn("      and paste it here, then press Enter.")
        print_fn("")

        def _stdin_reader():
            # Read a pasted callback URL/code concurrently with the local server so a
            # headless-remote login (whose browser callback cannot reach this box without
            # an SSH tunnel) completes by paste. First source to fill box["code"] wins.
            try:
                raw = input_fn("Paste redirected URL or code (or wait for auto-capture): ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not raw or box.get("code"):
                return
            if "code=" in raw:
                q = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
                pr = urllib.parse.parse_qs(q)
                box.setdefault("state", (pr.get("state") or [""])[0])
                box["code"] = (pr.get("code") or [""])[0]
            else:
                box["code"] = raw
        threading.Thread(target=_stdin_reader, daemon=True).start()

        deadline = time.time() + 300
        while not box.get("code") and time.time() < deadline:
            time.sleep(0.5)
        try:
            server.server_close()
        except Exception:
            pass
        code, rstate = box.get("code", ""), box.get("state", "")
    else:
        print_fn("[2] After login, paste the redirect URL (antigravity.google/oauth-callback?code=...) or the code.\n")
        raw = input_fn("Paste callback URL or code: ").strip()
        rstate = ""
        if "code=" in raw:
            pr = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1])
            rstate = (pr.get("state") or [""])[0]
            code = (pr.get("code") or [""])[0]
        else:
            code = raw
    if not code:
        raise RuntimeError("no authorization code received")
    if rstate and rstate != state:
        raise RuntimeError("state mismatch")
    data = urllib.parse.urlencode({"code": code, "client_id": cid, "client_secret": csec,
                                   "code_verifier": verifier, "grant_type": "authorization_code",
                                   "redirect_uri": redirect}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        _TOKEN, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30))
    return _save(r)
