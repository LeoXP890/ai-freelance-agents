import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aicommerce.providers.woocommerce import _http


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_authenticated_transport_never_follows_redirects(method):
    seen = []

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_port}/secret-target")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = _http(
            method,
            f"http://127.0.0.1:{server.server_port}/source",
            {"Authorization": "Bearer test-credential"},
            None,
            2,
        )
        assert status == 302
        assert seen == ["/source"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
