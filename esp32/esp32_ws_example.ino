/*
  ESP32 WebSocket telemetry example
  Library: WebSockets by Markus Sattler v2.7.2

  Endpoint:
    ws://ipLaptop:8765/esp

  Data format:
    HEADER:micros,var1,var2,...
    micros,val1,val2,...
*/

#include <WiFi.h>
#include <WebSocketsClient.h>

const char* WIFI_SSID = "XxX";
const char* WIFI_PASS = "12345678";
const char* WS_HOST = "10.47.100.165";   // ganti dengan IP laptop
const uint16_t WS_PORT = 8765;
const char* WS_PATH = "/esp";

WebSocketsClient ws;
unsigned long lastSendMs = 0;
const uint32_t SEND_PERIOD_MS = 10; // 100 Hz

float gainKp = 0.10f;
float gainKi = 0.01f;
float gainKd = 0.05f;

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.println("[WS] connected");
      ws.sendTXT("HEADER:micros,setpoint,measurement,control,kp");
      break;

    case WStype_TEXT: {
      String cmd = String((char*)payload).substring(0, length);
      Serial.print("[WS] command: ");
      Serial.println(cmd);

      // contoh command: kp=0.12
      if (cmd.startsWith("kp=")) {
        gainKp = cmd.substring(3).toFloat();
      }
      break;
    }

    case WStype_DISCONNECTED:
      Serial.println("[WS] disconnected");
      break;

    default:
      break;
  }
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi connected. IP: ");
  Serial.println(WiFi.localIP());
}

void setup() {
  Serial.begin(115200);
  connectWifi();

  ws.begin(WS_HOST, WS_PORT, WS_PATH);
  ws.onEvent(onWsEvent);
  ws.setReconnectInterval(2000);
}

void loop() {
  ws.loop();

  unsigned long now = millis();
  if (now - lastSendMs < SEND_PERIOD_MS) {
    return;
  }
  lastSendMs = now;

  // Dummy telemetry: ganti dengan variabel proyek Anda.
  float t = micros() / 1000000.0f;
  float setpoint = 1.0f;
  float measurement = 0.5f + 0.5f * sinf(2.0f * PI * 0.5f * t);
  float control = gainKp * (setpoint - measurement) + gainKi + gainKd;

  char line[128];
  snprintf(
    line,
    sizeof(line),
    "%lu,%.4f,%.4f,%.4f,%.4f",
    micros(),
    setpoint,
    measurement,
    control,
    gainKp
  );
  ws.sendTXT(line);
}
