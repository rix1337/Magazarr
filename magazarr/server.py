# -*- coding: utf-8 -*-

from socketserver import TCPServer, ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]
        self.setup_environ()


class NoLoggingWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass


class Server:
    def __init__(self, wsgi_app, listen="127.0.0.1", port=8090):
        self.server = make_server(
            listen,
            port,
            wsgi_app,
            ThreadingWSGIServer,
            handler_class=NoLoggingWSGIRequestHandler,
        )

    def serve_forever(self):
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            self.server.shutdown()
            self.server.server_close()
