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
    - 점검모드 택트스위치: GPIO27 (TEST_MODE_PIN, 배선 확정되면 실제 핀 번호로 교체)

  화재 재현 방법: 열풍기로 직접 가열한다. **열풍기는 ESP32에 연결되지 않은 완전
  수동 조작**이다 — 그래서 v3까지 있던 "니크롬선 가열시험 릴레이"는 이 버전에서
  제거했다(더 이상 ESP32가 열원을 제어하지 않으므로 필요 없어짐).

  ⚠️ 샘플링 주기 트레이드오프(실측 후 재검토 필요): 온도상승률은 화재처럼 "몇 초~몇 분"
  단위로 빠르게 진행되는 현상을 잡아야 해서 SEND_INTERVAL_MS을 짧게(5초) 잡았다.
  반면 루프저항 열화(z-score 대상)는 원래 시간~일 단위로 느리게 진행되는 현상이라,
  이렇게 짧은 주기로 같이 보내면 서버 쪽 z-score 이동평균 창(window)이 짧은 시간만
  담게 되어 장기 부식 추세 포착에는 불리해진다. 화재대응 속도를 우선한 트레이드오프
  — 실측해서 정말 문제가 되면 온도/저항을 서로 다른 로컬 주기로 분리하는 것도 고려할 것.

  점검모드(Test Mode, 2026-08-23 추가): 부작동/작동시험(열원 시험) 중에는 온도상승률
  판정 로직이 상시로 그대로 반응해서 서버가 진짜 화재 경보를 내보낸다 — 시험과 실제
  비상상황을 구분 못 하는 문제. 그래서 물리 택트스위치로 "지금 점검 중"을 서버에
  알려주기만 하고, 판정 로직 자체(서버 evaluate_temp_rise_rate)는 절대 안 바꾼다.
  버튼을 누를 때마다 상태만 토글해서 매 패킷에 실어 보낸다 — 별도 명령 왕복 없음.

  견고화 내역: 다른 노드와 동일 패턴 (millis() 논블로킹, WiFi 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>
#include <time.h>
#include "esp_task_wdt.h"  // 워치독 타이머 — Fail-Soft(§3-1) 적용
// 아두이노 ESP32 코어 3.x(ESP-IDF 5 기반) 기준 — esp_task_wdt_init()이 구조체 인자를 받는다.
// 코어 3.x는 프레임워크가 이미 TWDT를 초기화해둔 상태일 수 있어(ESP_ERR_INVALID_STATE)
// setup()에서 재설정(reconfigure)으로 방어한다. 코어 2.x를 쓴다면 옛 시그니처
// esp_task_wdt_init(seconds, panic)로 되돌려야 함 — 사용 중인 코어 버전 확인.

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "SJHOUSE";
const char* WIFI_PASS = "benjamin";
const char* SERVER_IP = "121.133.229.156";
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "fire_zone_differential_01";
const char* ZONE_NAME = "차동식구역";
const char* ZONE_TYPE = "differential";  // db/schema.sql의 fire_alarm_log.zone_type과 일치

const float SUPPLY_VOLTAGE = 5.0;   // 자탐 루프 인가전압 (실측값으로 교체)
const float SHUNT_OHM = 100.0;        // 기준 션트 저항 (실측/사양값으로 교체)
const int SAMPLE_COUNT = 10;         // 저항 단일값 노이즈 감소용 평균 횟수

// TS0202 온도센서 전압 -> 섭씨 변환 (실측 후 정확한 계수로 교체 — 데이터시트 특성곡선 확인 필요)
const float TEMP_V_MIN = 0.5;    // 센서 0도 출력전압
const float TEMP_V_MAX = 4.5;    // 센서 최대범위 출력전압
const float TEMP_C_MAX = 100.0;  // 센서 최대범위 온도(°C)

const unsigned long SEND_INTERVAL_MS = 5000;   // 화재 대응 속도 우선(파일 상단 트레이드오프 참고)
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

// ---- 점검모드(Test Mode) 택트스위치 — 인수인계 문서 §7-2, 자탐1/자탐2/소화기 공통 패턴 ----
const int TEST_MODE_PIN = 27;  // 실제 배선한 GPIO 핀 번호로 교체 — 하드웨어팀과 핀 번호 확인 필요

WiFiUDP udp;
Adafruit_ADS1115 ads;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;
unsigned long lastWifiCheckMs = 0;
unsigned long bootMs = 0;  // 부팅 시각(millis() 기준) — 실험 t=0, 수동 스톱워치와 맞추는 용도
float lastTempC = NAN;     // 직전 측정 온도 — dT/dt 계산용
double lastTempTs = -1;    // 직전 측정 시각(epoch 초)

bool testModeActive = false;    // 점검모드 상태 — 버튼 누를 때마다 토글, 매 패킷에 실어 보냄
bool lastButtonState = HIGH;    // INPUT_PULLUP이라 평소 HIGH, 누르면 LOW

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

// 부팅 후 경과시간을 "HH:MM:SS"로 포맷 — 가스계 노드와 동일한 표기 방식.
// 수동으로 온도계를 재는 실험자는 ESP32 전원을 켜는 순간(=이 값이 00:00:00 근처인 시점)
// 스톱워치도 같이 시작하면, 이후 시리얼 로그의 boot= 값과 실측 온도를 바로 대조할 수 있다.
void formatElapsed(unsigned long ms, char *outBuf, size_t outLen) {
  unsigned long totalSec = ms / 1000;
  unsigned int hh = totalSec / 3600;
  unsigned int mm = (totalSec % 3600) / 60;
  unsigned int ss = totalSec % 60;
  snprintf(outBuf, outLen, "%02u:%02u:%02u", hh, mm, ss);
}

// 점검모드 버튼 폴링 — loop() 매 반복 초입에서 호출(인수인계 문서 §7-2). 디바운스 포함.
// 판정 로직(evaluate_temp_rise_rate, 서버 쪽)은 절대 건드리지 않는다 — 여기서 바뀌는 건
// testModeActive 플래그 하나뿐이고, 이 값은 그대로 패킷에 실려 서버로 전달만 된다.
void pollTestModeButton() {
  bool buttonState = digitalRead(TEST_MODE_PIN);
  if (buttonState == LOW && lastButtonState == HIGH) {
    delay(30);  // 디바운스
    if (digitalRead(TEST_MODE_PIN) == LOW) {
      testModeActive = !testModeActive;  // 누를 때마다 토글
      Serial.printf("점검모드 %s\n", testModeActive ? "시작" : "종료");
    }
  }
  lastButtonState = buttonState;
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
// ⚠️ 캘리브레이션 실험용 임시 수정(raw/전압 병기): TEMP_V_MIN/V_MAX/C_MAX 선형식은
// 아직 검증 안 된 임시값이다. 조리용 온도계로 raw↔실제℃ 대조 실험을 하는 동안에는
// 이 (틀릴 수 있는) 선형식으로 이미 변환된 값만 남기면 안 되고, raw ADC 값과 전압을
// 반드시 같이 남겨야 나중에 "이 식이 맞았는지" 검증하고 새 식으로 교체할 수 있다.
//
// ⚠️⚠️ 2026-08-23 논의 결과 — 이 함수와 TEMP_* 상수는 "temp_c 표시값" 계산 전용이고,
// 화재 경보 판정과는 완전히 무관하다(judge/differential.py가 temp_raw_adc를 직접 읽어서
// raw 기준으로만 판정하며, 이 함수가 리턴하는 temp_c는 판정 코드에서 아예 참조되지 않는다).
// 그래서 다음 두 가지를 반드시 지킬 것:
//   1) 작동시험으로 확정된 raw 기반 회귀계수(TEMP_RAW_PER_C=-139.0 등, 인수인계 문서 §4)는
//      이 파일에 하드코딩하지 않는다 — 그 계수는 서버 evaluate_temp_rise_rate() 쪽에서만
//      쓰는 값이다. 여기 있는 TEMP_V_MIN/V_MAX/C_MAX(전압 기반 선형식)에 그 값을 그대로
//      대입할 수도 없다 — 애초에 파라미터 형식(전압 기반 vs raw 기반)이 다르다.
//      temp_c 표시 정확도를 개선하고 싶으면 이 함수 본문을 raw 기반 새 공식으로 통째로
//      바꾸는 건 괜찮지만(표시값만 바뀜), 그 계수를 "판정 임계값"으로 오해해 쓰면 안 된다.
//   2) 이 파일(또는 다른 어떤 노드 펌웨어)에도 화재 경보를 스스로 판단해서 트리거하는
//      로직을 추가하지 않는다 — 판정은 오직 서버 judge/differential.py 하나에서만 한다.
//      노드는 raw 값을 그대로 실어 보내는 역할만 한다.
// ⚠️ 부작동시험 2회차 로그(2026-08-23)에서 실측온도와 무관하게 단일 샘플이 주변 대비
// 150~420raw나 튀는 이상치가 15분간 8건 발견됨(예: 정상 8470대에서 갑자기 8019, 7881 등).
// 무열원 무반응시험(Phase1) 때의 노이즈 폭(±5~32raw/5초)보다 훨씬 크고, 실제 온도 변화로는
// 설명 안 되는 크기라 I2C/전기적 노이즈(추정: WiFi 재연결 시도와 겹치는 타이밍)로 보인다.
// 루프저항처럼 평균을 쓰면 이상치 하나가 SAMPLE_COUNT분의 1로만 희석돼 여전히 오차가 남으므로,
// 여러 샘플을 정렬해 "중앙값"을 쓰는 방식으로 이런 단일 튐을 확실하게 걸러낸다.
float measureTempC(int16_t &outRawAdc, float &outVolts) {
  int16_t samples[SAMPLE_COUNT];
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samples[i] = ads.readADC_SingleEnded(0);  // A0
    delay(10);  // ADS1115 변환 안정화 대기
  }
  // 버블정렬(SAMPLE_COUNT=10이라 성능 문제 없음) 후 중앙값 계산
  for (int i = 0; i < SAMPLE_COUNT - 1; i++) {
    for (int j = i + 1; j < SAMPLE_COUNT; j++) {
      if (samples[j] < samples[i]) {
        int16_t tmp = samples[i];
        samples[i] = samples[j];
        samples[j] = tmp;
      }
    }
  }
  int16_t raw = (samples[SAMPLE_COUNT / 2 - 1] + samples[SAMPLE_COUNT / 2]) / 2;  // 짝수개 중앙값
  float v = ads.computeVolts(raw);
  outRawAdc = raw;
  outVolts = v;
  float ratio = (TEMP_V_MIN - v) / (TEMP_V_MAX - TEMP_V_MIN);
  return ratio * TEMP_C_MAX;
}

void sendPacket(float loopResistanceOhm, float tempC, float tempRiseRate, int16_t tempRawAdc, float tempVolts, unsigned long elapsedMs) {
  // 캘리브레이션 필드(temp_raw_adc, temp_v, boot_elapsed_ms) + test_mode 필드 추가분만큼
  // 버퍼를 256->352로 키웠다(기존 320에서 "test_mode":false, 약 18바이트 여유 추가 확보).
  StaticJsonDocument<352> doc;
  doc["node_id"] = NODE_ID;
  doc["device_type"] = "fire_alarm";
  doc["zone"] = ZONE_NAME;
  doc["zone_type"] = ZONE_TYPE;
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["test_mode"] = testModeActive;  // 점검모드 상태 — 서버 test_mode.py:handle_judgment()가 분기
  doc["loop_resistance_ohm"] = loopResistanceOhm;
  doc["temp_c"] = tempC;
  if (!isnan(tempRiseRate)) doc["temp_rise_rate"] = tempRiseRate;
  // ⚠️ 캘리브레이션 실험용 임시 필드 — 서버 receiver/packet_parser.py와 schema.sql이
  // 이 필드를 받아 저장하도록 같이 고치지 않으면 DB에는 안 쌓이고 그냥 무시된다.
  // 그 전까지는 아래 loop()의 Serial.printf 로그를 시리얼 모니터에서 파일로
  // 저장(또는 터미널 로거)해서 캘리브레이션 데이터로 직접 쓰면 된다.
  doc["temp_raw_adc"] = tempRawAdc;
  doc["temp_v"] = tempVolts;
  doc["boot_elapsed_ms"] = elapsedMs;  // 수기 실측 온도와 대조할 때 쓰는 부팅 후 경과시간(ms)

  // ArduinoJson v6는 용량(StaticJsonDocument<352>)이 부족해도 에러 없이 필드를 조용히
  // 누락시킨다 — 나중에 "왜 DB에 이 필드가 안 들어왔지" 하는 디버깅을 피하려면 여기서
  // 바로 확인하는 게 낫다. 필드가 늘어날 때마다(예: 나중에 자탐2/소화기에 test_mode
  // 확장 시) 이 로그가 찍히는지 다시 확인할 것.
  if (doc.overflowed()) {
    Serial.println("⚠️ JSON 버퍼 부족! StaticJsonDocument 크기를 늘려야 함");
  }

  char buf[352];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

void setup() {
  Serial.begin(115200);

  esp_task_wdt_config_t twdtConfig = {
    .timeout_ms = 30000,   // 30초 동안 loop()에서 응답 없으면 자동 재부팅
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  if (esp_task_wdt_init(&twdtConfig) == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&twdtConfig);  // 프레임워크가 이미 초기화해둔 경우 재설정
  }
  esp_task_wdt_add(NULL);  // 현재 태스크(메인 loop)를 감시 대상으로 등록(이미 등록돼 있어도 무해)

  bootMs = millis();  // 실험 t=0 — 이 순간(전원 인가 직후)에 수동 스톱워치도 같이 시작할 것
  bootId = esp_random();

  Wire.begin();
  if (!ads.begin()) {
    Serial.println("ADS1115 초기화 실패 — 배선 확인 필요");
  }

  pinMode(TEST_MODE_PIN, INPUT_PULLUP);  // 점검모드 택트스위치 — 평소 HIGH, 누르면 LOW

  connectWiFi();
}

void loop() {
  esp_task_wdt_reset();  // "나 아직 살아있음" 신호 — 매 반복 맨 먼저 호출

  pollTestModeButton();  // 점검모드 버튼 폴링 — 전송 주기와 무관하게 매 반복 확인(디바운스 포함)
  ensureWiFiConnected();

  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float resistance = measureLoopResistanceOhm();
  if (resistance < 0) {
    Serial.println("루프 개방(단선) 의심 — 이번 측정 건너뜀");
    return;
  }

  int16_t tempRawAdc;
  float tempVolts;
  float tempC = measureTempC(tempRawAdc, tempVolts);
  double nowTs = getEpochSeconds();
  float riseRate = NAN;
  if (!isnan(lastTempC) && lastTempTs > 0) {
    double dt = nowTs - lastTempTs;
    if (dt > 0) riseRate = (tempC - lastTempC) / dt;  // °C/초 (임시식 기준 — 캘리브레이션 전)
  }
  lastTempC = tempC;
  lastTempTs = nowTs;

  unsigned long elapsedMs = millis() - bootMs;
  char elapsedBuf[16];
  formatElapsed(elapsedMs, elapsedBuf, sizeof(elapsedBuf));

  // 캘리브레이션 실험용 로그 — boot=HH:MM:SS를 수동 스톱워치/온도계 실측 시각과 맞춰서
  // raw(또는 v) <-> 실제 온도계 값 대조표를 만드는 데 쓴다. ts(epoch 초)는 NTP 미동기 시
  // millis() 기반이라 boot=와 사실상 같은 값이 되므로, 사람이 보기엔 boot=가 기준이다.
  // 시리얼 모니터를 "파일로 저장"하거나 터미널 로거(예: PuTTY, screen -L, pyserial)로
  // 캡처해두면 실험 끝나고 바로 raw~℃ 캘리브레이션 데이터로 쓸 수 있다.
  Serial.printf("boot=%s | ts=%.0f | raw=%d | v=%.4f | temp_c(임시식)=%.1f | 루프저항=%.1f ohm | 점검모드=%s\n",
                elapsedBuf, nowTs, tempRawAdc, tempVolts, tempC, resistance, testModeActive ? "ON" : "OFF");
  sendPacket(resistance, tempC, riseRate, tempRawAdc, tempVolts, elapsedMs);
}