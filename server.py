#!/usr/bin/env python3
"""WebSocket bridge between ESP32 telemetry and web dashboard.

Endpoints:
- ws://<host>:8765/esp  -> ESP32 client
- ws://<host>:8765/ui   -> Dashboard browser

HTTP dashboard:
- http://<host>:8765/
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

HOST = "0.0.0.0"
PORT = 8765
WEB_DIR = Path(__file__).parent / "web"
DATA_DIR = Path(__file__).parent / "data"


@dataclass
class TelemetryState:
    esp: ServerConnection | None = None
    ui_clients: set[ServerConnection] = field(default_factory=set)
    header: list[str] = field(default_factory=list)
    csv_enabled: bool = False
    csv_writer: csv.writer | None = None
    csv_file: Any | None = None
    csv_path: Path | None = None
    frame_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def set_esp(self, conn: ServerConnection | None) -> None:
        async with self.lock:
            self.esp = conn

    async def add_ui(self, conn: ServerConnection) -> None:
        async with self.lock:
            self.ui_clients.add(conn)

    async def remove_ui(self, conn: ServerConnection) -> None:
        async with self.lock:
            self.ui_clients.discard(conn)

    async def snapshot(self) -> dict[str, Any]:
        async with self.lock:
            return {
                "type": "status",
                "espConnected": self.esp is not None,
                "uiClients": len(self.ui_clients),
                "header": self.header,
                "csvEnabled": self.csv_enabled,
                "frameCount": self.frame_count,
                "csvPath": str(self.csv_path) if self.csv_path else "",
            }

    async def start_csv(self) -> tuple[bool, str]:
        async with self.lock:
            if self.csv_enabled:
                return False, "CSV recording already running"
            if not self.header:
                return False, "CSV cannot start before HEADER arrives from ESP"

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self.csv_path = DATA_DIR / f"telemetry_{stamp}.csv"
            self.csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(self.header)
            self.csv_file.flush()
            self.csv_enabled = True
            return True, f"CSV recording started: {self.csv_path.name}"

    async def stop_csv(self) -> tuple[bool, str]:
        async with self.lock:
            if not self.csv_enabled:
                return False, "CSV recording is not active"
            self.csv_enabled = False
            if self.csv_file:
                self.csv_file.flush()
                self.csv_file.close()
            path = self.csv_path.name if self.csv_path else "(unknown)"
            self.csv_writer = None
            self.csv_file = None
            return True, f"CSV recording stopped: {path}"

    async def write_csv_row(self, row: list[str]) -> None:
        async with self.lock:
            if self.csv_enabled and self.csv_writer and self.csv_file:
                self.csv_writer.writerow(row)
                self.csv_file.flush()

    async def set_header(self, header: list[str]) -> None:
        async with self.lock:
            self.header = header

    async def increment_frame(self) -> int:
        async with self.lock:
            self.frame_count += 1
            return self.frame_count


STATE = TelemetryState()


def http_response(
    status: int,
    body: bytes,
    content_type: str = "text/plain; charset=utf-8",
) -> Response:
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    headers["Date"] = formatdate(usegmt=True)
    headers["Connection"] = "close"
    return Response(status, "OK", headers, body)


async def process_request(conn: ServerConnection, request: Request) -> Response | None:
    # Return None for websocket handshake paths.
    if request.path in {"/esp", "/ui"}:
        return None

    # Minimal static file handler for dashboard assets.
    file_map = {
        "/": WEB_DIR / "index.html",
        "/index.html": WEB_DIR / "index.html",
        "/app.js": WEB_DIR / "app.js",
        "/styles.css": WEB_DIR / "styles.css",
    }
    file_path = file_map.get(request.path)
    if not file_path or not file_path.exists():
        return http_response(404, b"Not found")

    content_type = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }.get(file_path.suffix, "application/octet-stream")

    return http_response(200, file_path.read_bytes(), content_type)


async def send_json(conn: ServerConnection, payload: dict[str, Any]) -> None:
    await conn.send(json.dumps(payload, ensure_ascii=False))


async def broadcast_ui(payload: dict[str, Any]) -> None:
    clients = list(STATE.ui_clients)
    if not clients:
        return

    message = json.dumps(payload, ensure_ascii=False)
    stale: list[ServerConnection] = []
    for client in clients:
        try:
            await client.send(message)
        except ConnectionClosed:
            stale.append(client)

    for client in stale:
        await STATE.remove_ui(client)


async def handle_esp(conn: ServerConnection) -> None:
    logging.info("ESP connected from %s", conn.remote_address)
    await STATE.set_esp(conn)
    await broadcast_ui(await STATE.snapshot())

    try:
        async for message in conn:
            if not isinstance(message, str):
                continue

            text = message.strip()
            if text.startswith("HEADER:"):
                raw_header = text.split(":", 1)[1].strip()
                header = [x.strip() for x in raw_header.split(",") if x.strip()]
                if not header:
                    continue
                if len(header) > 15:
                    await send_json(
                        conn,
                        {
                            "type": "error",
                            "message": "HEADER rejected: variable count exceeds 15",
                        },
                    )
                    continue

                await STATE.set_header(header)
                await broadcast_ui({"type": "header", "columns": header})
                await broadcast_ui(await STATE.snapshot())
                continue

            row = [x.strip() for x in text.split(",")]
            frame = await STATE.increment_frame()
            await STATE.write_csv_row(row)
            await broadcast_ui(
                {
                    "type": "telemetry",
                    "values": row,
                    "frame": frame,
                }
            )

    finally:
        logging.info("ESP disconnected")
        await STATE.set_esp(None)
        ok, _ = await STATE.stop_csv() if STATE.csv_enabled else (False, "")
        if ok:
            logging.info("Stopped CSV because ESP disconnected")
        await broadcast_ui(await STATE.snapshot())


async def handle_ui(conn: ServerConnection) -> None:
    await STATE.add_ui(conn)
    logging.info("UI connected from %s", conn.remote_address)

    snapshot = await STATE.snapshot()
    await send_json(conn, snapshot)
    if snapshot["header"]:
        await send_json(conn, {"type": "header", "columns": snapshot["header"]})

    try:
        async for message in conn:
            if not isinstance(message, str):
                continue

            # Protocol from UI -> server:
            # {"type":"command","value":"..."}
            # {"type":"csv","action":"start|stop"}
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                payload = {"type": "command", "value": message}

            mtype = payload.get("type")
            if mtype == "command":
                value = str(payload.get("value", "")).strip()
                if value and STATE.esp:
                    await STATE.esp.send(value)
            elif mtype == "csv":
                action = payload.get("action")
                if action == "start":
                    ok, info = await STATE.start_csv()
                elif action == "stop":
                    ok, info = await STATE.stop_csv()
                else:
                    ok, info = False, "Unknown csv action"
                await send_json(conn, {"type": "notice", "ok": ok, "message": info})
                await broadcast_ui(await STATE.snapshot())

    finally:
        await STATE.remove_ui(conn)
        logging.info("UI disconnected")
        await broadcast_ui(await STATE.snapshot())


async def ws_handler(conn: ServerConnection) -> None:
    if conn.request.path == "/esp":
        await handle_esp(conn)
    elif conn.request.path == "/ui":
        await handle_ui(conn)
    else:
        await conn.close(code=1008, reason="Unknown path")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
    logging.info("Starting server at ws://%s:%d", HOST, PORT)

    async with serve(ws_handler, HOST, PORT, process_request=process_request):
        logging.info("Dashboard: http://127.0.0.1:%d", PORT)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
