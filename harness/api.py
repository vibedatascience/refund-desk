"""The Claude API over plain HTTPS, with nothing but Python's standard library. Nothing to install.

It offers the few calls this kit makes, under the names the official SDK (the 'anthropic' package) gives them, so that the code that
uses it reads like code written for the SDK, and could be: client.beta.agents.create(...), client.beta.sessions.events.stream(...),
client.messages.create(...). An answer is a Thing: a dict whose keys can also be read as attributes (event.type, usage.input_tokens).

One request, one try: waiting and trying again is the caller's business (see runner.py and cma.py).
"""
from __future__ import annotations

import copy
import json
import mimetypes
import os
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

# What goes over the wire (paths, headers, bodies) is what version 2.6.0 of the official SDK sends for the same calls: compared request by request.
VERSION = "2026-10-21"
MANAGED_AGENTS = "managed-agents-2026-04-01"


# ── errors, named as the SDK names them ───────────────────────────────────
class APIError(Exception):
    status_code = request_id = body = response = None


class APIConnectionError(APIError):
    """No answer at all: no network, a refused or dropped connection, a certificate that cannot be checked."""


class APITimeoutError(APIConnectionError):
    pass


class APIStatusError(APIError):
    """The API answered, and the answer is an error."""

    def __init__(self, status, headers, raw, call=""):
        try:
            self.body = json.loads(raw)
        except ValueError:
            self.body = raw.decode("utf-8", "replace")[:2000]
        inner = self.body.get("error") if isinstance(self.body, dict) else None
        said = inner.get("message") if isinstance(inner, dict) else inner          # (a gateway may say {"error": "forbidden"})
        self.status_code, self.request_id = status, headers.get("request-id")
        where = ", ".join(x for x in (call, "request " + self.request_id if self.request_id else "") if x)
        super().__init__("Error code: %d - %s%s" % (status, said or self.body, " (%s)" % where if where else ""))
        self.response = Thing(headers={k.lower(): v for k, v in headers.items()})


class BadRequestError(APIStatusError):
    pass


class AuthenticationError(APIStatusError):
    pass


class PermissionDeniedError(APIStatusError):
    pass


class NotFoundError(APIStatusError):
    pass


class RateLimitError(APIStatusError):
    pass


class InternalServerError(APIStatusError):
    pass


KINDS = {400: BadRequestError, 401: AuthenticationError, 403: PermissionDeniedError, 404: NotFoundError, 429: RateLimitError}


# ── answers ───────────────────────────────────────────────────────────────
class Thing(dict):
    """A JSON object. thing["type"] and thing.type are the same; a key that is not there reads as None."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self.get(name)

    def to_dict(self, **_):
        return plain(self)


def thing(value):
    if isinstance(value, dict):
        return Thing((k, thing(v)) for k, v in value.items())
    return [thing(v) for v in value] if isinstance(value, list) else value


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return [plain(v) for v in value] if isinstance(value, list) else value


# ── the wire ──────────────────────────────────────────────────────────────
def trust() -> ssl.SSLContext:
    """The system's certificates. Some Pythons for macOS come with an empty store: then the system's own file is tried.
    Checking the certificate is never switched off."""
    context = ssl.create_default_context()
    if not context.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        context.load_verify_locations("/etc/ssl/cert.pem")
    for name in ("NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):      # behind a company proxy that opens TLS, one of these
        path = os.environ.get(name)                                                  # usually names the company's own certificate
        if path and os.path.isfile(path):
            try:
                context.load_verify_locations(path)
            except (OSError, ssl.SSLError):
                pass
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The API never redirects. Following one would carry the key to another host, so a 3xx is an error like any other."""

    def redirect_request(self, *_, **__):
        return None


def as_bytes(body) -> bytes:
    try:
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:          # half a surrogate pair in somebody's text: send it escaped
        return json.dumps(body, separators=(",", ":")).encode("utf-8")


class Anthropic:
    def __init__(self, api_key=None, base_url=None, timeout=60, max_retries=0):
        self.api_key = (api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not all("!" <= ch <= "~" for ch in self.api_key):
            raise APIConnectionError("The API key holds a space, a line break or an odd character. Copy it again.")
        # Straight to the API. A shell that is set up for a company's gateway (ANTHROPIC_BASE_URL and the like) is not obeyed: the workshop's key
        # belongs nowhere else. DESK_BASE_URL is this kit's own switch, for tests against a stand-in.
        self.base_url = (base_url or os.environ.get("DESK_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        where = urllib.parse.urlparse(self.base_url)
        if where.scheme != "https" and where.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise APIConnectionError("DESK_BASE_URL is set to %s. The key is sent over https only (or to this very laptop)." % self.base_url)
        self.timeout = timeout
        self.extra = {}             # further headers from the environment, one "Name: value" a line
        for line in (os.environ.get("DESK_EXTRA_HEADERS") or "").split("\n"):
            if ":" in line:
                self.extra[line.split(":", 1)[0].strip()] = line.split(":", 1)[1].strip()
        # urllib finds a company's proxy in the environment (https_proxy, no_proxy) by itself
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=trust()), NoRedirect())
        self.messages, self.beta = Messages(self), Beta(self)

    def with_options(self, timeout=None):
        """The same client with another time limit."""
        other = copy.copy(self)
        other.timeout = timeout or self.timeout
        other.messages, other.beta = Messages(other), Beta(other)
        return other

    def open(self, method, path, body=None, query=None, beta=None, timeout=None, content_type="application/json", accept="application/json"):
        """Send one request and return the open response. Raises one of the errors above."""
        if not self.api_key:
            raise APIConnectionError("There is no API key.")
        query = {k: v for k, v in (query or {}).items() if v is not None}
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": "Bearer " + self.api_key, "Anthropic-Version": VERSION, "Accept": accept, "User-Agent": "refund-desk-workshop/2"}
        if beta:
            headers["Anthropic-Beta"] = beta
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else as_bytes(body)
            headers["Content-Type"] = content_type
        headers.update(self.extra)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            return self.opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as error:
            raw = error.read()
            kind = KINDS.get(error.code) or (InternalServerError if error.code >= 500 else APIStatusError)
            raise kind(error.code, error.headers, raw, method + " " + path) from None
        except (socket.timeout, TimeoutError):
            raise APITimeoutError("Request timed out.") from None
        except (urllib.error.URLError, OSError) as error:
            why = getattr(error, "reason", error)
            if isinstance(why, (socket.timeout, TimeoutError)):
                raise APITimeoutError("Request timed out.") from None
            if "CERTIFICATE_VERIFY_FAILED" in str(why):
                raise APIConnectionError(
                    "Connection error: this Python cannot check the API's certificate. If Python came from python.org, run "
                    "'Install Certificates.command' in its folder under /Applications once. Behind a company proxy that opens "
                    "TLS, point SSL_CERT_FILE at the company's certificate file.") from None
            raise APIConnectionError("Connection error: no answer from %s. Is this laptop online (Wi-Fi, VPN, proxy)? (%s)" % (
                urllib.parse.urlparse(self.base_url).hostname, why)) from None

    def call(self, method, path, **how):
        with self.open(method, path, **how) as response:
            try:
                raw = response.read()
            except (socket.timeout, TimeoutError):
                raise APITimeoutError("Request timed out.") from None
            except OSError as error:
                raise APIConnectionError("Connection error. (%s)" % error) from None
        return thing(json.loads(raw)) if raw.strip() else Thing()

    def pages(self, path, beta, query=None):
        """Every item of a list, page after page. The first page is fetched at once, so that an error shows at the call."""
        query = dict(query or {})
        first = self.call("GET", path, query=query, beta=beta)

        def items(page):
            while True:
                for item in page.get("data") or []:
                    yield item
                if not page.get("next_page"):
                    return
                page = self.call("GET", path, query=dict(query, page=page["next_page"]), beta=beta)
        return items(first)


class Stream:
    """A server-sent event stream: iterate over it for the events, close() it from anywhere to end the wait."""

    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:        # also from another thread, while this one waits in a read: shut the socket down, so that the read returns
            self.response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.response.close()
        except Exception:
            pass

    def __iter__(self):
        data = []
        try:
            for raw in self.response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
                elif not line and data:             # a blank line ends an event. (Lines that begin with ":" are keep-alives.)
                    text, data = "\n".join(data), []
                    if not text.strip():
                        continue
                    event = thing(json.loads(text))
                    if event.type == "error":       # the service says so in mid-stream: an error like any other
                        kind = (event.error or {}).get("type")
                        status = {"rate_limit_error": 429, "overloaded_error": 529, "api_error": 500}.get(kind, 400)
                        raise (KINDS.get(status) or (InternalServerError if status >= 500 else APIStatusError))(status, {}, text.encode("utf-8"), "in the event stream")
                    if event.type != "ping":
                        yield event
        except (socket.timeout, TimeoutError):
            raise APITimeoutError("Request timed out.") from None
        except APIError:
            raise
        except (OSError, ValueError, AttributeError) as error:      # dropped, or closed from outside
            raise APIConnectionError("Connection error. (%s)" % error) from None


# ── the calls ─────────────────────────────────────────────────────────────
class Part:
    def __init__(self, client):
        self.client = client


class Messages(Part):
    def create(self, **body):
        timeout = body.pop("timeout", None)
        return self.client.call("POST", "/v1/messages", body=body, timeout=timeout)


class Beta(Part):
    def __init__(self, client):
        super().__init__(client)
        self.environments, self.skills, self.agents, self.sessions = Environments(client), Skills(client), Agents(client), Sessions(client)


class Environments(Part):
    def create(self, **body):
        return self.client.call("POST", "/v1/environments", body=body, beta=MANAGED_AGENTS)

    def retrieve(self, environment_id):
        return self.client.call("GET", "/v1/environments/" + environment_id, beta=MANAGED_AGENTS)

    def delete(self, environment_id):
        return self.client.call("DELETE", "/v1/environments/" + environment_id, beta=MANAGED_AGENTS)


def multipart(fields: dict, files: list):
    """multipart/form-data: plain fields, then files as (field name, file name, bytes)."""
    edge, out = uuid.uuid4().hex, []
    for name, value in fields.items():
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (edge, name, value)).encode("utf-8"))
    for field, name, content in files:
        kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
        out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (edge, field, name, kind)).encode("utf-8")
                   + content + b"\r\n")
    return b"".join(out) + ("--%s--\r\n" % edge).encode(), "multipart/form-data; boundary=" + edge


class Skills(Part):
    def __init__(self, client):
        super().__init__(client)
        self.versions = SkillVersions(client)

    def create(self, files, **fields):
        body, kind = multipart(fields, [("files[]",) + tuple(f) for f in files])
        return self.client.call("POST", "/v1/skills", body=body, content_type=kind)

    def delete(self, skill_id):
        return self.client.call("DELETE", "/v1/skills/" + skill_id)


class SkillVersions(Part):
    def create(self, skill_id, files):
        body, kind = multipart({}, [("files[]",) + tuple(f) for f in files])
        return self.client.call("POST", "/v1/skills/%s/versions" % skill_id, body=body, content_type=kind)

    def list(self, skill_id):
        return self.client.pages("/v1/skills/%s/versions" % skill_id, None)

    def delete(self, version, skill_id):
        return self.client.call("DELETE", "/v1/skills/%s/versions/%s" % (skill_id, version))


class Agents(Part):
    def create(self, **body):
        return self.client.call("POST", "/v1/agents", body=body, beta=MANAGED_AGENTS)

    def update(self, agent_id, **body):
        return self.client.call("POST", "/v1/agents/" + agent_id, body=body, beta=MANAGED_AGENTS)

    def retrieve(self, agent_id, version=None):
        return self.client.call("GET", "/v1/agents/" + agent_id, query={"version": version}, beta=MANAGED_AGENTS)

    def list(self, limit=None):
        return self.client.pages("/v1/agents", MANAGED_AGENTS, {"limit": limit})

    def archive(self, agent_id):
        return self.client.call("POST", "/v1/agents/%s/archive" % agent_id, beta=MANAGED_AGENTS)


class Sessions(Part):
    def __init__(self, client):
        super().__init__(client)
        self.events = Events(client)

    def create(self, **body):
        return self.client.call("POST", "/v1/sessions", body=body, beta=MANAGED_AGENTS)

    def retrieve(self, session_id):
        return self.client.call("GET", "/v1/sessions/" + session_id, beta=MANAGED_AGENTS)

    def delete(self, session_id):
        return self.client.call("DELETE", "/v1/sessions/" + session_id, beta=MANAGED_AGENTS)


class Events(Part):
    def send(self, session_id, events):
        return self.client.call("POST", "/v1/sessions/%s/events" % session_id, body={"events": events}, beta=MANAGED_AGENTS)

    def list(self, session_id):
        return self.client.pages("/v1/sessions/%s/events" % session_id, MANAGED_AGENTS)

    def stream(self, session_id, timeout=None):
        return Stream(self.client.open("GET", "/v1/sessions/%s/events/stream" % session_id, beta=MANAGED_AGENTS,
                                       timeout=timeout))
