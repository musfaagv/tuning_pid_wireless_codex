# ESP32 ↔ WebSocket ↔ Web Dashboard

Implementasi lengkap sesuai spesifikasi:

## Struktur
- `server/ws_bridge_server.py`: WebSocket bridge server (Python `websockets==16.0`)
- `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css`: dashboard realtime (uPlot CDN)
- `esp32/esp32_ws_client_example.ino`: contoh sketch ESP32 client

## Menjalankan server websocket
```bash
cd server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ws_bridge_server.py
```

## Menjalankan HTTP static server untuk dashboard
Dari root project:
```bash
cd dashboard
python3 -m http.server 8000
```
Buka browser ke `http://[laptopIP]:8000`.

## Endpoint
- ESP32: `ws://[laptopIP]:8765/esp`
- Dashboard: `ws://[laptopIP]:8765/ui`
- HTTP dashboard: `http://[laptopIP]:8000`

## Catatan
- Folder CSV recording otomatis dibuat pada `recordings/` saat `REC:start`.
- Nama file mengikuti pola `recording_YYYYMMDD_HHMMSS.csv`.
