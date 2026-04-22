#!/usr/bin/env python3
"""
WebSocket telemetry bridge for ESP32 + browser dashboard.

Endpoints:
- /esp : ESP32 client sends CSV telemetry and receives text commands.
- /ui  : Browser dashboard receives telemetry batches and sends commands.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

# Server bind host and port for WebSocket traffic.
HOST = "0.0.0.0"
PORT = 8765

# Batch telemetry data sent to UI every 250 ms.
BATCH_INTERVAL_SEC = 0.250

# Defensive limit from spec: max 15 variables + millis => max 16 columns.
MAX_COLUMNS = 16

# Directory where recording CSV files are saved.
RECORDINGS_DIR = Path(__file__).resolve().parent / "recordings"


@dataclass
class BridgeState:
    """Shared mutable state for all WebSocket clients."""

    # Current ESP32 connection; only one device is expected in this setup.
    esp_conn: Optional[ServerConnection] = None

    # Connected browser UI clients (can be one or more tabs).
    ui_clients: set[ServerConnection] = field(default_factory=set)

    # CSV header row as parsed from HEADER:<csv...> message.
    header: Optional[list[str]] = None

    # Frame queue for UI batch delivery.
    batch_buffer: list[list[str]] = field(default_factory=list)

    # Recording state.
    recording_enabled: bool = False
    recording_file = None
    recording_writer: Optional[csv.writer] = None

    # Async lock protects state from concurrent tasks.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


state = BridgeState()


def parse_csv_line(message: str) -> list[str]:
    """Parse one CSV line into fields (keeps everything as strings)."""
    return next(csv.reader([message]))


async def start_recording() -> str:
    """Create a CSV file and prepare writer; returns created file path string."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = RECORDINGS_DIR / f"telemetry_{timestamp}.csv"

    state.recording_file = file_path.open("w", newline="", encoding="utf-8")
    state.recording_writer = csv.writer(state.recording_file)
    state.recording_enabled = True

    # If header is already known, write immediately.
    if state.header:
        state.recording_writer.writerow(state.header)
        state.recording_file.flush()

    return str(file_path)


async def stop_recording() -> None:
    """Stop recording and close open file handle (if any)."""
    state.recording_enabled = False
    state.recording_writer = None
    if state.recording_file:
        state.recording_file.close()
        state.recording_file = None


async def broadcast_ui(payload: dict) -> None:
    """Send JSON payload to all active UI clients and cleanup stale sockets."""
    stale: list[ServerConnection] = []
    for client in list(state.ui_clients):
        try:
            await client.send(json.dumps(payload))
        except ConnectionClosed:
            stale.append(client)

    # Remove clients that disconnected during broadcast.
    for dead in stale:
        state.ui_clients.discard(dead)


async def handle_esp(websocket: ServerConnection) -> None:
    """Process telemetry stream from ESP32 endpoint /esp."""
    async with state.lock:
        # Replace previous device connection if a new one arrives.
        state.esp_conn = websocket

    logging.info("ESP32 connected: %s", websocket.remote_address)

    try:
        async for message in websocket:
            if not isinstance(message, str):
                continue

            async with state.lock:
                # HEADER message must be sent once when device connects.
                if message.startswith("HEADER:"):
                    raw = message.removeprefix("HEADER:")
                    header = parse_csv_line(raw)

                    if len(header) == 0 or header[0] != "millis":
                        logging.warning("Ignoring invalid header (must start with millis): %s", header)
                        continue

                    if len(header) > MAX_COLUMNS:
                        logging.warning("Ignoring header with too many columns: %s", len(header))
                        continue

                    state.header = header

                    # If recording is active and file is empty, write header.
                    if state.recording_enabled and state.recording_writer and state.recording_file:
                        if state.recording_file.tell() == 0:
                            state.recording_writer.writerow(state.header)
                            state.recording_file.flush()

                    await broadcast_ui({"type": "header", "header": state.header})
                    continue

                # Normal telemetry frame line.
                row = parse_csv_line(message)

                # Validate frame shape only when header exists.
                if state.header and len(row) != len(state.header):
                    logging.debug("Dropping row length mismatch: %s", row)
                    continue

                if len(row) > MAX_COLUMNS:
                    logging.debug("Dropping row above max column count: %s", row)
                    continue

                state.batch_buffer.append(row)

                # Record frame if recording is enabled.
                if state.recording_enabled and state.recording_writer:
                    state.recording_writer.writerow(row)
                    if state.recording_file:
                        state.recording_file.flush()

    finally:
        async with state.lock:
            if state.esp_conn is websocket:
                state.esp_conn = None
        logging.info("ESP32 disconnected")


async def handle_ui(websocket: ServerConnection) -> None:
    """Process dashboard client endpoint /ui."""
    async with state.lock:
        state.ui_clients.add(websocket)
        current_header = state.header

    logging.info("UI connected: %s", websocket.remote_address)

    # Send known header immediately for fast dashboard initialization.
    if current_header:
        await websocket.send(json.dumps({"type": "header", "header": current_header}))

    try:
        async for message in websocket:
            if not isinstance(message, str):
                continue

            async with state.lock:
                # REC commands are intercepted by server and not forwarded to ESP32.
                if message == "REC:start":
                    if not state.recording_enabled:
                        file_path = await start_recording()
                        await websocket.send(json.dumps({"type": "recording", "status": "started", "file": file_path}))
                    else:
                        await websocket.send(json.dumps({"type": "recording", "status": "already_running"}))
                    continue

                if message == "REC:stop":
                    if state.recording_enabled:
                        await stop_recording()
                        await websocket.send(json.dumps({"type": "recording", "status": "stopped"}))
                    else:
                        await websocket.send(json.dumps({"type": "recording", "status": "already_stopped"}))
                    continue

                # Command format CMD:<text>; forward only command text to ESP32.
                if message.startswith("CMD:"):
                    command = message.removeprefix("CMD:")
                    if state.esp_conn:
                        await state.esp_conn.send(command)
                        await websocket.send(json.dumps({"type": "cmd", "status": "forwarded", "cmd": command}))
                    else:
                        await websocket.send(json.dumps({"type": "cmd", "status": "no_esp", "cmd": command}))
                    continue
    finally:
        async with state.lock:
            state.ui_clients.discard(websocket)
        logging.info("UI disconnected")


async def router(websocket: ServerConnection) -> None:
    """Route incoming WebSocket clients based on URL path."""
    path = websocket.request.path
    if path == "/esp":
        await handle_esp(websocket)
    elif path == "/ui":
        await handle_ui(websocket)
    else:
        await websocket.send(json.dumps({"type": "error", "message": f"Unknown path: {path}"}))
        await websocket.close(code=1008, reason="Invalid endpoint")


async def batch_publisher() -> None:
    """Periodic task to push telemetry rows to UI clients every 250 ms."""
    while True:
        await asyncio.sleep(BATCH_INTERVAL_SEC)

        async with state.lock:
            if not state.batch_buffer:
                continue
            rows = state.batch_buffer
            state.batch_buffer = []
            header = state.header

        payload = {"type": "batch", "header": header, "rows": rows}
        await broadcast_ui(payload)


async def main() -> None:
    """Run WebSocket bridge server plus periodic batch publisher task."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    publisher_task = asyncio.create_task(batch_publisher())

    async with serve(router, host=HOST, port=PORT, max_size=2**20):
        logging.info("WebSocket bridge listening on ws://%s:%s", HOST, PORT)
        await asyncio.Future()

    publisher_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
