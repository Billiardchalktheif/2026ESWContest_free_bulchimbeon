/*
  불침번 — 유도등 노드 v4 (ESP32 DevKitC ×1, 유도등 4개 배터리 전압 직결감시)

  역할: 유도등 4개의 배터리 전압을 ESP32 ADC 핀에 각각 직결해서 읽고 라즈베리파이로
  전송한다. 서버(judge/regression.py)가 전압 추세를 회귀분석해 방전 임계전압
  도달까지 남은 시간을 예측하고, 법정 최소 작동시간(20분) 대비 부족한지 판정한다.

  v3 변경점 (물리 버튼 제거) — 참고용으로 남겨둠:
    - GPIO13 물리 버튼 + 인터럽트 방식 제거. 실제 배선에서 "계속 눌린 것으로 인식"되는
      신뢰성 문제가 있었고, 방전시험 실측(1h/2h/4h/6h/8h, 4대 개체)도 전부 시리얼
      커맨드로 검증됐던 방식이라 이걸 정식 트리거로 채택.

  v4 변경점 (물리 버튼 재도입 — 대시보드/노트북 없이 보조배터리 단독 운용 대응):
    - 파이 WiFi가 불안정해 대시보드 원격 트리거를 포기하고, 시리얼 명령을 그대로
      로컬 GPIO 버튼으로 옮겼다. v2 때 실패했던 attachInterrupt 방식은 다시 쓰지
      않고, loop() 안에서 폴링 + millis() 기반 디바운스로 구현해 오인식 문제를 피함.
    - 택트스위치(4핀, 대각선 페어 = 내부 접점) 2개:
        GPIO25 = 's' 대체 (실제 정격시험 20분)
        GPIO26 = 'd' 대체 (데모 45초)
      둘 다 INPUT_PULLUP, 버튼 다리 하나 -> GPIO, 대각선 반대 다리 -> GND.
    - 시리얼 명령('s'/'d')은 실험실 디버깅용으로 그대로 남겨둠 — 버튼과 시리얼
      둘 다 동일하게 startDischargeTest()를 호출하므로 서로 충돌 없음.

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
    2) 방전시험 모드: GPIO25/26 버튼(또는 시리얼 's'/'d') 입력 시 시작. 주전원
       차단 릴레이가 없어 담당자가 수동으로 플러그를 뽑아야 하며, 그 순간 버튼을
       눌러 트리거한다. 실제 정격시험(20분)과 현장 데모(45초 압축) 둘 다 같은
       판정 로직을 시간축만 다르게 써서 재사용한다.

  견고화 내역: 다른 노드와 동일 패턴
    (millis() 논블로킹 상태머신, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>
#include "esp_task_wdt.h"  // 워치독 타이머 — Fail-Soft(§3-1) 적용
// 아두이노 ESP32 코어 3.x(ESP-IDF 5 기반) 기준 — esp_task_wdt_init()이 구조체 인자를 받는다.
// 코어 3.x는 프레임워크가 이미 TWDT를 초기화해둔 상태일 수 있어(ESP_ERR_INVALID_STATE)
// setup()에서 재설정(reconfigure)으로 방어한다. 코어 2.x를 쓴다면 옛 시그니처
// esp_task_wdt_init(seconds, panic)로 되돌려야 함 — 사용 중인 코어 버전 확인.

// ---- 설정값 (실제 배포 시 아래 3개만 채우면 됨) ----
const char* WIFI_SSID = "SJHOUSE";
const char* WIFI_PASS = "benjamin";
const char* SERVER_IP = "121.133.229.156";
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

// ---- 방전시험 트리거 버튼 (v4 신규) ----
// 택트스위치(4핀, 대각선 페어): 다리 하나 -> GPIO, 대각선 반대 다리 -> GND.
// INPUT_PULLUP이므로 평소 HIGH, 누르면 LOW.
const int BUTTON_S_PIN = 25;   // 's' 대체 — 실제 정격시험(20분)
const int BUTTON_D_PIN = 26;   // 'd' 대체 — 데모(45초)
const unsigned long BUTTON_DEBOUNCE_MS = 50;   // 이 시간 이상 안정적으로 유지돼야 진짜 입력으로 인정

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

// 버튼별 디바운스 상태 (인터럽트 대신 폴링 — v2 오인식 문제 회피)
bool buttonSLastReading = HIGH;
bool buttonSStableState = HIGH;
unsigned long buttonSLastChangeMs = 0;

bool buttonDLastReading = HIGH;
bool buttonDStableState = HIGH;
unsigned long buttonDLastChangeMs = 0;

void startDischargeTest(bool demoMode);  // 전방 선언

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
// 방전시험 트리거 — 시리얼 명령(디버깅용, 그대로 유지) + 택트스위치(v4 신규, 메인)
//   's' / GPIO25 : 실제 정격시험(20분) 시작 — 주전원을 지금 뽑았을 때 입력
//   'd' / GPIO26 : 데모(45초) 시작 — 현장 시연용
// ---------------------------------------------------------------------------
void checkSerialCommand() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 's' || c == 'S') startDischargeTest(false);
  if (c == 'd' || c == 'D') startDischargeTest(true);
}

// 버튼 1개에 대한 폴링+디바운스 처리. isDemo=false면 GPIO25(s), true면 GPIO26(d).
// 인터럽트 대신 loop()에서 매번 읽고, 값이 BUTTON_DEBOUNCE_MS 이상 안정적으로
// 유지된 뒤 "눌림 순간(HIGH->LOW로 바뀐 그 한 번)"에만 startDischargeTest()를 호출한다.
void pollButton(int pin, bool &lastReading, bool &stableState,
                 unsigned long &lastChangeMs, bool isDemo) {
  bool reading = digitalRead(pin);

  if (reading != lastReading) {
    lastChangeMs = millis();   // 값이 바뀌는 순간마다 디바운스 타이머 리셋
    lastReading = reading;
  }

  if ((millis() - lastChangeMs) > BUTTON_DEBOUNCE_MS) {
    if (reading != stableState) {
      stableState = reading;
      if (stableState == LOW) {   // INPUT_PULLUP -> 눌리면 LOW. 눌리는 그 순간에만 1회 트리거
        Serial.println(isDemo ? "[버튼] GPIO26(d) 눌림 -> 데모 방전시험" : "[버튼] GPIO25(s) 눌림 -> 실제 방전시험");
        startDischargeTest(isDemo);
      }
    }
  }
}

void checkButtonPress() {
  pollButton(BUTTON_S_PIN, buttonSLastReading, buttonSStableState, buttonSLastChangeMs, false);
  pollButton(BUTTON_D_PIN, buttonDLastReading, buttonDStableState, buttonDLastChangeMs, true);
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

  esp_task_wdt_config_t twdtConfig = {
    .timeout_ms = 30000,   // 30초 동안 loop()에서 응답 없으면 자동 재부팅
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  if (esp_task_wdt_init(&twdtConfig) == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&twdtConfig);  // 프레임워크가 이미 초기화해둔 경우 재설정
  }
  esp_task_wdt_add(NULL);  // 현재 태스크(메인 loop)를 감시 대상으로 등록(이미 등록돼 있어도 무해)

  bootId = esp_random();

  pinMode(BUTTON_S_PIN, INPUT_PULLUP);
  pinMode(BUTTON_D_PIN, INPUT_PULLUP);

  connectWiFi();
  Serial.println("준비됨 — GPIO25(s)=실제 방전시험(20분), GPIO26(d)=데모(45초) / 시리얼 's','d'도 동일 동작");
}

void loop() {
  esp_task_wdt_reset();  // "나 아직 살아있음" 신호 — 매 반복 맨 먼저 호출(방전시험 중에도 계속 리셋되도록 이 위치)

  ensureWiFiConnected();
  checkSerialCommand();
  checkButtonPress();

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
