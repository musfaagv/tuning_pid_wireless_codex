# ESP32 ↔ WebSocket Server ↔ Web Dashboard

Sistem ini menyediakan pipeline telemetry real-time:
1. **ESP32** mengirim data CSV lewat WebSocket (`/esp`).
2. **Python server** menerima data, melakukan batching 250 ms, meneruskan ke UI (`/ui`), dan menangani recording CSV.
3. **Web dashboard** menampilkan grafik uPlot + tabel 20 data terakhir.

Struktur proyek:

```text
project-root/
├── dashboard/          # HTML, CSS, JS untuk web dashboard
├── server/             # Python WebSocket server + requirements.txt
└── esp32/              # ESP32 PlatformIO source code
```

---

## 1) Prasyarat

## Laptop / PC
- Python **3.10+**
- pip
- Browser modern (Chrome/Edge/Firefox)
- ESP32 dan laptop berada pada **WiFi yang sama**

## ESP32
- Board: **ESP32 DevKitC V4**
- Framework Arduino (via PlatformIO)
- Library WebSocket: Markus Sattler/Links2004 WebSockets `2.7.2`

---

## 2) Setup Server (WebSocket + Recording)

Masuk ke folder server dan install dependency:

```bash
cd server
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

Jalankan WebSocket server:

```bash
python server.py
```

Server listen pada:
- `ws://0.0.0.0:8765/esp` (untuk ESP32)
- `ws://0.0.0.0:8765/ui` (untuk browser dashboard)

File rekaman akan tersimpan di:
- `server/recordings/telemetry_YYYYmmdd_HHMMSS.csv`

---

## 3) Setup HTTP File Server untuk Dashboard

Buka terminal baru di root project:

```bash
cd dashboard
python3 -m http.server 8000
```

Dashboard diakses via:
- `http://<IP_LAPTOP>:8000`

> Gunakan IP lokal laptop (mis. `192.168.1.10`).

Cara cek IP laptop:
- Linux: `hostname -I`
- macOS: `ipconfig getifaddr en0` (atau interface aktif)
- Windows: `ipconfig`

---

## 4) Setup ESP32 (PlatformIO)

Edit file `esp32/src/main.cpp`:
- `WIFI_SSID`
- `WIFI_PASS`
- `WS_HOST` → isi dengan **IP laptop**

Contoh:

```cpp
const char* WIFI_SSID = "RumahKu";
const char* WIFI_PASS = "passwordwifi";
const char* WS_HOST = "192.168.1.10";
```

Build dan upload (dari folder `esp32`):

```bash
cd esp32
pio run -t upload
pio device monitor -b 115200
```

Monitor serial harus menunjukkan:
- WiFi connected
- WebSocket connected
- Header terkirim

---

## 5) Alur Menjalankan Sistem (Urutan Disarankan)

1. Jalankan WebSocket server (port 8765)
2. Jalankan HTTP server dashboard (port 8000)
3. Upload + jalankan firmware ESP32
4. Buka browser ke `http://<IP_LAPTOP>:8000`
5. Verifikasi status dashboard menjadi **connected**

---

## 6) Protokol Data (Sesuai Spesifikasi)

## ESP32 → Server
1. Header (sekali saat connect):

```text
HEADER:millis,variableName1,variableName2,...
```

2. Frame data kontinu:

```text
millisValue,value1,value2,...
```

Batas jumlah variabel telemetry: maks 15 (di luar `millis`).

## Browser → Server → ESP32
- Kirim command dari UI:
  - `CMD:<string>`
- Server meneruskan ke ESP32 sebagai plain `<string>`.

## Recording (hanya di server)
- `REC:start` → mulai tulis CSV
- `REC:stop` → stop + close file

---

## 7) Fitur Dashboard

- Grafik **uPlot** (line + point marker)
- Grid horizontal/vertikal tipis
- Pan/drag pada area plot
- Tombol **Pause/Resume** (render UI pause, data tetap diterima)
- Tombol **Zoom In/Zoom Out**
- Panel setting:
  - visibility tiap variabel
  - y-axis auto/manual min-max
  - mode x-axis: frame index / millis
  - visible points (sliding window)
- Tabel sinkron untuk **20 frame terakhir**
- Tombol recording start/stop
- Input command `CMD`

---

## 8) Troubleshooting

## Dashboard tidak connect
- Pastikan server Python berjalan di port 8765
- Pastikan browser membuka host yang benar (`http://<IP_LAPTOP>:8000`)
- Pastikan firewall tidak memblokir port 8765/8000

## ESP32 tidak connect
- Cek SSID/password WiFi
- Pastikan `WS_HOST` benar (IP laptop, bukan localhost)
- Pastikan laptop dan ESP32 pada subnet yang sama

## Data masuk tapi grafik kosong
- Pastikan header dikirim format `HEADER:millis,...`
- Pastikan jumlah kolom data sama dengan header
- Cek console browser untuk error JavaScript

## Rekaman kosong
- Pastikan klik **Start Recording** sebelum data masuk
- Cek folder `server/recordings/`

---

## 9) Catatan Pengembangan Lanjutan

- Tambahkan autentikasi command jika dipakai di jaringan non-trusted.
- Pertimbangkan ring-buffer di server untuk histori lebih panjang.
- Untuk throughput tinggi, bisa pertimbangkan kompresi/paket biner.
- Jika multi ESP32 diperlukan, ubah state server agar device diidentifikasi per ID/path.
