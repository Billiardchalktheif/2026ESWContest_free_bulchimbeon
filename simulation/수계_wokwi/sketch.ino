#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <time.h>

// ---- 설정값 ----
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";
const char* SERVER_IP = "192.168.0.10";
const uint16_t SERVER_PORT = 9000;

// ---- 시뮬레이션 전용 핀 (실물은 INA219 I2C 0x40/0x41 + 압력센서 GPIO32) ----
const int JOCKEY_CURRENT_SIM_PIN = 0;   // 포텐셔미터1 (충압펌프 INA219 대신)
const int MAIN_CURRENT_SIM_PIN = 1;     // 포텐셔미터2 (주펌프 INA219 대신)
const int PRESSURE_SIM_PIN = 4;         // 포텐셔미터3 (압력센서 대신)

const int RELAY_JOCKEY_PIN = 18;   // LED1로 대체 가능
const int RELAY_MAIN_PIN = 19;     // LED2로 대체 가능

const int SAMPLE_COUNT = 50;       // 시뮬레이션이라 실물(100)보다 축소
const unsigned long SAMPLE_INTERVAL_US = 20000;

// 압력값 변환 (시뮬레이션용 임의 스케일 — 실물은 저항분압 후 실측값 기준)
const float PRESSURE_KPA_MAX = 1000.0;

const float JOCKEY_START_THRESHOLD_MA = 500.0;  // 시뮬레이션 스케일(0~4095)에 맞춰 조정한 임계값
const unsigned long JOCKEY_POLL_INTERVAL_MS = 500;
const unsigned long MAIN_SEND_INTERVAL_MS = 3000;

WiFiUDP udp;
uint32_t seqNum = 0;
uint32_t bootId;

unsigned long lastMainSendMs = 0;
unsigned long lastJockeyPollMs = 0;
bool jockeyWasRunning = false;
double jockeyLastStartEpoch = -1;

float samples[SAMPLE_COUNT];

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

// ---- 주펌프: 시뮬레이션 전류(raw값)를 mA처럼 취급해 시계열 샘플링 ----
void sampleMainPumpCurrent() {
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samples[i] = analogRead(MAIN_CURRENT_SIM_PIN);   // 0~4095를 mA처럼 취급(시뮬레이션 한정)
    delayMicroseconds(SAMPLE_INTERVAL_US);
  }
}

float computeRMS() {
  float sumSq = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sumSq += samples[i] * samples[i];
  return sqrt(sumSq / SAMPLE_COUNT);
}

float computePeak() {
  float peak = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    float a = abs(samples[i]);
    if (a > peak) peak = a;
  }
  return peak;
}

float computeMean() {
  float sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) sum += samples[i];
  return sum / SAMPLE_COUNT;
}

float computeDutyCycle(float threshold) {
  float mean = computeMean();
  int active = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    if (abs(samples[i] - mean) > threshold) active++;
  }
  return (float)active / SAMPLE_COUNT;
}

// 압력센서 (시뮬레이션 — 실물처럼 저항분압 계산 없이 raw값을 그대로 kPa로 스케일)
float measurePressureKpa() {
  int raw = analogRead(PRESSURE_SIM_PIN);
  return (raw / 4095.0) * PRESSURE_KPA_MAX;
}

void sendMainPumpPacket(float rms, float peak, float duty, float pressureKpa, const char* valveState = nullptr) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = "pump_main_01";
  doc["device_type"] = "water_pump";
  doc["pump_type"] = "main";
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["rms"] = rms;
  doc["peak"] = peak;
  doc["duty_cycle"] = duty;
  doc["pressure_kpa"] = pressureKpa;
  if (valveState != nullptr) doc["valve_state"] = valveState;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  Serial.print(">> 주펌프 패킷: ");
  Serial.println(buf);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// ---- 충압펌프: 기동 이벤트(엣지) 감지 ----
void sendJockeyPumpPacket(double intervalSec) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = "pump_jockey_01";
  doc["device_type"] = "water_pump";
  doc["pump_type"] = "jockey";
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["cycle_interval_sec"] = intervalSec;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  Serial.print(">> 충압펌프 패킷: ");
  Serial.println(buf);

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

void pollJockeyPump() {
  if (millis() - lastJockeyPollMs < JOCKEY_POLL_INTERVAL_MS) return;
  lastJockeyPollMs = millis();

  float currentSim = analogRead(JOCKEY_CURRENT_SIM_PIN);   // 0~4095를 mA처럼 취급
  bool isRunning = currentSim > JOCKEY_START_THRESHOLD_MA;

  if (isRunning && !jockeyWasRunning) {
    double nowEpoch = getEpochSeconds();
    if (jockeyLastStartEpoch > 0) {
      double intervalSec = nowEpoch - jockeyLastStartEpoch;
      sendJockeyPumpPacket(intervalSec);
    }
    jockeyLastStartEpoch = nowEpoch;
    Serial.println(">> 충압펌프 기동 감지!");
  }
  jockeyWasRunning = isRunning;
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();

  pinMode(RELAY_JOCKEY_PIN, OUTPUT);
  pinMode(RELAY_MAIN_PIN, OUTPUT);
  digitalWrite(RELAY_JOCKEY_PIN, LOW);
  digitalWrite(RELAY_MAIN_PIN, LOW);

  connectWiFi();
  Serial.println("수계 시뮬레이션 시작");
  Serial.println("- 포텐셔미터1(GPIO0): 충압펌프 전류 - 크게 돌리면 '기동 감지' 로그 확인");
  Serial.println("- 포텐셔미터2(GPIO1): 주펌프 전류 - 계속 움직여서 RMS/Peak 값 변화 확인");
  Serial.println("- 포텐셔미터3(GPIO4): 압력센서 - 값 변화로 성능시험 압력 확인");
}

void loop() {
  pollJockeyPump();

  if (millis() - lastMainSendMs < MAIN_SEND_INTERVAL_MS) return;
  lastMainSendMs = millis();

  sampleMainPumpCurrent();

  float minVal = samples[0], maxVal = samples[0];
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    if (samples[i] < minVal) minVal = samples[i];
    if (samples[i] > maxVal) maxVal = samples[i];
  }
  Serial.printf("[디버그] 캡처 내 최소: %.0f, 최대: %.0f, 차이: %.0f\n", minVal, maxVal, maxVal - minVal);
  float rms = computeRMS();
  float peak = computePeak();
  float duty = computeDutyCycle(0.3 * peak);
  float pressure = measurePressureKpa();

  sendMainPumpPacket(rms, peak, duty, pressure);
}
