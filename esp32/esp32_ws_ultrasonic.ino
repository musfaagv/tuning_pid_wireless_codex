/*
  ESP32 + HC-SR04 via WebSocket
  Adapted to send/receive data similar to esp32_ws_example.ino

  Endpoint:
    ws://ipLaptop:8765/esp
*/

#include <WiFi.h>
#include <WebSocketsClient.h>

const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASS = "YOUR_WIFI_PASSWORD";
const char* WS_HOST = "192.168.1.10";   // ganti dengan IP laptop
const uint16_t WS_PORT = 8765;
const char* WS_PATH = "/esp";

const int trigPin = 16;
const int echoPin = 17;

#define SOUND_SPEED 0.034f
#define CM_TO_INCH 0.393701f

WebSocketsClient ws;
unsigned long lastSendMs = 0;
const uint32_t SEND_PERIOD_MS = 500;

long durationUs = 0;
float distanceCm = 0.0f;
float distanceInch = 0.0f;

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  (void)payload;
  (void)length;

  switch (type) {
    case WStype_CONNECTED:
      Serial.println("[WS] connected");
      ws.sendTXT("HEADER:micros,distance_cm,distance_inch");
      break;

    case WStype_TEXT:
      // Tidak dipakai untuk saat ini.
      break;

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

void readUltrasonic() {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);

  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  durationUs = pulseIn(echoPin, HIGH, 35000);  // timeout ~6m

  if (durationUs <= 0) {
    distanceCm = -1.0f;
    distanceInch = -1.0f;
    return;
  }

  distanceCm = (durationUs * SOUND_SPEED) / 2.0f;
  distanceInch = distanceCm * CM_TO_INCH;
}

void setup() {
  Serial.begin(115200);

  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);

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

  readUltrasonic();

  char line[128];
  snprintf(
    line,
    sizeof(line),
    "%lu,%.2f,%.2f",
    micros(),
    distanceCm,
    distanceInch
  );

  ws.sendTXT(line);

  Serial.print("Distance (cm): ");
  Serial.println(distanceCm);
  Serial.print("Distance (inch): ");
  Serial.println(distanceInch);
}
