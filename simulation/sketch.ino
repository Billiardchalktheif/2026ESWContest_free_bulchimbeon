#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- 설정값 ----
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

// ---- 시뮬레이션 전용 핀 (실물은 CD74HC4067 멀티플렉서 경유, 4개 통합) ----
const int VOLTAGE_SIM_PIN = 4;   // 포텐셔미터1 (배터리 전압 대신)
const int LUX_SIM_PIN = 3;       // 포텐셔미터2 (CDS 조도센서 대신)
const int DEMO_BUTTON_PIN = 0;   // 데모모드 트리거 버튼

const char* NODE_ID = "evac_light_01";
const char* ZONE_NAME = "1층 복도";

const float ADC_REF_V = 3.3;
const int ADC_MAX = 4095;
const float VOLTAGE_DIVIDER_RATIO = 1.5;  // 실물과 동일 상수 유지(실측 후 재조정 대상)

const unsigned long NORMAL_SEND_INTERVAL_MS = 3000;   // 시뮬레이션 확인용으로 대폭 단축(실물은 1시간)

// 방전시험 — 데모 모드만 시뮬레이션 (실제 20분 시험은 시간상 생략)
const unsigned long DEMO_TEST_DURATION_MS = 45000;                 // 데모: 45초
const unsigned long DEMO_SAMPLE_INTERVAL_MS = 3000;                // 3초 간격으로 샘플

WiFiUDP udp;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastSendMs = 0;

enum TestState { TEST_IDLE, TEST_RUNNING };
TestState testState = TEST_IDLE;
unsigned long testStartMs = 0;
unsigned long testLastSampleMs = 0;

volatile bool demoButtonPressed = false;

void IRAM_ATTR onDemoButtonPress() {
  demoButtonPressed = true;  // ISR에서는 플래그만 세팅
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
  Serial.println(WiFi.status() == WL_CONNECTED ? " 연결됨" : " 실패(시뮬레이션 정상)");
}

double getEpochSeconds() {
  return millis() / 1000.0;
}

float measureBatteryVoltage() {
  int raw = analogRead(VOLTAGE_SIM_PIN);
  float v = (raw / (float)ADC_MAX) * ADC_REF_V;
  return v * VOLTAGE_DIVIDER_RATIO;
}

int measureApproxLux() {
  int raw = analogRead(LUX_SIM_PIN);
  return map(raw, 0, ADC_MAX, 0, 500);
}

void sendPacket(float batteryVoltage, int lux, bool demoMode) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_ID;
  doc["device_type"] = "evac_light";
  doc["zone"] = ZONE_NAME;
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["battery_voltage"] = batteryVoltage;
  doc["lux"] = lux;
  doc["demo_mode"] = demoMode ? 1 : 0;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  Serial.print(">> 유도등 패킷: ");
  Serial.println(buf);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// ---- 방전시험(데모모드) ----
void startDischargeTest() {
  if (testState == TEST_RUNNING) {
    Serial.println("이미 방전시험 진행 중 — 요청 무시");
    return;
  }
  testStartMs = millis();
  testLastSampleMs = 0;
  testState = TEST_RUNNING;
  Serial.println("방전시험(데모) 시작 — 45초간 진행, 포텐셔미터1을 서서히 내려서 방전을 흉내내보세요");
}

void updateDischargeTest() {
  if (testState != TEST_RUNNING) return;

  unsigned long elapsed = millis() - testStartMs;

  if (elapsed - testLastSampleMs >= DEMO_SAMPLE_INTERVAL_MS || testLastSampleMs == 0) {
    testLastSampleMs = elapsed;
    float v = measureBatteryVoltage();
    int lux = measureApproxLux();
    sendPacket(v, lux, true);
    Serial.printf("방전시험 샘플 (경과 %lu ms / 목표 %lu ms)\n", elapsed, DEMO_TEST_DURATION_MS);
  }

  if (elapsed >= DEMO_TEST_DURATION_MS) {
    testState = TEST_IDLE;
    Serial.println("방전시험 종료 -> 평상시 모니터링으로 복귀");
  }
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();

  pinMode(DEMO_BUTTON_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(DEMO_BUTTON_PIN), onDemoButtonPress, FALLING);

  connectWiFi();
  Serial.println("유도등 시뮬레이션 시작");
  Serial.println("- 포텐셔미터1(GPIO4): 배터리 전압");
  Serial.println("- 포텐셔미터2(GPIO3): 조도(CDS)");
  Serial.println("- 버튼(GPIO0): 누르면 45초 방전시험 데모 시작");
}

void loop() {
  if (demoButtonPressed) {
    demoButtonPressed = false;
    startDischargeTest();
  }

  if (testState == TEST_RUNNING) {
    updateDischargeTest();
    return;  // 시험 중엔 평상시 전송 건너뜀
  }

  if (millis() - lastSendMs < NORMAL_SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float v = measureBatteryVoltage();
  int lux = measureApproxLux();
  sendPacket(v, lux, false);
}