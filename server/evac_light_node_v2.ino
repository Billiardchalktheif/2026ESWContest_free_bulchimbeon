/*
  불침번 — 유도등 노드 (ESP32 DevKitC ×1, 유도등 4개 배터리 전압 직결감시)
  역할: 유도등 4개의 배터리 전압(저항분압)을 ESP32 ADC 핀에 각각 직결해서 읽고,
        라즈베리파이로 전송한다. 서버(server/regression_forecast.py)가 전압 추세를
        선형회귀해 "방전 임계전압 도달까지 남은 시간"을 예측하고, 법정 최소 작동시간
        (20분/60분) 대비 부족한지 판정한다.

  v2 변경점 (CDS/멀티플렉서 제거):
    - 조도(CDS) 기반 점등확인 로직 삭제. LED 점등 여부는 이진(binary) 신호라
      회귀예측 데이터로 쓸 수 없고, 육안 점검으로도 쉽게 확인되는 항목이라 스코프에서
      제외했다. 배터리 전압 트렌드 감시만이 이 시스템의 예측 가치를 가진다.
    - CD74HC4067 멀티플렉서 제거. CDS를 빼서 채널 수가 4개(전압만)로 줄었고,
      ESP32 ADC1 핀이 4개 이상 남기 때문에 멀티플렉서 없이 유도등 4개의 분압 출력을
      각각 다른 ADC 핀에 직결하는 구조로 단순화했다. 채널 전환 딜레이/배선 복잡도가
      사라져 더 단순하고 안정적이다.

  확정 핀 배치:
    - 유도등 4개 저항분압 출력 -> GPIO32, GPIO33, GPIO34, GPIO35 (전부 ADC1,
      WiFi 사용 중에도 안정적으로 동작. GPIO34/35는 입력 전용이라 분압 출력
      읽기용으로 적합)
    - 데모모드 버튼 -> GPIO13 (인터럽트, 내부 풀업)

  분압 저항비(VOLTAGE_DIVIDER_RATIO)는 배터리 홀더 클립 양단 실측 전압에 따라
  달라진다 (guides/유도등_실험가이드.md §5 실측 표 참고).
    - 실측 3.6~4.2V (셀 3개 직렬 추정) -> R1=R2=10kΩ -> RATIO = 2.0
    - 실측 4.8~5.5V (셀 4개 직렬 추정) -> R1=15kΩ, R2=10kΩ -> RATIO = 2.5
  아래 상수는 실측 전 임시값이니 실측 후 반드시 교체할 것.

  두 가지 동작 모드:
    1) 평상시 상시 모니터링: 느린 주기(예: 1시간)로 4개 유도등을 순차 스캔해
       전압을 상시 기록 -> 서버의 장기 회귀 예측용 데이터
    2) 방전시험 모드: 실제 소방점검 절차상 유도등은 정전 상황을 가정해 배터리
       단독으로 정격시간(예: 20분) 이상 점등 유지되는지 시험해야 한다.
       ⚠️ 이 보드에는 주전원 차단 릴레이가 배선되어 있지 않다 — 그래서 실제
       시험에서는 담당자가 수동으로 주전원을 차단하고, ESP32는 그 시험 구간
       동안 촘촘한 샘플링/판정만 담당한다.
       데모 버튼을 누르면 "같은 판정 로직"을 시간축만 압축(45초)해서 돌린다
       -> 결선 현장 시연에서 20분을 기다릴 필요 없이 즉시 결론 확인 가능.

  견고화 내역: 다른 노드와 동일 패턴
    (millis() 논블로킹 상태머신, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

const int DEMO_BUTTON_PIN = 13;

const int NUM_LIGHTS = 4;
// 유도등 4개 저항분압 출력을 각각 다른 ADC1 핀에 직결 (멀티플렉서 없음)
const int VOLTAGE_PINS[NUM_LIGHTS] = {32, 33, 34, 35};
const char* NODE_IDS[NUM_LIGHTS] = {"evac_light_01", "evac_light_02", "evac_light_03", "evac_light_04"};
const char* ZONE_NAMES[NUM_LIGHTS] = {"1층 복도", "2층 복도", "3층 복도", "4층 복도"};

const float ADC_REF_V = 3.3;
const int ADC_MAX = 4095;
// 전압분압 비율 — (R1+R2)/R2. 배터리 실제전압을 ADC 입력범위(0~3.3V)로 낮추는 비율.
// ⚠️ 실측 전 임시값. guides/유도등_실험가이드.md §5 실측 표 기준으로 확정할 것.
const float VOLTAGE_DIVIDER_RATIO = 2.0;   // R1=R2=10kΩ 가정 (3셀 직렬, 실측 후 교체)

const unsigned long NORMAL_SEND_INTERVAL_MS = 3600000;   // 평상시 1시간 간격
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

// 방전시험 관련 상수 — 데모 모드는 동일 판정 로직을 시간축만 압축해서 재사용한다.
const unsigned long REAL_TEST_DURATION_MS = 20UL * 60UL * 1000UL;  // 실제 정격시험: 20분
const unsigned long REAL_SAMPLE_INTERVAL_MS = 30000;               // 30초 간격 (총 40회)
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

volatile bool demoButtonPressed = false;

void IRAM_ATTR onDemoButtonPress() {
  demoButtonPressed = true;  // ISR에서는 플래그만 세팅 — 무거운 작업은 loop()에서 처리
}

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

// ---------------------------------------------------------------------------
// 배터리 전압 측정 — 멀티플렉서 없이 ADC 핀 직결
// ---------------------------------------------------------------------------
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
// 방전시험 — 실제/데모 공통 로직, 시간축(duration/interval)만 다르게 넘겨받는다.
// 4개 유도등을 동시에 시험 대상으로 삼아 매 샘플마다 4개 패킷을 전송한다.
// ---------------------------------------------------------------------------
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

  pinMode(DEMO_BUTTON_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(DEMO_BUTTON_PIN), onDemoButtonPress, FALLING);

  connectWiFi();
}

void loop() {
  ensureWiFiConnected();

  if (demoButtonPressed) {
    demoButtonPressed = false;
    startDischargeTest(true);
  }

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
