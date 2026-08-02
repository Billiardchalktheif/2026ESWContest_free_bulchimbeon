/*
  불침번 — 자탐1 노드: 차동식구역 (ESP32 DevKitC)
  역할: TS0202 온도센서로 온도 상승률(dT/dt)을 계산해 화재 재현(열풍기로 수동 가열)을
        감지하고, 루프저항도 함께 감시한다(z-score 판정은 서버 공통 로직,
        server/anomaly_detection.py 참고).

  v4에서 확정: 자탐 2구역은 센서 구성이 완전히 다르다. 기존 fire_alarm_node.ino
  (2구역 동일 구성 가정)를 differential/photoelectric 두 파일로 분리했다.
  이 파일은 차동식(온도) 전용이며, MQ-2/DHT22(비화재보 판별 AI용)는 이 구역에 없다
  — 그건 fire_alarm_photoelectric_node.ino가 담당한다.

  확정 핀 배치(§3):
    - ADS1115 I2C: SDA -> GPIO21, SCL -> GPIO22
    - TS0202 온도센서: ADS1115 채널 A0
    - 저항분압(루프 tap): ADS1115 채널 A1

  화재 재현 방법: 열풍기로 직접 가열한다. **열풍기는 ESP32에 연결되지 않은 완전
  수동 조작**이다 — 그래서 v3까지 있던 "니크롬선 가열시험 릴레이"는 이 버전에서
  제거했다(더 이상 ESP32가 열원을 제어하지 않으므로 필요 없어짐).

  ⚠️ 샘플링 주기 트레이드오프(실측 후 재검토 필요): 온도상승률은 화재처럼 "몇 초~몇 분"
  단위로 빠르게 진행되는 현상을 잡아야 해서 SEND_INTERVAL_MS을 짧게(5초) 잡았다.
  반면 루프저항 열화(z-score 대상)는 원래 시간~일 단위로 느리게 진행되는 현상이라,
  이렇게 짧은 주기로 같이 보내면 서버 쪽 z-score 이동평균 창(window)이 짧은 시간만
  담게 되어 장기 부식 추세 포착에는 불리해진다. 화재대응 속도를 우선한 트레이드오프
  — 실측해서 정말 문제가 되면 온도/저항을 서로 다른 로컬 주기로 분리하는 것도 고려할 것.

  견고화 내역: 다른 노드와 동일 패턴 (millis() 논블로킹, WiFi 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <time.h>

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "fire_zone_differential_01";
const char* ZONE_NAME = "차동식구역";
const char* ZONE_TYPE = "differential";  // db/schema.sql의 fire_alarm_log.zone_type과 일치

const float SUPPLY_VOLTAGE = 24.0;   // 자탐 루프 인가전압 (실측값으로 교체)
const float SHUNT_OHM = 10.0;        // 기준 션트 저항 (실측/사양값으로 교체)
const int SAMPLE_COUNT = 10;         // 저항 단일값 노이즈 감소용 평균 횟수

// TS0202 온도센서 전압 -> 섭씨 변환 (실측 후 정확한 계수로 교체 — 데이터시트 특성곡선 확인 필요)
const float TEMP_V_MIN = 0.5;    // 센서 0도 출력전압
const float TEMP_V_MAX = 4.5;    // 센서 최대범위 출력전압
const float TEMP_C_MAX = 100.0;  // 센서 최대범위 온도(°C)

const unsigned long SEND_INTERVAL_MS = 5000;   // 화재 대응 속도 우선(파일 상단 트레이드오프 참고)
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

WiFiUDP udp;
Adafruit_ADS1115 ads;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;
unsigned long lastWifiCheckMs = 0;
float lastTempC = NAN;     // 직전 측정 온도 — dT/dt 계산용
double lastTempTs = -1;    // 직전 측정 시각(epoch 초)

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
    configTime(NTP_GMT_OFFSET_SEC, 0, "pool.ntp.org", "time.google.com");
  } else {
    Serial.println(" 연결 실패 - loop()에서 계속 재시도함");
  }
}

void ensureWiFiConnected() {
  if (millis() - lastWifiCheckMs < WIFI_CHECK_INTERVAL_MS) return;
  lastWifiCheckMs = millis();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi 끊김 감지 -> 재연결 시도");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
}

double getEpochSeconds() {
  time_t now;
  time(&now);
  if (now < NTP_SYNCED_THRESHOLD) {
    return millis() / 1000.0;
  }
  return (double)now;
}

// A1 채널 — 션트 저항 양단 전압을 여러 번 측정해 평균낸 뒤 옴의 법칙으로 루프저항 계산
float measureLoopResistanceOhm() {
  float sumVolts = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    int16_t raw = ads.readADC_SingleEnded(1);   // A1
    sumVolts += ads.computeVolts(raw);
    delay(10);  // ADS1115 변환 안정화 대기
  }
  float vShunt = sumVolts / SAMPLE_COUNT;
  if (vShunt <= 0.0001) {
    return -1;  // 단선(개방) 의심
  }
  float current = vShunt / SHUNT_OHM;
  return (SUPPLY_VOLTAGE - vShunt) / current;
}

// A0 채널 — TS0202 온도센서
float measureTempC() {
  int16_t raw = ads.readADC_SingleEnded(0);  // A0
  float v = ads.computeVolts(raw);
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
  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();

  Wire.begin();
  if (!ads.begin()) {
    Serial.println("ADS1115 초기화 실패 — 배선 확인 필요");
  }

  connectWiFi();
}

void loop() {
  ensureWiFiConnected();

  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float resistance = measureLoopResistanceOhm();
  if (resistance < 0) {
    Serial.println("루프 개방(단선) 의심 — 이번 측정 건너뜀");
    return;
  }

  float tempC = measureTempC();
  double nowTs = getEpochSeconds();
  float riseRate = NAN;
  if (!isnan(lastTempC) && lastTempTs > 0) {
    double dt = nowTs - lastTempTs;
    if (dt > 0) riseRate = (tempC - lastTempC) / dt;  // °C/초
  }
  lastTempC = tempC;
  lastTempTs = nowTs;

  sendPacket(resistance, tempC, riseRate);
}
