/* =====================================================================
   ESP32 WebSocket Client Example
   ---------------------------------------------------------------------
   Target library/version sesuai spesifikasi:
   - ESP32 Arduino Core 3.3.7
   - WebSockets by Markus Sattler v2.7.2

   Fungsi utama sketch:
   1) Connect Wi-Fi
   2) Connect ke websocket server laptop endpoint /esp
   3) Kirim HEADER sekali saat connect
   4) Kirim data telemetry berkala (target 100 Hz)
   5) Terima command string dari server (asalnya dari dashboard CMD:...)
   6) Reconnect otomatis dengan exponential backoff (1s, 2s, ..., max 30s),
      maksimal 10 percobaan, lalu berhenti mencoba.

   NOTE:
   - Ganti WIFI_SSID, WIFI_PASS, LAPTOP_IP sesuai jaringan Anda.
   - Contoh data sensor di bawah adalah dummy; silakan ganti dengan MPU9250,
     encoder motor, PID output aktual, dll.
   ===================================================================== */

#include <WiFi.h>
#include <WebSocketsClient.h>

// -------------------------
// Konfigurasi jaringan Wi-Fi
// -------------------------
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// --------------------------------------------
// Konfigurasi endpoint websocket pada laptop
// ws://[laptopIP]:8765/esp
// --------------------------------------------
const char* LAPTOP_IP = "192.168.1.100";
const uint16_t WS_PORT = 8765;
const char* WS_PATH = "/esp";

// --------------------------------
// Objek WebSocket client dari library
// --------------------------------
WebSocketsClient wsClient;

// --------------------------------------------------
// Timing loop telemetry (target 100 Hz => 10 ms/frame)
// --------------------------------------------------
const uint32_t TELEMETRY_INTERVAL_US = 10000;
uint32_t lastSendUs = 0;

// ------------------------------------------------------
// Header telemetry (maks 15 variabel selain micros)
// Contoh di bawah pakai 4 variabel dummy
// ------------------------------------------------------
const char* TELEMETRY_HEADER = "HEADER:micros,roll,pitch,yaw,motorCmd";

// -------------------------------------------------
// State reconnect exponential backoff untuk websocket
// -------------------------------------------------
bool wsConnected = false;
bool stopReconnect = false;         // true jika sudah capai max attempts
uint8_t reconnectAttempt = 0;       // jumlah percobaan saat ini
uint32_t reconnectDelayMs = 1000;   // mulai dari 1 detik
const uint8_t MAX_RECONNECT_ATTEMPTS = 10;
const uint32_t MAX_BACKOFF_MS = 30000;
uint32_t nextReconnectAtMs = 0;

// -------------------------------------------------
// Fungsi bantu: kirim status ke Serial monitor
// -------------------------------------------------
void logLine(const String& s) {
  Serial.println(s);
}

// --------------------------------------------------
// Proses command teks yang diterima dari websocket
// (server sudah menghapus prefix CMD: dari dashboard)
// --------------------------------------------------
void handleCommand(const String& cmd) {
  logLine("[CMD] Received: " + cmd);

  // TODO: mapping command ke aksi nyata kontrol motor/PID
  // Contoh sederhana:
  // if (cmd == "MOTOR:STOP") { ... }
  // else if (cmd.startsWith("KP:")) { ... }
}

// ----------------------------------------------
// Callback event websocket dari library client
// ----------------------------------------------
void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    // -----------------------------------
    // Event: websocket berhasil connected
    // -----------------------------------
    case WStype_CONNECTED: {
      wsConnected = true;
      stopReconnect = false;
      reconnectAttempt = 0;
      reconnectDelayMs = 1000;

      logLine("[WS] Connected to server");

      // Kirim HEADER sekali setiap konek baru
      wsClient.sendTXT(TELEMETRY_HEADER);
      break;
    }

    // -----------------------------
    // Event: websocket disconnected
    // -----------------------------
    case WStype_DISCONNECTED: {
      wsConnected = false;
      logLine("[WS] Disconnected");

      // Jadwalkan reconnect pertama jika belum stop permanen
      if (!stopReconnect) {
        nextReconnectAtMs = millis() + reconnectDelayMs;
      }
      break;
    }

    // --------------------------------------------
    // Event: menerima text frame (command string)
    // --------------------------------------------
    case WStype_TEXT: {
      String cmd = String((char*)payload).substring(0, length);
      handleCommand(cmd);
      break;
    }

    default:
      // Event lain (PING/PONG/BIN) tidak dipakai pada contoh ini
      break;
  }
}

// ----------------------------------------------
// Mulai koneksi websocket pertama kali
// ----------------------------------------------
void startWsConnection() {
  wsClient.begin(LAPTOP_IP, WS_PORT, WS_PATH);
  wsClient.onEvent(onWsEvent);
  wsClient.setReconnectInterval(0);  // nonaktifkan auto-reconnect bawaan library
  wsClient.enableHeartbeat(15000, 3000, 2);
}

// -----------------------------------------------------------
// Logic reconnect manual (exponential backoff, max 10 attempt)
// -----------------------------------------------------------
void handleReconnect() {
  if (wsConnected || stopReconnect) {
    return;
  }

  uint32_t now = millis();
  if (now < nextReconnectAtMs) {
    return;
  }

  reconnectAttempt++;

  if (reconnectAttempt > MAX_RECONNECT_ATTEMPTS) {
    stopReconnect = true;
    logLine("[WS] Reconnect failed: reached max attempts (10). Stopping retries.");
    return;
  }

  logLine("[WS] Reconnect attempt #" + String(reconnectAttempt) +
          " (delay=" + String(reconnectDelayMs) + " ms)");

  // Putuskan instance lama lalu inisialisasi koneksi baru
  wsClient.disconnect();
  startWsConnection();

  // Atur jadwal percobaan berikutnya dengan backoff x2 (cap 30 detik)
  reconnectDelayMs = min(MAX_BACKOFF_MS, reconnectDelayMs * 2);
  nextReconnectAtMs = now + reconnectDelayMs;
}

// ---------------------------------
// Dummy generator data telemetry
// ---------------------------------
void sendTelemetryIfDue() {
  if (!wsConnected) {
    return;
  }

  uint32_t nowUs = micros();
  if ((nowUs - lastSendUs) < TELEMETRY_INTERVAL_US) {
    return;
  }
  lastSendUs = nowUs;

  // ---------------------------------------------------------
  // Contoh data dummy (silakan ganti dengan pembacaan sensor nyata)
  // ---------------------------------------------------------
  float t = nowUs / 1000000.0f;
  float roll = 10.0f * sinf(t);
  float pitch = 8.0f * cosf(t * 0.8f);
  float yaw = 5.0f * sinf(t * 1.5f);
  float motorCmd = 120.0f + 30.0f * sinf(t * 0.3f);

  // Format frame data: micros,val1,val2,val3,val4
  String line = String(nowUs) + "," +
                String(roll, 6) + "," +
                String(pitch, 6) + "," +
                String(yaw, 6) + "," +
                String(motorCmd, 6);

  wsClient.sendTXT(line);
}

// -----------------------
// Setup awal board ESP32
// -----------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  // ---------------------------------
  // Koneksi ke Wi-Fi AP lokal/lab
  // ---------------------------------
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  logLine("[WIFI] Connecting...");

  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  logLine("[WIFI] Connected, IP=" + WiFi.localIP().toString());

  // Mulai websocket connection ke laptop
  startWsConnection();
}

// --------------------------
// Loop utama firmware ESP32
// --------------------------
void loop() {
  // Jalankan internal event loop websocket library
  wsClient.loop();

  // Reconnect logic manual sesuai spesifikasi
  handleReconnect();

  // Kirim telemetry periodik 100 Hz
  sendTelemetryIfDue();
}
