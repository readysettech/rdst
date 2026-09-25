"""A tiny TCP proxy between RDST and the database that a test can cut and restore.

Cutting closes the listening socket and every open connection, so RDST's reads fail and new
connections are refused: a real outage between the monitor and the database. Restoring listens
on the same port again.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any



class CuttableProxy:
    def __init__(self, upstream_host: str, upstream_port: int, *, listen_host: str = "127.0.0.1",
                 listen_port: int = 0) -> None:
        self.upstream = (upstream_host, upstream_port)
        self.listen_host = listen_host
        self.port = listen_port or _free_port(listen_host)
        self.cut_count = 0
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ci-shop-proxy",
                                        daemon=True)
        self._thread.start()

    @property
    def open(self) -> bool:
        return self._server is not None

    def _call(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout=15)

    def start(self) -> None:
        self._call(self._listen())

    def cut(self) -> None:
        self._call(self._cut())

    def close(self) -> None:
        self._call(self._cut())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    async def _listen(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._accept, self.listen_host, self.port, reuse_address=True)

    async def _cut(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            self.cut_count += 1
            server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        self._writers.clear()
        if server is not None:
            await server.wait_closed()

    async def _accept(self, client_reader: asyncio.StreamReader,
                      client_writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(*self.upstream)
        except OSError:
            client_writer.transport.abort()
            return
        self._writers.update((client_writer, upstream_writer))

        async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                self._writers.discard(writer)
                writer.transport.abort()

        await asyncio.gather(pipe(client_reader, upstream_writer),
                             pipe(upstream_reader, client_writer))


def _free_port(host: str) -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]
