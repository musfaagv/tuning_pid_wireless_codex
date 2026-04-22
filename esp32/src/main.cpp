/*
  ESP32 telemetry client example for laptop-hosted WebSocket bridge.

  Behavior:
  - Connect WiFi
  - Connect ws://<laptopIP>:8765/esp
  - Send HEADER once per WebSocket connection
  - Send CSV telemetry frames around 100 Hz
  - Auto reconnect if socket drops
*/

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>

// ===== User configuration =====
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";

// Laptop IP where Python websocket server runs.
const char* WS_HOST = "192.168.1.10";
const uint16_t WS_PORT = 8765;
const char* WS_PATH = "/esp";

// Target sample interval = 10ms => ~100 Hz.
constexpr uint32_t SAMPLE_INTERVAL_MS = 10;

WebSocketsClient wsClient;
uint32_t lastSampleAt = 0;

// Example tunable variables for telemetry stream.
float variableName1 = 0.0f;
float variableName2 = 0.0f;
float variableName3 = 0.0f;

void connectWiFi() {
  // Connect STA to local network.
  Serial.printf("Connecting to WiFi SSID: %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print('.');
  }

  Serial.println();
  Serial.printf("WiFi connected. ESP IP: %s\n", WiFi.localIP().toString().c_str());
}

void sendHeader() {
  // Format required by spec: HEADER:millis,var1,var2,...
  wsClient.sendTXT("HEADER:millis,variableName1,variableName2,variableName3");
}

void sendTelemetry() {
  // Replace with real sensor reads / PID values.
  uint32_t now = millis();

  // Example signal generation so chart moves immediately.
  variableName1 = sinf(now / 500.0f) * 30.0f + 40.0f;
  variableName2 = cosf(now / 650.0f) * 20.0f + 50.0f;
  variableName3 = (float)(now % 1000) / 10.0f;

  char frame[128];
  snprintf(frame, sizeof(frame), "%lu,%.3f,%.3f,%.3f",
           (unsigned long)now, variableName1, variableName2, variableName3);

  wsClient.sendTXT(frame);
}

void onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] Disconnected");
      break;

    case WStype_CONNECTED:
      Serial.printf("[WS] Connected to: %s\n", payload);
      // Header must be sent once after each (re)connect.
      sendHeader();
      break;

    case WStype_TEXT: {
      // Server forwards CMD:<text> as plain <text> to ESP32.
      String cmd = String((char*)payload).substring(0, length);
      Serial.printf("[WS] CMD received: %s\n", cmd.c_str());

      // Example command handling placeholder.
      // You can parse commands like: "kp=1.20" and apply to PID values.
      break;
    }

    default:
      break;
  }
}

void setupWebSocket() {
  // Begin websocket client and register callback.
  wsClient.begin(WS_HOST, WS_PORT, WS_PATH);
  wsClient.onEvent(onWebSocketEvent);
  wsClient.setReconnectInterval(2000);  // Auto reconnect every 2s when disconnected.
}

void setup() {
  Serial.begin(115200);
  delay(500);

  connectWiFi();
  setupWebSocket();
}

void loop() {
  // Maintain websocket state machine and reconnect logic.
  wsClient.loop();

  // Keep WiFi alive (optional self-healing).
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    setupWebSocket();
  }

  // Send telemetry at ~100 Hz when websocket is connected.
  uint32_t now = millis();
  if (wsClient.isConnected() && (now - lastSampleAt >= SAMPLE_INTERVAL_MS)) {
    lastSampleAt = now;
    sendTelemetry();
  }
}
