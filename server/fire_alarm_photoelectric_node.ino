/*
  불침번 — 자탐2 노드: 광전식/연기구역 (ESP32 DevKitC)
  역할: MQ-2(가스농도) + DHT22(온습도) 조합으로 "비화재보(오작동) 판별" feature를
        전송한다. 서버(server/nuisance_alarm_classifier.py)가 RandomForest로
        fire(진짜 화재)/cooking(조리 수증기)/normal(평상시) 3클래스를 분류한다
        — 이 구역만 AI 적용 대상이다(§4). 루프저항도 함께 감시한다
        (z-score 판정은 서버 공통 로직, server/anomaly_detection.py 참고).

  v4에서 확정: 자탐 2구역은 센서 구성이 완전히 다르다. 기존 fire_alarm_node.ino
  (2구역 동일 구성 가정)를 differential/photoelectric 두 파일로 분리했다.
  이 파일은 광전식(연기/비화재보 판별) 전용이며, TS0202(차동식 온도상승률 전용)는
  이 구역에 없다 — 그건 fire_alarm_differential_node.ino가 담당한다.

  확정 핀 배치(§3):
    - ADS1115 I2C(공통): SDA -> GPIO21, SCL -> GPIO22
    - 저항분압(루프 tap): ADS1115 채널 A1
    - MQ-2 가스센서: ADS1115 채널 A0
    - DHT22 온습도: GPIO4

  발연/가습 재현 방법: 가습기(수증기)/발연원(연기)으로 재현한다. **둘 다 ESP32에
  연결되지 않은 완전 수동 조작**이다 — 그래서 v3까지 있던 "니크롬선 가열시험 릴레이"는
  애초에 이 구역과 무관했고, 이 파일에는 포함하지 않는다.

  ⚠️ 샘플링 주기: fire_alarm_differential_node.ino와 동일한 이유로 SEND_INTERVAL_MS을
  짧게(5초) 잡았다 — 비화재보 판별도 가습기/발연원을 즉석에서 조작하는 시연 상황을
  빠르게 반영해야 하기 때문. 루프저항 z-score의 장기 추세 포착과는 트레이드오프
  관계이니 실측 후 재검토할 것 (differential 노드 상단 설명 참고).

  견고화 내역: 다른 노드와 동일 패턴 (millis() 논블로킹, WiFi 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <DHT.h>
#include <time.h>

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "fire_zone_photoelectric_01";
const char* ZONE_NAME = "광전식구역";
const char* ZONE_TYPE = "photoelectric";  // db/schema.sql의 fire_alarm_log.zone_type과 일치

const int DHT_PIN = 4;

const float SUPPLY_VOLTAGE = 24.0;   // 자탐 루프 인가전압 (실측값으로 교체)
const float SHUNT_OHM = 10.0;        // 기준 션트 저항 (실측/사양값으로 교체)
const int SAMPLE_COUNT = 10;         // 저항 단일값 노이즈 감소용 평균 횟수

const unsigned long SEND_INTERVAL_MS = 5000;   // 비화재보 판별 시연 반응성 우선(파일 상단 참고)
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

WiFiUDP udp;
Adafruit_ADS1115 ads;
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

// A0 채널 — MQ-2 가스센서 원시값 (비화재보 판별 AI의 입력 feature 중 하나)
int16_t readMq2Raw() {
  return ads.readADC_SingleEnded(0);   // A0
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
  // label 필드는 이 노드에서 보내지 않는다 — 학습 데이터 수집시엔 더미
  // 생성기(simulate/dummy_generator.py)나 별도 수동 절차로 채워 넣는다.

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
  dht.begin();

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
  int16_t mq2Raw = readMq2Raw();
  float tempC = dht.readTemperature();
  float humidityPct = dht.readHumidity();

  sendPacket(resistance, mq2Raw, tempC, humidityPct);
}
