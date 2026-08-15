/*
  불침번 — 유도등 노드 v3 (ESP32 DevKitC ×1, 유도등 4개 배터리 전압 직결감시)

  역할: 유도등 4개의 배터리 전압을 ESP32 ADC 핀에 각각 직결해서 읽고 라즈베리파이로
  전송한다. 서버(judge/regression.py)가 전압 추세를 회귀분석해 방전 임계전압
  도달까지 남은 시간을 예측하고, 법정 최소 작동시간(20분) 대비 부족한지 판정한다.

  v3 변경점 (물리 버튼 제거):
    - GPIO13 물리 버튼 + 인터럽트 방식 제거. 실제 배선에서 "계속 눌린 것으로 인식"되는
      신뢰성 문제가 있었고, 방전시험 실측(1h/2h/4h/6h/8h, 4대 개체)도 전부 시리얼
      커맨드로 검증됐던 방식이라 이걸 정식 트리거로 채택. 하드웨어 배선/부품이 하나
      줄어 더 단순해짐. 나중에 신뢰성 있는 버튼을 확보하면 다시 추가 가능(주석 참고).

  확정 사양 (실측 완료):
    - 배터리: AA 단일 Ni-Cd 셀(WB-NC0601, 완충 약 1.2~1.3V), 분압 없이 직결 +
      보호저항 1kΩ만 직렬 (VOLTAGE_DIVIDER_RATIO = 1.0)
    - 유도등 4개 배터리(+) -> GPIO32/33/34/35 (전부 ADC1, WiFi 사용 중에도 안정)
    - 방전시험 판정 임계전압: EVAC_DISCHARGE_THRESHOLD_V = 0.30V (서버측 값, 4대
      개체·5개 충전시간 실측으로 확정. 이 노드는 판정을 하지 않고 전압만 전송하므로
      이 값은 참고용이며, 실제 로직은 server/judge/regression.py에 있음. 자세한
      내용은 docs/유도등_실물실험_기록.md, docs/유도등_방전시험_통합_분석_리포트.md 참고)

  두 가지 동작 모드:
    1) 평상시 상시 모니터링: 1시간 간격으로 4개 유도등을 순차 스캔해 전압 전송
    2) 방전시험 모드: 시리얼 모니터에 's' 입력 시 시작. 주전원 차단 릴레이가 없어
       담당자가 수동으로 플러그를 뽑아야 하며, 그 순간 's'를 입력해 트리거한다.
       실제 정격시험(20분)과 현장 데모(45초 압축) 둘 다 같은 판정 로직을 시간축만
       다르게 써서 재사용한다. 데모는 'd' 입력으로 시작.

  견고화 내역: 다른 노드와 동일 패턴
    (millis() 논블로킹 상태머신, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- 설정값 (실제 배포 시 아래 3개만 채우면 됨) ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

const int NUM_LIGHTS = 4;
const int VOLTAGE_PINS[NUM_LIGHTS] = {32, 33, 34, 35};
const char* NODE_IDS[NUM_LIGHTS] = {"evac_light_01", "evac_light_02", "evac_light_03", "evac_light_04"};
const char* ZONE_NAMES[NUM_LIGHTS] = {"1층 복도", "2층 복도", "3층 복도", "4층 복도"};

const float ADC_REF_V = 3.3;
const int ADC_MAX = 4095;
const float VOLTAGE_DIVIDER_RATIO = 1.0;  // 1.2V 단일셀, 분압 없이 직결 (실측 확정)

const unsigned long NORMAL_SEND_INTERVAL_MS = 3600000;   // 평상시 1시간 간격
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

// 방전시험 — 실제/데모 공통 로직, 시간축(duration/interval)만 다르게 사용
const unsigned long REAL_TEST_DURATION_MS = 20UL * 60UL * 1000UL;  // 실제 정격시험: 20분
const unsigned long REAL_SAMPLE_INTERVAL_MS = 30000;               // 30초 간격
const unsigned long DEMO_TEST_DURATION_MS = 45000;                 // 데모: 45초로 압축
const unsigned long DEMO_SAMPLE_INTERVAL_MS =
    REAL_SAMPLE_INTERVAL_MS * DEMO_TEST_DURATION_MS / REAL_TEST_DURATION_MS;

WiFiUDP udp;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;
unsigned long lastWifiCheckMs = 0;

enum TestState { TEST_IDLE, TEST_RUNNING };
TestState testState = TEST_IDLE;
bool testIsDemo = false;
unsigned long testStartMs = 0;
unsigned long testLastSampleMs = 0;
unsigned long testDurationMs = 0;
unsigned long testSampleIntervalMs = 0;

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

float measureBatteryVoltage(int lightIndex) {
  int raw = analogRead(VOLTAGE_PINS[lightIndex]);
  float v = (raw / (float)ADC_MAX) * ADC_REF_V;
  return v * VOLTAGE_DIVIDER_RATIO;
}

void sendPacket(int lightIndex, float batteryVoltage, bool demoMode) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_IDS[lightIndex];
  doc["device_type"] = "evac_light";
  doc["zone"] = ZONE_NAMES[lightIndex];
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["battery_voltage"] = batteryVoltage;
  doc["demo_mode"] = demoMode ? 1 : 0;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// ---------------------------------------------------------------------------
// 방전시험 — 시리얼 커맨드로 트리거 (물리버튼 대체)
//   's' : 실제 정격시험(20분) 시작 — 주전원을 지금 뽑았을 때 입력
//   'd' : 데모(45초) 시작 — 현장 시연용
// ---------------------------------------------------------------------------
void checkSerialCommand() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 's' || c == 'S') startDischargeTest(false);
  if (c == 'd' || c == 'D') startDischargeTest(true);
}

void startDischargeTest(bool demoMode) {
  if (testState == TEST_RUNNING) {
    Serial.println("이미 방전시험 진행 중 — 요청 무시");
    return;
  }
  testIsDemo = demoMode;
  testDurationMs = demoMode ? DEMO_TEST_DURATION_MS : REAL_TEST_DURATION_MS;
  testSampleIntervalMs = demoMode ? DEMO_SAMPLE_INTERVAL_MS : REAL_SAMPLE_INTERVAL_MS;
  testStartMs = millis();
  testLastSampleMs = 0;
  testState = TEST_RUNNING;

  Serial.printf("방전시험 시작 (%s, %.0f초) — 주전원은 담당자가 수동으로 차단할 것\n",
                demoMode ? "데모" : "실제", testDurationMs / 1000.0);
}

void updateDischargeTest() {
  if (testState != TEST_RUNNING) return;

  unsigned long elapsed = millis() - testStartMs;

  if (elapsed - testLastSampleMs >= testSampleIntervalMs || testLastSampleMs == 0) {
    testLastSampleMs = elapsed;
    for (int i = 0; i < NUM_LIGHTS; i++) {
      float v = measureBatteryVoltage(i);
      sendPacket(i, v, testIsDemo);
    }
    Serial.printf("방전시험 샘플 전송 (경과 %lu ms / 목표 %lu ms)\n", elapsed, testDurationMs);
  }

  if (elapsed >= testDurationMs) {
    testState = TEST_IDLE;
    Serial.println("방전시험 종료 -> 평상시 모니터링으로 복귀 (주전원 복구는 담당자 확인 필요)");
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  bootId = esp_random();
  connectWiFi();
  Serial.println("준비됨 — 's'=실제 방전시험(20분), 'd'=데모(45초)");
}

void loop() {
  ensureWiFiConnected();
  checkSerialCommand();

  if (testState == TEST_RUNNING) {
    updateDischargeTest();
    return;  // 시험 중에는 평상시 주기 전송을 건너뛴다
  }

  if (millis() - lastSendMs < NORMAL_SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  for (int i = 0; i < NUM_LIGHTS; i++) {
    float v = measureBatteryVoltage(i);
    sendPacket(i, v, false);
  }
}
