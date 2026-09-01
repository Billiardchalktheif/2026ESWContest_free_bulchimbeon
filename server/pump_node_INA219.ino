/*
  불침번 — 수계 노드 (ESP32 DevKitC) — 충압펌프 + 주펌프 통합
  역할:
    1) 주펌프 INA219 전류값(시계열)을 읽어 feature(RMS/peak/duty cycle)를 현장에서
       계산해 전송 -> 서버가 RandomForest로 정상운전/체절운전/유량저하/공회전/기동실패
       분류 (유일한 AI 적용 지점 중 하나, 2차/상시 — 나머지 하나는 자탐2 비화재보 판별,
       server/nuisance_alarm_classifier.py)
    2) 충압펌프 INA219로 기동 이벤트(전류 상승 엣지)를 감지해 기동 간격을 계산해
       전송 -> 서버가 간격 단축 추세를 보고 미세누수 의심 판정
    3) 성능시험(1차, 규칙기반) — 압력값 + INA219 전류값을 항상 함께 전송한다. 밸브가
       **니들밸브(수동 조작)**로 정정됨에 따라(v4 §5) 이 노드는 체절/부하 상태를
       직접 알지 못한다. 그래서 valve_state를 이 노드가 붙이지 않고 그냥
       pressure_kpa를 계속 흘려보내면, 서버가 압력값 패턴을 보고 체절/부하 상태를
       스스로 추정해서 label을 붙인다(server/pump_performance_test.py의
       determine_valve_state 참고).

  ⚠️ 성능시험(1차)과 AI(2차)의 관계 — 순차 대체가 아니다:
    AI(파형분류)는 이 노드가 항상 상시로 돌린다(loop()의 MAIN_SEND_INTERVAL_MS 주기).
    성능시험은 밸브를 인위적으로(이제는 사람이 손으로) 조작해야 해서 상시로 돌릴 수
    없고, PERF_TEST_AUTO_INTERVAL_MS 주기에만 짧게 펌프를 켜서 발표자가 밸브를
    만질 시간을 준다. 그 순간에만 같은 패킷들에 압력값이 서버의 체절/부하 판정에
    쓰일 뿐, "성능시험 실패시 AI로 넘어간다" 같은 구조가 아니다.

  ⚠️⚠️ v5 정정 — CT클램프 → INA219 교체 (가장 중요한 변경):
    기존 코드는 CT클램프(비접촉, analogRead + AC 커플링 바이어스 회로)를 전제로
    했으나, CT클램프는 전자기 유도 원리라 **직류(DC) 전류를 측정할 수 없다.**
    우리 워터펌프는 12V DC 구동이라 이 상태로는 전류값이 거의 0 또는 무의미한
    노이즈로 측정될 뻔한 근본적 설계 오류였다. INA219(I2C, 션트저항 기반)로
    전면 교체했다.
      - 배선 방식 자체가 바뀜: CT클램프(전선을 감싸기만 함, 비접촉) →
        INA219(전선을 절단해 전류 경로에 직렬로 삽입, High-side)
      - GPIO34/35 아날로그 읽기, ADC_MID_V 바이어스, 커플링 커패시터 관련 코드는
        전부 불필요해짐 (I2C는 그런 아날로그 보정이 필요 없음)
      - 대신 I2C 주소로 두 센서를 구분: 충압펌프=0x40(기본), 주펌프=0x41
        (INA219 보드의 A0 패드를 VCC로 점퍼해서 0x41로 변경)
      - feature_extraction(RMS/peak/duty_cycle) 로직 자체는 그대로 유지 —
        INA219로 읽은 전류값(mA) 시계열에 동일하게 적용

  확정 핀 배치(§3/§5, v5로 정정):
    - INA219(충압펌프, 주소 0x40): SDA -> GPIO21, SCL -> GPIO22 (I2C 공용 버스)
    - INA219(주펌프, 주소 0x41):   SDA -> GPIO21, SCL -> GPIO22 (동일 버스, 주소로 구분)
    - 릴레이 CH1(충압펌프 시험용): GPIO25 (디지털 출력)
    - 릴레이 CH2(주펌프 시험용):   GPIO26 (디지털 출력)
    - 압력센서(G1/4, 0.5~4.5V 출력, 1차 필수 — 성능시험의 핵심 계측 도구): GPIO32 (ADC1)
    ⚠️ 릴레이는 2채널로 충분하다. **솔레노이드 밸브는 구매/배선하지 않는다.**
       기존 니들밸브를 그대로 재사용하고, 발표자가 시연 시 직접 손으로 잠그고 연다.

  배선 메모:
    - INA219는 전선을 실제로 끊어서 전류 경로에 직렬로 삽입해야 한다(High-side):
      릴레이 NO -> INA219 Vin+, INA219 Vin- -> 펌프(+), 펌프(-) -> 어댑터(-) 공통.
      CT클램프처럼 전선을 감싸기만 하면 안 된다.
    - INA219 두 모듈은 같은 I2C 버스(SDA/SCL)를 공유하되 주소만 다르게 설정한다.
      주소 충돌 시 둘 다 같은 값이 찍히거나 통신 에러가 나므로, 배선 직후 시리얼
      모니터로 두 센서가 독립적으로 읽히는지 반드시 확인할 것.
    - ⚠️ 압력센서(G1/4)는 0.5~4.5V를 출력한다 — ESP32 ADC 입력 한계(3.3V)를 넘는
      신호이므로, **반드시 저항분압을 거쳐서** GPIO32(ADC1)로 연결할 것. 분압 없이
      직결하면 ESP32가 손상될 수 있다. 분압비는 PRESSURE_V_MIN/MAX 상수가
      "분압 이후 ESP32가 실제로 보게 되는 전압 범위"를 가리키도록 실측 후 맞출 것.
    - 니들밸브는 배관에 이미 있던 것을 재사용한다 — ESP32/릴레이와 전혀 연결되지
      않는 완전 수동 부품이다. 자동화(솔레노이드)는 결선 단계 확장 계획으로만
      남겨두고 이번 판에서는 구현하지 않는다.
    - 시리얼 명령: TEST_JOCKEY(충압펌프 릴레이 배선 점검), TEST_PERFORMANCE(성능시험
      수동 트리거 — 펌프를 잠깐 켜서 발표자가 밸브를 조작할 시간을 줌),
      TEST_DRYRUN(공회전 파형 수동 캡처 — 흡입측 차단/탱크 비움 상태에서 실행).

  라이브러리: Adafruit INA219 (아두이노 라이브러리 매니저에서 "Adafruit INA219" 검색)

  견고화 내역: millis() 논블로킹, WiFi 자동 재연결, ArduinoJson, boot_id, NTP epoch 동기화
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_INA219.h>
#include <time.h>

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "YOUR_SSID";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER_IP = "YOUR_SERVER_IP";   // 라즈베리파이 IP
const uint16_t SERVER_PORT = 9000;

// INA219 I2C 주소 — 보드의 A0 패드를 VCC로 점퍼하면 0x40 -> 0x41로 변경됨
const uint8_t JOCKEY_INA219_ADDR = 0x40;   // 충압펌프 (기본 주소, 점퍼 없음)
const uint8_t MAIN_INA219_ADDR   = 0x41;   // 주펌프 (A0 패드 -> VCC 점퍼)

const int PRESSURE_PIN = 32;        // ADC1 — 압력센서(1차 필수, 저항분압 후 입력 — 배선 메모 참고)
const int RELAY_JOCKEY_PIN = 25;
const int RELAY_MAIN_PIN = 26;
// v4: 솔레노이드 밸브 릴레이(CH3)는 제거됨 — 니들밸브 수동조작으로 정정

const int SAMPLE_COUNT = 100;       // 주펌프 전류 한 사이클당 샘플 수 (I2C 통신 속도 고려해 CT클램프 시절보다 축소)
const unsigned long SAMPLE_INTERVAL_US = 2000;  // 샘플 간격(us) — INA219 변환시간 고려해 실측 후 재조정

// 압력센서 "저항분압 이후" 전압 -> kPa 변환 (실측 후 정확한 값으로 교체).
const float PRESSURE_V_MIN = 0.5;    // 센서 0 kPa 상당 전압(분압 후 기준)
const float PRESSURE_V_MAX = 3.0;    // 센서 최대범위 전압(분압 후 기준, 3.3V 이내로 낮춘 값)
const float PRESSURE_KPA_MAX = 1000.0;  // 센서 최대범위 압력(kPa)
const float ADC_REF_V = 3.3;
const int ADC_MAX = 4095;           // 12bit

// ---- 성능시험(1차) 관련 상수 ----
#define PUMP_TEST_ENVIRONMENT_TESTBED
#ifdef PUMP_TEST_ENVIRONMENT_TESTBED
const unsigned long PERF_TEST_AUTO_INTERVAL_MS = 6UL * 3600UL * 1000UL;    // 테스트베드: 6시간
#else
const unsigned long PERF_TEST_AUTO_INTERVAL_MS = 180UL * 86400UL * 1000UL; // 배포: 180일(법정 점검주기 근접)
#endif
const unsigned long PERF_TEST_DURATION_MS = 60000;   // 발표자가 밸브를 조작할 시간을 충분히 줌

// 충압펌프 기동 감지 — 전류가 이 임계값(mA)을 상승 통과하면 "기동 시작"으로 판단(실측 후 튜닝)
const float JOCKEY_START_THRESHOLD_MA = 50.0;   // CT클램프 시절 전압 임계값(0.3V)을 전류(mA) 기준으로 변경
const unsigned long JOCKEY_POLL_INTERVAL_MS = 500;   // 기동 감지 폴링 주기(파형 아니므로 느려도 됨)

const unsigned long MAIN_SEND_INTERVAL_MS = 3000;      // 주펌프 전류 사이클 간격(평상시/성능시험 공용)
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

WiFiUDP udp;
Adafruit_INA219 inaJockey(JOCKEY_INA219_ADDR);
Adafruit_INA219 inaMain(MAIN_INA219_ADDR);

uint32_t seqNum = 0;      // 주펌프/충압펌프 패킷 공용 시퀀스 (device_type+pump_type로 서버가 구분)
uint32_t bootId;

unsigned long lastMainSendMs = 0;
unsigned long lastWifiCheckMs = 0;
unsigned long lastJockeyPollMs = 0;
bool jockeyWasRunning = false;
double jockeyLastStartEpoch = -1;

float samples[SAMPLE_COUNT];   // 전류값(mA) 시계열 버퍼

// ---- 성능시험(1차) 상태 ----
enum PerfTestState { PERF_IDLE, PERF_RUNNING };
PerfTestState perfState = PERF_IDLE;
unsigned long perfStateStartMs = 0;
unsigned long perfLastAutoRunMs = 0;

// ---------------------------------------------------------------------------
// WiFi / 시각 동기화 — 다른 노드와 동일 패턴
// ---------------------------------------------------------------------------
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
// 주펌프 — INA219 전류(mA) 시계열 샘플링 및 feature 계산
// (CT클램프 시절의 sampleMainPumpWaveform()을 대체. computeRMS/Peak/DutyCycle은
//  server/feature_extraction.py와 동일 로직을 유지하되, 입력이 "AC 파형 진폭"에서
//  "DC 전류값(mA)의 시간에 따른 변화"로 바뀌었다는 점에 유의할 것)
// ---------------------------------------------------------------------------
void sampleMainPumpCurrent() {
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    samples[i] = inaMain.getCurrent_mA();
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

// duty_cycle의 의미가 CT클램프(AC 파형이 임계치를 넘는 비율)와 살짝 달라짐 —
// 여기서는 "평균 대비 변동폭이 threshold를 넘는 샘플의 비율"로 재정의해 리플/맥동을 봄
float computeDutyCycle(float threshold) {
  float mean = computeMean();
  int active = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    if (abs(samples[i] - mean) > threshold) active++;
  }
  return (float)active / SAMPLE_COUNT;
}

// v4: valve_state는 이 노드가 더 이상 판단하지 않는다(니들밸브가 수동이라 알 방법이
// 없음) — 그래서 이 함수는 항상 pressure_kpa만 함께 실어 보내고, "지금이 체절인지
// 부하인지"는 서버가 압력값 추세를 보고 알아낸다(server/pump_performance_test.py).
void sendMainPumpPacket(float rms, float peak, float duty, float pressureKpa,
                        const char* valveState = nullptr) {
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
  if (!isnan(pressureKpa)) doc["pressure_kpa"] = pressureKpa;
  if (valveState != nullptr) doc["valve_state"] = valveState;
  // label 필드는 이 노드에서 보내지 않는다 — 성능시험 정답 라벨은 서버가
  // 압력값으로 추정한 valve_state를 보고 직접 채운다(§5 결합안 핵심).

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));
  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// ---------------------------------------------------------------------------
// 압력센서 (1차 필수 — 성능시험의 핵심 계측 도구, 반드시 저항분압 후 입력할 것)
// ---------------------------------------------------------------------------
float measurePressureKpa() {
  int raw = analogRead(PRESSURE_PIN);
  float v = (raw / (float)ADC_MAX) * ADC_REF_V;
  float ratio = (v - PRESSURE_V_MIN) / (PRESSURE_V_MAX - PRESSURE_V_MIN);
  return ratio * PRESSURE_KPA_MAX;
}

// ---------------------------------------------------------------------------
// 충압펌프 — INA219 전류(mA) 상승 엣지로 기동 이벤트 감지, 기동 간격 계산
// (파형 분류가 아니라 "켜졌다/꺼졌다"만 보면 되므로 저속 폴링으로 충분)
// ---------------------------------------------------------------------------
void sendJockeyPumpPacket(double intervalSec);

void pollJockeyPump() {
  if (millis() - lastJockeyPollMs < JOCKEY_POLL_INTERVAL_MS) return;
  lastJockeyPollMs = millis();

  float currentMa = inaJockey.getCurrent_mA();
  bool isRunning = currentMa > JOCKEY_START_THRESHOLD_MA;

  if (isRunning && !jockeyWasRunning) {
    // 상승 엣지 감지 = 기동 시작
    double nowEpoch = getEpochSeconds();
    if (jockeyLastStartEpoch > 0) {
      double intervalSec = nowEpoch - jockeyLastStartEpoch;
      sendJockeyPumpPacket(intervalSec);
    }
    jockeyLastStartEpoch = nowEpoch;
  }
  jockeyWasRunning = isRunning;
}

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
  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// ---------------------------------------------------------------------------
// 충압펌프 릴레이 배선 확인용 — 성능시험 대상이 아니라(§4/§5는 주펌프 한정) 단순 점검 트리거
// ---------------------------------------------------------------------------
void triggerRelayTest(int pin, unsigned long durationMs) {
  digitalWrite(pin, HIGH);
  delay(durationMs);  // 배선 점검용 트리거는 드물게 수동으로만 실행되므로 짧은 블로킹은 허용
  digitalWrite(pin, LOW);
}

// ---------------------------------------------------------------------------
// 성능시험(1차, 규칙기반) — 이 노드는 "펌프를 켜서 발표자에게 밸브 조작 시간을
// 주는 것"까지만 담당하고, 체절/부하 구분은 서버가 압력값으로 알아낸다.
// PERF_TEST_AUTO_INTERVAL_MS 주기로 자동 실행되거나, 시리얼 명령
// "TEST_PERFORMANCE"로 수동 트리거할 수 있다.
// ---------------------------------------------------------------------------
void startPerformanceTest() {
  if (perfState != PERF_IDLE) {
    Serial.println("이미 성능시험 진행 중 — 요청 무시");
    return;
  }
  Serial.println("성능시험 시작: 펌프 기동 — 지금부터 니들밸브를 손으로 잠갔다 여세요");
  digitalWrite(RELAY_MAIN_PIN, HIGH);
  perfState = PERF_RUNNING;
  perfStateStartMs = millis();
}

void updatePerformanceTest() {
  if (perfState != PERF_RUNNING) return;
  if (millis() - perfStateStartMs >= PERF_TEST_DURATION_MS) {
    Serial.println("성능시험 종료 — 펌프 정지");
    digitalWrite(RELAY_MAIN_PIN, LOW);
    perfState = PERF_IDLE;
  }
}

// 공회전(dryrun) 캡처 — 흡입측 차단/탱크 비움은 밸브 개폐와 무관한 별도 물리 조작이라
// 자동화하지 않고, 담당자가 그 상태를 만든 뒤 수동으로만 트리거한다.
void captureDryrunSample() {
  sampleMainPumpCurrent();
  float rms = computeRMS();
  float peak = computePeak();
  float duty = computeDutyCycle(0.3 * peak);
  sendMainPumpPacket(rms, peak, duty, NAN, "dryrun");
}

void handleSerialCommands() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "TEST_JOCKEY") {
    Serial.println("충압펌프 배선 점검 릴레이 트리거");
    triggerRelayTest(RELAY_JOCKEY_PIN, 2000);
  } else if (cmd == "TEST_PERFORMANCE") {
    startPerformanceTest();
  } else if (cmd == "TEST_DRYRUN") {
    Serial.println("공회전 파형 캡처 — 흡입측을 먼저 차단/탱크를 비운 상태인지 확인할 것");
    captureDryrunSample();
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  bootId = esp_random();

  pinMode(RELAY_JOCKEY_PIN, OUTPUT);
  pinMode(RELAY_MAIN_PIN, OUTPUT);
  digitalWrite(RELAY_JOCKEY_PIN, LOW);
  digitalWrite(RELAY_MAIN_PIN, LOW);

  Wire.begin();  // INA219 두 개가 공유하는 I2C 버스 초기화

  if (!inaJockey.begin()) {
    Serial.println("[WARN] INA219(충압펌프, 0x40) 초기화 실패 — 배선/주소 확인 필요");
  }
  if (!inaMain.begin()) {
    Serial.println("[WARN] INA219(주펌프, 0x41) 초기화 실패 — A0 패드 점퍼 확인 필요");
  }

  connectWiFi();
  perfLastAutoRunMs = millis();  // 부팅 직후 바로 시험이 돌지 않도록 기준시각을 지금으로 설정
}

void loop() {
  ensureWiFiConnected();
  handleSerialCommands();
  pollJockeyPump();
  updatePerformanceTest();

  // 성능시험(1차)은 정해진 주기에만 자동 실행 — AI(2차, 아래 전류패턴분류)는 이 조건과
  // 무관하게 항상 상시로 돈다(파일 상단 "관계" 설명 참고).
  if (perfState == PERF_IDLE && millis() - perfLastAutoRunMs >= PERF_TEST_AUTO_INTERVAL_MS) {
    perfLastAutoRunMs = millis();
    startPerformanceTest();
  }

  if (millis() - lastMainSendMs < MAIN_SEND_INTERVAL_MS) return;
  lastMainSendMs = millis();

  // 압력센서는 1차 필수 계측 도구라 성능시험 중이든 평상시든 항상 함께 측정해 보낸다.
  // "지금이 체절/부하/평상시인지"는 이제 이 노드가 아니라 서버가 압력값 추세로 판단한다.
  sampleMainPumpCurrent();
  float rms = computeRMS();
  float peak = computePeak();
  float duty = computeDutyCycle(0.3 * peak);
  float pressure = measurePressureKpa();
  sendMainPumpPacket(rms, peak, duty, pressure);
}
