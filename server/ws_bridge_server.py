#!/usr/bin/env python3
"""
WebSocket bridge server untuk arsitektur:
- ESP32 client terhubung ke /esp
- Dashboard browser terhubung ke /ui

Fitur utama:
1) Forward telemetry dari ESP32 -> dashboard (dibatch tiap 250 ms)
2) Forward command dari dashboard -> ESP32 (prefix CMD: dihapus)
3) REC:start / REC:stop diproses lokal untuk logging CSV
4) Notifikasi status koneksi ESP32 ke dashboard

Dependensi:
- websockets==16.0
"""

from __future__ import annotations

# ------------------------------
# Import standar library Python
# ------------------------------
import asyncio
import csv
import datetime as dt
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Set, List

# ---------------------------------------
# Import library websockets (server side)
# ---------------------------------------
from websockets.asyncio.server import serve
from websockets.server import ServerConnection
from websockets.exceptions import ConnectionClosed


# ---------------------------------------
# Konfigurasi umum server dan performa
# ---------------------------------------
WS_HOST = "0.0.0.0"        # Bind semua interface jaringan laptop
WS_PORT = 8765             # Port websocket sesuai spesifikasi
BATCH_INTERVAL_S = 0.250   # Flush telemetry ke UI tiap 250 ms
MAX_VARIABLES = 15         # Maks variabel telemetry selain micros
RECORDINGS_DIR = Path("recordings")


@dataclass
class BridgeState:
    """State bersama untuk seluruh koneksi websocket."""

    # Koneksi ESP32 aktif saat ini (hanya 1 perangkat utama)
    esp_conn: Optional[ServerConnection] = None

    # Sekumpulan koneksi dashboard UI yang aktif (boleh lebih dari 1 tab)
    ui_clients: Set[ServerConnection] = field(default_factory=set)

    # Header telemetry aktif untuk sesi saat ini
    header_cols: Optional[List[str]] = None

    # Buffer frame telemetry yang akan dibroadcast per batch
    telemetry_batch: List[str] = field(default_factory=list)

    # Sinkronisasi akses state antar coroutine
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # State recording CSV
    recording_active: bool = False
    csv_file_handle: Optional[object] = None
    csv_writer: Optional[csv.writer] = None
    csv_file_path: Optional[Path] = None


STATE = BridgeState()


# -------------------------------------------------
# Utility: kirim event/status sederhana ke semua UI
# -------------------------------------------------
async def broadcast_ui(message: str) -> None:
    """Kirim satu string message ke semua dashboard yang masih aktif."""
    async with STATE.lock:
        targets = list(STATE.ui_clients)

    if not targets:
        return

    stale = []
    for ws in targets:
        try:
            await ws.send(message)
        except ConnectionClosed:
            stale.append(ws)

    # Bersihkan client yang sudah putus agar state tetap rapi
    if stale:
        async with STATE.lock:
            for ws in stale:
                STATE.ui_clients.discard(ws)


# -------------------------------------------------
# Utility: mulai recording CSV ketika dapat REC:start
# -------------------------------------------------
def start_recording() -> str:
    """Aktifkan perekaman CSV jika belum aktif dan header sudah tersedia."""
    if STATE.recording_active:
        return "STATUS:recording_already_active"

    if not STATE.header_cols:
        return "STATUS:recording_failed_no_header"

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Format nama file: recording_YYYYMMDD_HHMMSS.csv
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RECORDINGS_DIR / f"recording_{stamp}.csv"

    # newline='' penting agar csv writer tidak menambah baris kosong di Windows
    fh = out_path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(fh)

    # Tulis header sesuai deklarasi HEADER: dari ESP32
    writer.writerow(STATE.header_cols)
    fh.flush()

    STATE.csv_file_handle = fh
    STATE.csv_writer = writer
    STATE.csv_file_path = out_path
    STATE.recording_active = True

    return f"STATUS:recording_started:{out_path.name}"


# ----------------------------------------------
# Utility: stop recording CSV ketika REC:stop
# ----------------------------------------------
def stop_recording() -> str:
    """Matikan perekaman CSV bila sedang aktif."""
    if not STATE.recording_active:
        return "STATUS:recording_not_active"

    if STATE.csv_file_handle:
        STATE.csv_file_handle.close()

    fname = STATE.csv_file_path.name if STATE.csv_file_path else "unknown.csv"

    STATE.csv_file_handle = None
    STATE.csv_writer = None
    STATE.csv_file_path = None
    STATE.recording_active = False

    return f"STATUS:recording_stopped:{fname}"


# -------------------------------------------------
# Task background: flush batch telemetry tiap 250 ms
# -------------------------------------------------
async def telemetry_batch_flusher() -> None:
    """Loop periodik untuk kirim batch telemetry ke semua UI client."""
    while True:
        await asyncio.sleep(BATCH_INTERVAL_S)

        async with STATE.lock:
            if not STATE.telemetry_batch:
                continue
            payload = "BATCH:\n" + "\n".join(STATE.telemetry_batch)
            STATE.telemetry_batch.clear()

        # Broadcast di luar lock supaya tidak blocking state access
        await broadcast_ui(payload)


# -------------------------------------------------
# Handler endpoint /esp -> menerima data dari ESP32
# -------------------------------------------------
async def handle_esp(ws: ServerConnection) -> None:
    """Terima telemetry dari ESP32 dan teruskan ke dashboard."""
    async with STATE.lock:
        STATE.esp_conn = ws

    logging.info("ESP32 connected")
    await broadcast_ui("STATUS:esp_connected")

    try:
        async for msg in ws:
            if not isinstance(msg, str):
                # Abaikan payload binary agar protokol tetap text-only
                continue

            # --------------------------
            # Parse pesan HEADER:<cols>
            # --------------------------
            if msg.startswith("HEADER:"):
                raw_cols = msg[len("HEADER:"):].strip()
                cols = [c.strip() for c in raw_cols.split(",") if c.strip()]

                # Validasi minimal harus ada micros di kolom pertama
                if not cols or cols[0] != "micros":
                    await broadcast_ui("STATUS:header_invalid_missing_micros")
                    continue

                # Validasi jumlah variabel (tanpa micros) <= 15
                if len(cols) - 1 > MAX_VARIABLES:
                    await broadcast_ui("STATUS:header_invalid_too_many_variables")
                    continue

                async with STATE.lock:
                    STATE.header_cols = cols

                # Forward header ke UI agar dashboard bisa setup series/table
                await broadcast_ui(msg)
                continue

            # -----------------------------------------------------
            # Parse frame data CSV biasa: micros,val1,val2,...,valN
            # -----------------------------------------------------
            async with STATE.lock:
                header_cols = list(STATE.header_cols) if STATE.header_cols else None

            # Jika data datang sebelum HEADER, beri tahu UI dan skip
            if not header_cols:
                await broadcast_ui("STATUS:data_ignored_no_header")
                continue

            row = [c.strip() for c in msg.strip().split(",")]

            # Validasi jumlah kolom data harus match jumlah header
            if len(row) != len(header_cols):
                await broadcast_ui("STATUS:data_invalid_column_mismatch")
                continue

            # Tambahkan ke buffer batch untuk dikirim periodik
            async with STATE.lock:
                STATE.telemetry_batch.append(msg)

            # Jika recording aktif, tulis juga ke CSV
            if STATE.recording_active and STATE.csv_writer and STATE.csv_file_handle:
                STATE.csv_writer.writerow(row)
                STATE.csv_file_handle.flush()

    except ConnectionClosed:
        logging.warning("ESP32 disconnected")
    finally:
        async with STATE.lock:
            if STATE.esp_conn is ws:
                STATE.esp_conn = None
        await broadcast_ui("STATUS:esp_disconnected")


# ---------------------------------------------------
# Handler endpoint /ui -> menerima command dari UI
# ---------------------------------------------------
async def handle_ui(ws: ServerConnection) -> None:
    """Terima command UI, lalu forward sesuai aturan protokol."""
    async with STATE.lock:
        STATE.ui_clients.add(ws)
        esp_online = STATE.esp_conn is not None
        header = list(STATE.header_cols) if STATE.header_cols else None

    logging.info("UI connected (total=%d)", len(STATE.ui_clients))

    # Kirim state awal supaya UI baru langsung sinkron
    await ws.send("STATUS:esp_connected" if esp_online else "STATUS:esp_disconnected")
    if header:
        await ws.send("HEADER:" + ",".join(header))

    try:
        async for msg in ws:
            if not isinstance(msg, str):
                continue

            # ---------------------------------
            # REC:start dan REC:stop (lokal)
            # ---------------------------------
            if msg == "REC:start":
                status = start_recording()
                await broadcast_ui(status)
                continue

            if msg == "REC:stop":
                status = stop_recording()
                await broadcast_ui(status)
                continue

            # ---------------------------------
            # CMD:<string> -> forward ke ESP32
            # ---------------------------------
            if msg.startswith("CMD:"):
                cmd_payload = msg[len("CMD:"):]
                async with STATE.lock:
                    esp = STATE.esp_conn

                if esp is None:
                    await ws.send("STATUS:cmd_failed_esp_offline")
                else:
                    await esp.send(cmd_payload)
                    await ws.send("STATUS:cmd_forwarded")
                continue

            # Pesan lain dianggap tidak valid menurut protokol
            await ws.send("STATUS:ui_message_unknown")

    except ConnectionClosed:
        logging.info("UI disconnected")
    finally:
        async with STATE.lock:
            STATE.ui_clients.discard(ws)


# ----------------------------------------------------
# Router utama path websocket: /esp atau /ui
# ----------------------------------------------------
async def ws_router(ws: ServerConnection) -> None:
    """Pilih handler berdasarkan path request websocket."""
    path = ws.request.path
    if path == "/esp":
        await handle_esp(ws)
    elif path == "/ui":
        await handle_ui(ws)
    else:
        await ws.send("STATUS:error_unknown_path")
        await ws.close(code=1008, reason="Unknown websocket path")


# -----------------------
# Entry point aplikasi
# -----------------------
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Task background untuk flush batch telemetry periodik
    flusher_task = asyncio.create_task(telemetry_batch_flusher())

    # Jalankan websocket server pada host/port yang ditentukan
    async with serve(ws_router, WS_HOST, WS_PORT, ping_interval=20, ping_timeout=20):
        logging.info("WebSocket server running at ws://%s:%d", WS_HOST, WS_PORT)
        await asyncio.Future()  # run forever

    # Safety cleanup (praktis tidak tercapai, kecuali shutdown)
    flusher_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
