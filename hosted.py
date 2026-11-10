"""Hosts the refund desk dashboard on a server (Render) without changing the harness.

The harness answers only to 127.0.0.1. This file starts it there and puts a password door in front of it:
it listens on 0.0.0.0:$PORT, asks for HTTP basic auth (DESK_USER / DESK_PASS), and passes each request on.
"""
import base64, hmac, http.client, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INNER = 8321
USER, PASS = os.environ.get("DESK_USER", "rahul"), os.environ.get("DESK_PASS", "")
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "upgrade", "proxy-authorization", "proxy-authenticate", "trailers", "host", "origin", "authorization", "content-length"}


class Door(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"          # one request per connection: a stream ends when the connection does

    def log_message(self, *a):
        pass

    def allowed(self):
        given = self.headers.get("Authorization") or ""
        want = "Basic " + base64.b64encode(("%s:%s" % (USER, PASS)).encode()).decode()
        return bool(PASS) and hmac.compare_digest(given.encode(), want.encode())

    def handle_any(self):
        if self.path == "/healthz":
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok"); return
        if not self.allowed():
            self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="refund desk"'); self.send_header("Content-Length", "0"); self.end_headers(); return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = "127.0.0.1:%d" % INNER
        if self.headers.get("Origin"):
            headers["Origin"] = "http://127.0.0.1:%d" % INNER
        try:
            conn = http.client.HTTPConnection("127.0.0.1", INNER, timeout=3600)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            self.send_response(502); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("connection", "keep-alive", "transfer-encoding"):
                self.send_header(k, v)
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk); self.wfile.flush()
        except OSError:
            pass
        finally:
            conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = handle_any


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        os.unlink(os.path.join(here, ".desk-server.json"))      # a file left by an instance that was stopped
    except OSError:
        pass
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "DESK_PASS")}
    inner = subprocess.Popen([sys.executable, "-m", "harness", "serve", "--port", str(INNER)], cwd=here, env=env)
    time.sleep(1)
    port = int(os.environ.get("PORT", "10000"))
    print("door open on 0.0.0.0:%d" % port, flush=True)
    try:
        ThreadingHTTPServer(("0.0.0.0", port), Door).serve_forever()
    finally:
        inner.terminate()
