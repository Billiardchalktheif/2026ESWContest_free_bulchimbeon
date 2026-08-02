#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- 설정값 ----
const char* WIFI_SSID = "Wokwi-GUEST";   // Wokwi 기본 제공 가상 WiFi
const char* WIFI_PASS = "";
const char* SERVER_IP = "192.168.0.10";  // 시뮬레이션에선 실제 전송 안 되니 아무 값
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "fire_zone_differential_01";
const char* ZONE_NAME = "차동식구역";
const char* ZONE_TYPE = "differential";

// ---- 시뮬레이션 전용 핀 (실물은 ADS1115 A0/A1 사용, 여기선 GPIO 직접 사용) ----
const int TEMP_SIM_PIN = 4;         // 포텐셔미터1 (TS0202 대신)
const int RESISTANCE_SIM_PIN = 3;   // 포텐셔미터2 (루프저항 대신)

const float SUPPLY_VOLTAGE = 24.0;
const float SHUNT_OHM = 10.0;

const float TEMP_V_MIN = 0.5;
const float TEMP_V_MAX = 4.5;
const float TEMP_C_MAX = 100.0;

const unsigned long SEND_INTERVAL_MS = 2000;   // 시뮬레이션에서 빠르게 확인하려고 2초로 단축
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

WiFiUDP udp;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;
unsigned long lastWifiCheckMs = 0;
float lastTempC = NAN;
double lastTempTs = -1;

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
  return millis() / 1000.0;   // 시뮬레이션은 NTP 동기화 없이 millis 기준으로 충분
}

// GPIO5 직접 읽기 (실물은 ADS1115 A1)
float measureLoopResistanceOhm() {
  int raw = analogRead(RESISTANCE_SIM_PIN);      // 0~4095
  Serial.print("[디버그] GPIO5 raw값: ");
  Serial.println(raw);   // 이 줄 추가 — 포텐셔미터 돌릴 때 이 값이 바뀌는지 확인

  float vShunt = (raw / 4095.0) * 3.3;
  if (vShunt <= 0.01) {
    return -1;  // 단선(개방) 의심
  }
  float current = vShunt / SHUNT_OHM;
  return (SUPPLY_VOLTAGE - vShunt) / current;
}

// GPIO4 직접 읽기 (실물은 ADS1115 A0)
float measureTempC() {
  int raw = analogRead(TEMP_SIM_PIN);
  float v = (raw / 4095.0) * 3.3;
  float ratio = (v - TEMP_V_MIN) / (TEMP_V_MAX - TEMP_V_MIN);
  return ratio * TEMP_C_MAX;
}

void sendPacket(float loopResistanceOhm, float tempC, float tempRiseRate) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_ID;
  doc["device_type"] = "fire_alarm";
  doc["zone"] = ZONE_NAME;
  doc["zone_type"] = ZONE_TYPE;
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["loop_resistance_ohm"] = loopResistanceOhm;
  doc["temp_c"] = tempC;
  if (!isnan(tempRiseRate)) doc["temp_rise_rate"] = tempRiseRate;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));

  // 시뮬레이션에선 실제 서버가 없어서 전송은 의미 없지만, 로직 검증용으로 콘솔에 출력
  Serial.print(">> 전송패킷: ");
  Serial.println(buf);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();
  connectWiFi();
  Serial.println("차동식구역 시뮬레이션 시작 - 포텐셔미터1(GPIO4)을 움직여보세요");
}

void loop() {
  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float resistance = measureLoopResistanceOhm();
  if (resistance < 0) {
    Serial.println("루프 개방(단선) 의심 — 이번 측정 건너뜀 (포텐셔미터2를 0 근처에서 살짝 올려보세요)");
    return;
  }

  float tempC = measureTempC();
  double nowTs = getEpochSeconds();
  float riseRate = NAN;
  if (!isnan(lastTempC) && lastTempTs > 0) {
    double dt = nowTs - lastTempTs;
    if (dt > 0) riseRate = (tempC - lastTempC) / dt;
  }
  lastTempC = tempC;
  lastTempTs = nowTs;

  Serial.printf("온도: %.1f C | 상승률: %.3f C/s | 루프저항: %.1f ohm\n", tempC, riseRate, resistance);

  sendPacket(resistance, tempC, riseRate);
}
