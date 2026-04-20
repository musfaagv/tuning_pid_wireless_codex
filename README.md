# ESP32 ↔ Python WebSocket Telemetry Dashboard

Sistem ini menyediakan jalur data real-time antara ESP32 dan dashboard web melalui WebSocket.

## Arsitektur

- ESP32 connect ke endpoint `ws://ipLaptop:8765/esp`
- Browser dashboard connect ke endpoint `ws://ipLaptop:8765/ui`
- Python server bertindak sebagai:
  - WebSocket server untuk ESP32 + UI
  - HTTP static server untuk dashboard
  - Perekam CSV telemetry

## Struktur Data

- Pesan awal dari ESP32: `HEADER:micros,var1,var2,...`
- Pesan data berikutnya: `microsValue,val1,val2,...`
- Pesan command dari UI ke ESP32: string biasa (contoh `kp=0.12`)

Batas maksimum kolom telemetry: **15 variabel per sesi**.

## Menjalankan Python Server

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install websockets==16.0
python3 server.py
```

Lalu buka dashboard: `http://localhost:8765`

## Fitur Dashboard

- Plot real-time menggunakan **uPlot**
- Render grafik setiap 10 frame telemetry
- Tabel 20 frame data terakhir
- Input command ke ESP32
- Tombol `Start CSV` / `Stop CSV`

CSV disimpan ke folder `data/`.

## Upload Sketch ESP32

Buka file berikut di Arduino IDE:

- `esp32/esp32_ws_example.ino`

Pastikan mengubah konfigurasi:

- `WIFI_SSID`
- `WIFI_PASS`
- `WS_HOST` (IP laptop)

## Catatan Kinerja

- Sketch mengirim data setiap `10 ms` (target 100 Hz)
- Dashboard update grafik setiap 10 telemetry frame untuk menjaga UI tetap ringan
