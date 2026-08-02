#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>

// ---- 설정값 ----
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "fire_zone_photoelectric_01";
const char* ZONE_NAME = "광전식구역";
const char* ZONE_TYPE = "photoelectric";

// ---- 시뮬레이션 전용 핀 (실물은 ADS1115 A0/A1 + DHT22 GPIO4 사용) ----
const int MQ2_SIM_PIN = 4;          // 포텐셔미터1 (MQ-2 대신) — ADC 가능 핀(0~4)
const int RESISTANCE_SIM_PIN = 3;   // 포텐셔미터2 (루프저항 대신) — ADC 가능 핀
const int DHT_PIN = 0;              // DHT22는 디지털이라 ADC 제약 없음

const float SUPPLY_VOLTAGE = 24.0;
const float SHUNT_OHM = 10.0;

const unsigned long SEND_INTERVAL_MS = 2000;   // 시뮬레이션 확인용으로 단축
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;

WiFiUDP udp;
DHT dht(DHT_PIN, DHT22);
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;
unsigned long lastWifiCheckMs = 0;

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("WiFi 연결 중");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(300);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" 연결됨: " + WiFi.localIP().toString());
  } else {
    Serial.println(" 연결 실패 - 시뮬레이션에서는 정상, 로직 검증엔 문제없음");
  }
}

double getEpochSeconds() {
  return millis() / 1000.0;
}

// GPIO3 직접 읽기 (실물은 ADS1115 A1)
float measureLoopResistanceOhm() {
  int raw = analogRead(RESISTANCE_SIM_PIN);
  float vShunt = (raw / 4095.0) * 3.3;
  if (vShunt <= 0.01) {
    return -1;  // 단선(개방) 의심
  }
  float current = vShunt / SHUNT_OHM;
  return (SUPPLY_VOLTAGE - vShunt) / current;
}

// GPIO4 직접 읽기 (실물은 ADS1115 A0, MQ-2 원시값)
int16_t readMq2Raw() {
  return analogRead(MQ2_SIM_PIN);   // 0~4095 (ESP32-C3는 12bit)
}

void sendPacket(float loopResistanceOhm, int16_t mq2Raw, float tempC, float humidityPct) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_ID;
  doc["device_type"] = "fire_alarm";
  doc["zone"] = ZONE_NAME;
  doc["zone_type"] = ZONE_TYPE;
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["loop_resistance_ohm"] = loopResistanceOhm;
  doc["mq2_raw"] = mq2Raw;
  if (!isnan(tempC)) doc["temp_c"] = tempC;
  if (!isnan(humidityPct)) doc["humidity_pct"] = humidityPct;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));

  Serial.print(">> 전송패킷: ");
  Serial.println(buf);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();
  dht.begin();
  connectWiFi();
  Serial.println("광전식구역 시뮬레이션 시작 - 포텐셔미터1(GPIO4, MQ-2 대신)을 움직여보세요");
}

void loop() {
  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float resistance = measureLoopResistanceOhm();
  if (resistance < 0) {
    Serial.println("루프 개방(단선) 의심 — 이번 측정 건너뜀");
    return;
  }

  int16_t mq2Raw = readMq2Raw();
  float tempC = dht.readTemperature();
  float humidityPct = dht.readHumidity();

  Serial.printf("MQ2raw: %d | 온도: %.1f C | 습도: %.1f %% | 루프저항: %.1f ohm\n",
                mq2Raw, tempC, humidityPct, resistance);

  sendPacket(resistance, mq2Raw, tempC, humidityPct);
}