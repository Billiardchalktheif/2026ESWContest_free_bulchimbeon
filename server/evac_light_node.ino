/*
  불침번 — 유도등 노드 (ESP32 DevKitC ×1 + CD74HC4067 멀티플렉서 ×1, 유도등 4개 통합감시)
  역할: 유도등 4개의 배터리 전압(전압분압) + 점등 확인(CDS 조도센서)을 멀티플렉서로
        순차 스캔해 라즈베리파이로 전송한다. 서버(server/regression_forecast.py)가
        전압 추세를 선형회귀해 "방전 임계전압 도달까지 남은 시간"을 예측하고,
        법정 최소 작동시간(20분/60분) 대비 부족한지 판정한다.

  확정 핀 배치(§5):
    - CD74HC4067 SIG -> GPIO32 (ADC1)
    - CD74HC4067 S0~S3 -> GPIO25, 26, 27, 14 (채널 선택용 디지털 출력 — ADC2 제약과
      무관한 디지털 출력이므로 GPIO25~27을 여기 써도 문제없다. §5 하단 주의사항 참고)
    - CDS 조도센서 ×4 -> MUX 채널 C0, C2, C4, C6
    - 전압분압(배터리전압) ×4 -> MUX 채널 C1, C3, C5, C7
    - 데모모드 버튼 -> GPIO13 (인터럽트, 내부 풀업)

  두 가지 동작 모드:
    1) 평상시 상시 모니터링: 느린 주기(예: 1시간)로 4개 유도등을 순차 스캔해
       전압/조도를 상시 기록 -> 서버의 장기 회귀 예측용 데이터
    2) 방전시험 모드: 실제 소방점검 절차상 유도등은 정전 상황을 가정해 배터리
       단독으로 정격시간(예: 20분) 이상 점등 유지되는지 시험해야 한다.
       ⚠️ 이 보드에는 주전원 차단 릴레이가 배선되어 있지 않다(§5 확정 핀 배치에
       없음) — 그래서 실제 시험에서는 담당자가 수동으로 주전원을 차단하고,
       ESP32는 그 시험 구간 동안 촘촘한 샘플링/판정만 담당한다.
       데모 버튼을 누르면 "같은 판정 로직"을 시간축만 압축(45초)해서 돌린다
       -> 결선 현장 시연에서 20분을 기다릴 필요 없이 즉시 결론 확인 가능.

  CDS 조도값 관련 참고: CDS는 저항이 조도에 비선형으로 반응하는 소자라, 여기서
  계산하는 lux는 실제 조도계 대비 근사치다. 정확한 lux가 필요하면 조도계로
  캘리브레이션 곡선을 따로 구해서 교체할 것 — 우리 시스템은 "켜져있는지 확인"이
  목적이라 근사치로 충분하다.

  견고화 내역: 다른 노드와 동일 패턴
    (millis() 논블로킹 상태머신, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)

  ⚠️ 2선식/3선식 전제: 이 로직은 "2선식(평상시 상시 점등)" 유도등을 전제로 한다.
  3선식은 평상시 소등이 정상이라 조도값으로 "꺼져있음=이상"을 판단하면 오탐이다.
  실제 배포 전에 현장이 2선식인지 반드시 확인할 것.
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

const int MUX_SIG_PIN = 32;   // ADC1
const int MUX_S0_PIN = 25;
const int MUX_S1_PIN = 26;
const int MUX_S2_PIN = 27;
const int MUX_S3_PIN = 14;
const int DEMO_BUTTON_PIN = 13;

const int NUM_LIGHTS = 4;
const int LUX_MUX_CHANNELS[NUM_LIGHTS] = {0, 2, 4, 6};       // C0, C2, C4, C6
const int VOLTAGE_MUX_CHANNELS[NUM_LIGHTS] = {1, 3, 5, 7};   // C1, C3, C5, C7
const char* NODE_IDS[NUM_LIGHTS] = {"evac_light_01", "evac_light_02", "evac_light_03", "evac_light_04"};
const char* ZONE_NAMES[NUM_LIGHTS] = {"1층 복도", "2층 복도", "3층 복도", "4층 복도"};

const float ADC_REF_V = 3.3;
const int ADC_MAX = 4095;
// 전압분압 비율 — (R1+R2)/R2. 배터리 실제전압을 ADC 입력범위(0~3.3V)로 낮추는 비율.
const float VOLTAGE_DIVIDER_RATIO = 1.5;   // 실측 후 정확한 비율로 교체

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
// CD74HC4067 채널 선택 및 읽기
// ---------------------------------------------------------------------------
void selectMuxChannel(int channel) {
  digitalWrite(MUX_S0_PIN, channel & 0x01);
  digitalWrite(MUX_S1_PIN, (channel >> 1) & 0x01);
  digitalWrite(MUX_S2_PIN, (channel >> 2) & 0x01);
  digitalWrite(MUX_S3_PIN, (channel >> 3) & 0x01);
  delayMicroseconds(10);  // 채널 전환 안정화 대기 (짧아서 WiFi 스택에 영향 없음)
}

int readMuxChannelRaw(int channel) {
  selectMuxChannel(channel);
  return analogRead(MUX_SIG_PIN);
}

float measureBatteryVoltage(int lightIndex) {
  int raw = readMuxChannelRaw(VOLTAGE_MUX_CHANNELS[lightIndex]);
  float v = (raw / (float)ADC_MAX) * ADC_REF_V;
  return v * VOLTAGE_DIVIDER_RATIO;
}

// CDS 원시값을 근사 lux로 변환 (정밀 보정 전 근사치 — 파일 상단 주석 참고)
int measureApproxLux(int lightIndex) {
  int raw = readMuxChannelRaw(LUX_MUX_CHANNELS[lightIndex]);
  return map(raw, 0, ADC_MAX, 0, 500);  // 0~500 lux 범위로 선형 근사, 실측 후 보정
}

void sendPacket(int lightIndex, float batteryVoltage, int lux, bool demoMode) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_IDS[lightIndex];
  doc["device_type"] = "evac_light";
  doc["zone"] = ZONE_NAMES[lightIndex];
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["battery_voltage"] = batteryVoltage;
  doc["lux"] = lux;
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
      int lux = measureApproxLux(i);
      sendPacket(i, v, lux, testIsDemo);
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

  pinMode(MUX_S0_PIN, OUTPUT);
  pinMode(MUX_S1_PIN, OUTPUT);
  pinMode(MUX_S2_PIN, OUTPUT);
  pinMode(MUX_S3_PIN, OUTPUT);

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
    int lux = measureApproxLux(i);
    sendPacket(i, v, lux, false);
  }
}
