#!/usr/bin/env python3
"""Small bounded TCP relay on the ECS Docker bridge, never on a public IP."""
from __future__ import annotations

import argparse
import select
import socket
import socketserver
import threading
import time


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection((self.server.target_host, self.server.target_port), timeout=5)
        except OSError:
            return
        client = self.request
        client.settimeout(None)
        upstream.settimeout(None)
        peers = {client: upstream, upstream: client}
        live = set(peers)
        last_io = time.monotonic()
        try:
            while live and time.monotonic() - last_io < self.server.idle_timeout:
                readable, _, _ = select.select(list(live), [], [], 5)
                if not readable:
                    continue
                for source in readable:
                    try:
                        chunk = source.recv(65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        live.discard(source)
                        try:
                            peers[source].shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                    last_io = time.monotonic()
                    try:
                        peers[source].sendall(chunk)
                    except OSError:
                        live.discard(source)
        finally:
            for sock in (client, upstream):
                try:
                    sock.close()
                except OSError:
                    pass


class BoundedThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler, *, target_host: str, target_port: int,
                 max_connections: int, idle_timeout: int):
        self.target_host = target_host
        self.target_port = target_port
        self.idle_timeout = idle_timeout
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler, bind_and_activate=True)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", required=True)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--max-connections", type=int, default=48)
    parser.add_argument("--idle-timeout", type=int, default=240)
    args = parser.parse_args()
    if not args.listen.startswith("172."):
        parser.error("The relay must bind to the private Docker bridge address")
    if min(args.listen_port, args.target_port, args.max_connections, args.idle_timeout) <= 0:
        parser.error("Ports and limits must be positive")
    server = BoundedThreadingTCPServer(
        (args.listen, args.listen_port), RelayHandler,
        target_host=args.target, target_port=args.target_port,
        max_connections=args.max_connections, idle_timeout=args.idle_timeout,
    )
    server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
