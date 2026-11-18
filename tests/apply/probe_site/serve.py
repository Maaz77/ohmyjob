"""Probe fixture server: same folder on 127.0.0.1:8731 and :8732 (two origins)."""
import http.server, socketserver, threading, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

for port in (8731, 8732):
    s = S(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
print("serving 8731 8732", flush=True)
threading.Event().wait()
