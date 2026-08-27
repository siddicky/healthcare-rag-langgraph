from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers['Content-Length']); open('cache/mermaid-svg.json','wb').write(self.rfile.read(n))
        self.send_response(200); self.send_header('Access-Control-Allow-Origin','*'); self.end_headers(); self.wfile.write(b'ok')
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Access-Control-Allow-Headers','*'); self.end_headers()
    def log_message(self,*a): pass
HTTPServer(('127.0.0.1',8766),H).serve_forever()
