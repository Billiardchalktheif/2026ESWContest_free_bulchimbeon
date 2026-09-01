/*
  불침번 — 가스계(CO2/할론 소화설비) 노드 (ESP32 DevKitC)
  역할: 로드셀(HX711)로 용기 중량을 상시 측정해 라즈베리파이로 전송한다.
        서버(server/regression_forecast.py)가 선형회귀로 "5% 손실 도달까지
        남은 일수"를 계산한다.

  라이브러리: HX711 (아두이노 라이브러리 매니저에서 "HX711" 검색, bogde/HX711 기준)
             ArduinoJson (6.x 버전, Benoit Blanchon)

  배선 메모:
    - HX711 DOUT -> GPIO16, SCK -> GPIO17 (다른 핀도 가능, 여기선 예시)
    - 로드셀 4선(빨강/검정/흰색/초록)을 HX711 E+/E-/A+/A-에 규격대로 연결

  캘리브레이션 관련 설계 노트:
    - calibration_factor(HX711 raw -> gram 환산 계수)와 initial_weight_g(최초
      영점/기준 중량)는 EEPROM 대신 ESP32 내장 Preferences(NVS)에 저장한다.
      재부팅해도 유지되어야 하는 값이기 때문.
    - "주기적 영점 재보정"은 자동 타이머로 하지 않는다 — 이미 용기가 걸려있는
      상태에서 자동으로 tare()를 걸면 실제 중량이 0으로 리셋되어 버리는
      심각한 오류가 생기기 때문. 대신 시리얼 명령으로 설치 담당자가 의도적으로
      트리거하도록 했다 (TARE: 빈 상태 영점 조정, SET_INITIAL: 현재 중량을
      기준 중량으로 저장).

  견고화 내역: pump_node.ino와 동일 패턴 적용
    (millis() 논블로킹, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)

  [수정 — 3일 방향제 캘리브레이션 테스트용]
    - SEND_INTERVAL_MS: 1일 -> 30분 (서버로 UDP 전송, 회귀분석 재료)
    - LOG_INTERVAL_MS 신설: 5분마다 시리얼에 CSV 한 줄씩 출력 (로컬 raw 데이터 백업용)
    - WIFI_CHECK_INTERVAL_MS: 5초 -> 5분 (재연결 시도 스팸 완화)
    - CSV 첫 컬럼을 raw epoch 대신 사람이 읽을 수 있는 시각 문자열로 변경
      * SET_TIME 명령으로 수동 동기화한 경우: "2026-08-16 14:23:05" (컴퓨터 시간 기준)
      * NTP 동기화 성공 시: "2026-08-16 14:23:05" 형태 (실제 날짜/시간, WiFi 쓸 때)
      * 둘 다 안 된 상태: "boot+00:10:05" 형태 (부팅 후 경과시간)
    - SET_TIME 명령 신설: WiFi 없이 컴퓨터 시각을 수동으로 한 번 넣어주면, 그 이후로는
      millis() 기준으로 계속 흘러가면서 실제 시각처럼 찍힘 (이 테스트에서 WiFi는 일부러
      안 쓰고, 나중에 라즈베리파이 연동할 때는 친구가 WIFI_SSID/PASS 채워서 NTP로 쓰면 됨)
    - DEFAULT_CALIBRATION_FACTOR: 420.0(임시값) -> 93.07 (502g 실측 역산값)
    - AVG_SAMPLES 신설(=30): 실측해보니 로드셀 raw 노이즈가 ±20~30g 상당으로 커서,
      TARE/SET_INITIAL/RAW/CSV로그/전송 시 평균 샘플 수를 전부 10 -> 30으로 상향해서
      노이즈를 줄임. 5분 간격 로깅이라 샘플 늘려도 시간 여유는 충분함.
    - DEFAULT_CALIBRATION_FACTOR: 93.07 -> 87.9 (흰/초록 신호선 납땜 후 재캘리브레이션.
      납땜으로 물리적 배선이 바뀌면 raw 신호 특성도 같이 바뀌므로, 하드웨어를 만질 때마다
      실측 기준으로 다시 역산해야 함 — 501g 실측 vs 473.2g 표시 -> 93.07*(473.2/501)≈87.9)

  [v3 — 택트스위치 추가, 노트북/대시보드 없이 보조배터리 단독 시연 대응]
    - 시연 흐름(가스통 올리기 전/후)에 맞춰 TARE/SET_INITIAL을 시리얼 없이 현장에서
      바로 트리거해야 해서, GPIO18/19에 택트스위치를 달고 loop() 폴링 + millis()
      디바운스로 처리한다 (유도등 노드와 동일 패턴 — attachInterrupt 대신 폴링을 쓰는
      이유는 인터럽트 방식에서 오인식 문제가 있었기 때문, 유도등 v3 참고).
      GPIO18 = TARE(영점조정, 가스통 올리기 전 빈 상태에서 누름)
      GPIO19 = SET_INITIAL(기준중량 저장, 가스통 올린 직후 누름)
      시리얼 명령(TARE/SET_INITIAL/RAW/MONITOR/STOP/SET_TIME)은 디버깅용으로 그대로 유지 —
      버튼과 시리얼 둘 다 같은 함수(scale.tare / saveInitialWeight)를 호출하므로 충돌 없음.
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <HX711.h>
#include <Preferences.h>
#include <time.h>

// ---- 설정값 (실측 후 재조정) ----
const char* WIFI_SSID = "YOUR_SSID";       // TODO: 실제 WiFi 이름으로 교체
const char* WIFI_PASS = "YOUR_PASSWORD";   // TODO: 실제 WiFi 비밀번호로 교체
const char* SERVER_IP = "YOUR_SERVER_IP";    // TODO: 라즈베리파이 실제 IP로 교체 (없으면 무시돼도 무관)
const uint16_t SERVER_PORT = 9000;

const char* NODE_ID = "gas_co2_01";
const char* ZONE_NAME = "가스계실";
// 'co2' / 'halon' / 'inert' — 종류별로 서버의 임계 손실률이 다르게 적용된다(§4,
// server/regression_forecast.py의 GAS_LOSS_THRESHOLD_PCT 참고). 실제 설치된 가스 종류로 교체할 것.
const char* GAS_TYPE = "co2";

const int HX711_DOUT_PIN = 16;
const int HX711_SCK_PIN = 17;
const float DEFAULT_CALIBRATION_FACTOR = 87.9;  // 납땜 후 재측정: 501g 실측 기준 역산값 배선 납땜 후 정리 
const byte AVG_SAMPLES = 30;  // 로드셀 노이즈가 커서(±20~30g 수준) 10 -> 30으로 상향

const unsigned long SEND_INTERVAL_MS = 1800000UL;  // 30분 (서버 UDP 전송용)
const unsigned long LOG_INTERVAL_MS = 300000UL;     // 5분 (시리얼 CSV 로컬 로그용)
const unsigned long WIFI_CHECK_INTERVAL_MS = 300000UL;  // 5분 (재연결 시도 스팸 완화)
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

// ---- 캘리브레이션 트리거 버튼 (v3 신규) ----
// 택트스위치(4핀, 대각선 페어): 다리 하나 -> GPIO, 대각선 반대 다리 -> GND.
// INPUT_PULLUP이므로 평소 HIGH, 누르면 LOW.
const int BUTTON_TARE_PIN = 18;         // TARE 대체 — 가스통 올리기 전, 빈 상태에서 누름
const int BUTTON_SET_INITIAL_PIN = 19;  // SET_INITIAL 대체 — 가스통 올린 직후 누름
const unsigned long BUTTON_DEBOUNCE_MS = 50;  // 이 시간 이상 안정적으로 유지돼야 진짜 입력으로 인정

WiFiUDP udp;
HX711 scale;
Preferences prefs;
uint32_t seqNum = 0;
uint32_t bootId;

float initialWeightG = 0;
unsigned long lastSendMs = 0;
unsigned long lastLogMs = 0;
unsigned long lastWifiCheckMs = 0;
bool csvHeaderPrinted = false;

// SET_TIME으로 수동 동기화한 컴퓨터 시각 (WiFi/NTP 없이 시간 표시용)
bool manualTimeSet = false;
time_t manualBaseEpoch = 0;
unsigned long manualBaseMillis = 0;

// 배선 접촉불량 진단용 실시간 raw 모니터링 모드
bool monitorMode = false;
unsigned long lastMonitorMs = 0;

// 버튼별 디바운스 상태 (인터럽트 대신 폴링 — 유도등 노드와 동일 패턴)
bool buttonTareLastReading = HIGH;
bool buttonTareStableState = HIGH;
unsigned long buttonTareLastChangeMs = 0;

bool buttonSetInitialLastReading = HIGH;
bool buttonSetInitialStableState = HIGH;
unsigned long buttonSetInitialLastChangeMs = 0;

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
    Serial.println(" 연결 실패 - loop()에서 계속 재시도함 (WiFi 없어도 시리얼 로그는 계속 찍힘)");
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
  if (manualTimeSet) {
    return (double)(manualBaseEpoch + (millis() - manualBaseMillis) / 1000);
  }
  time_t now;
  time(&now);
  if (now < NTP_SYNCED_THRESHOLD) {
    return millis() / 1000.0;
  }
  return (double)now;
}

// 사람이 읽기 좋은 시각 문자열 반환.
// 우선순위: SET_TIME으로 수동 동기화한 값 > NTP 동기화 값 > 부팅 후 경과시간
// - 수동/NTP 동기화 시: "YYYY-MM-DD HH:MM:SS" (실제 날짜/시간)
// - 둘 다 안 된 상태: "boot+HH:MM:SS" (부팅 후 경과시간)
String getTimestampString() {
  if (manualTimeSet) {
    time_t now = manualBaseEpoch + (millis() - manualBaseMillis) / 1000;
    struct tm timeinfo;
    localtime_r(&now, &timeinfo);
    char buf[24];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &timeinfo);
    return String(buf);
  }
  time_t now;
  time(&now);
  if (now < NTP_SYNCED_THRESHOLD) {
    unsigned long sec = millis() / 1000;
    unsigned long h = sec / 3600;
    unsigned long m = (sec % 3600) / 60;
    unsigned long s = sec % 60;
    char buf[24];
    snprintf(buf, sizeof(buf), "boot+%02lu:%02lu:%02lu", h, m, s);
    return String(buf);
  }
  struct tm timeinfo;
  localtime_r(&now, &timeinfo);
  char buf[24];
  strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &timeinfo);
  return String(buf);
}

// NVS(비휘발성 메모리)에 저장된 초기중량을 불러온다. 없으면(최초 부팅) 0 반환.
void loadInitialWeight() {
  prefs.begin("gas_node", true);  // read-only 모드
  initialWeightG = prefs.getFloat("initial_g", 0);
  prefs.end();
}

void saveInitialWeight(float w) {
  prefs.begin("gas_node", false);
  prefs.putFloat("initial_g", w);
  prefs.end();
  initialWeightG = w;
  Serial.printf("기준 중량 저장됨: %.1f g\n", w);
}

// 버튼(GPIO18)과 시리얼('TARE') 둘 다 이 함수를 호출한다.
void doTare() {
  scale.tare(AVG_SAMPLES);
  Serial.println("영점(tare) 재조정 완료 — 용기를 올리기 전 빈 상태에서만 실행할 것");
}

// 버튼(GPIO19)과 시리얼('SET_INITIAL') 둘 다 이 함수를 호출한다.
void doSetInitial() {
  float w = scale.get_units(AVG_SAMPLES);
  saveInitialWeight(w);
}

// 버튼 1개에 대한 폴링+디바운스 처리 (유도등 노드와 동일 패턴).
// 인터럽트 대신 loop()에서 매번 읽고, 값이 BUTTON_DEBOUNCE_MS 이상 안정적으로
// 유지된 뒤 "눌림 순간(HIGH->LOW로 바뀐 그 한 번)"에만 action()을 호출한다.
void pollButton(int pin, bool &lastReading, bool &stableState,
                 unsigned long &lastChangeMs, void (*action)()) {
  bool reading = digitalRead(pin);

  if (reading != lastReading) {
    lastChangeMs = millis();   // 값이 바뀌는 순간마다 디바운스 타이머 리셋
    lastReading = reading;
  }

  if ((millis() - lastChangeMs) > BUTTON_DEBOUNCE_MS) {
    if (reading != stableState) {
      stableState = reading;
      if (stableState == LOW) {   // INPUT_PULLUP -> 눌리면 LOW. 눌리는 그 순간에만 1회 트리거
        action();
      }
    }
  }
}

void checkButtonPress() {
  pollButton(BUTTON_TARE_PIN, buttonTareLastReading, buttonTareStableState,
             buttonTareLastChangeMs, doTare);
  pollButton(BUTTON_SET_INITIAL_PIN, buttonSetInitialLastReading, buttonSetInitialStableState,
             buttonSetInitialLastChangeMs, doSetInitial);
}

// 시리얼 명령 처리 — 설치/점검 담당자가 의도적으로 캘리브레이션할 때만 사용.
// 자동으로 실행되지 않는다(이유는 파일 상단 설계 노트 참고).
void handleSerialCommands() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "TARE") {
    doTare();
  } else if (cmd == "SET_INITIAL") {
    doSetInitial();
  } else if (cmd == "RAW") {
    Serial.print("RAW 평균값: ");
    Serial.println(scale.read_average(AVG_SAMPLES));
  } else if (cmd == "MONITOR") {
    monitorMode = true;
    Serial.println("실시간 모니터링 시작 — 배선(로드셀 4선, HX711 연결부, 두 로드셀 합산 지점)을");
    Serial.println("하나씩 손으로 살짝 눌러/흔들어보면서 값이 튀는 지점 찾기. 끝내려면 STOP 입력");
  } else if (cmd == "STOP") {
    monitorMode = false;
    Serial.println("모니터링 종료");
  } else if (cmd.startsWith("SET_TIME ")) {
    // 사용법: SET_TIME 2026-08-16 14:30:00  (지금 컴퓨터 시계에 뜨는 시각 그대로 입력)
    String datetimeStr = cmd.substring(9);
    struct tm tmStruct = {};
    int y, mo, d, h, mi, s;
    if (sscanf(datetimeStr.c_str(), "%d-%d-%d %d:%d:%d", &y, &mo, &d, &h, &mi, &s) == 6) {
      tmStruct.tm_year = y - 1900;
      tmStruct.tm_mon = mo - 1;
      tmStruct.tm_mday = d;
      tmStruct.tm_hour = h;
      tmStruct.tm_min = mi;
      tmStruct.tm_sec = s;
      tmStruct.tm_isdst = -1;
      manualBaseEpoch = mktime(&tmStruct);
      manualBaseMillis = millis();
      manualTimeSet = true;
      Serial.printf("컴퓨터 시간 동기화 완료: %s\n", datetimeStr.c_str());
    } else {
      Serial.println("형식 오류 — 'SET_TIME 2026-08-16 14:30:00' 형태로 입력해줘 (연-월-일 시:분:초)");
    }
  }
}

void sendPacket(float weightG) {
  StaticJsonDocument<256> doc;
  doc["node_id"] = NODE_ID;
  doc["device_type"] = "gas";
  doc["zone"] = ZONE_NAME;
  doc["gas_type"] = GAS_TYPE;
  doc["seq"] = seqNum++;
  doc["ts"] = getEpochSeconds();
  doc["boot_id"] = bootId;
  doc["weight_g"] = weightG;
  doc["initial_weight_g"] = initialWeightG;

  char buf[256];
  size_t len = serializeJson(doc, buf, sizeof(buf));

  udp.beginPacket(SERVER_IP, SERVER_PORT);
  udp.write((const uint8_t*)buf, len);
  udp.endPacket();
}

// 5분마다 시리얼에 CSV 한 줄 출력 — datetime, raw, weight_g, loss_pct
void logCsvLine() {
  if (!csvHeaderPrinted) {
    Serial.println("datetime,raw,weight_g,loss_pct");
    csvHeaderPrinted = true;
  }
  if (millis() - lastLogMs < LOG_INTERVAL_MS) return;
  lastLogMs = millis();

  long raw = scale.read_average(AVG_SAMPLES);
  float weight = scale.get_units(AVG_SAMPLES);
  float lossPct = (initialWeightG > 0) ? (initialWeightG - weight) / initialWeightG * 100.0 : 0;
  String ts = getTimestampString();

  Serial.printf("%s,%ld,%.2f,%.2f\n", ts.c_str(), raw, weight, lossPct);
}

void setup() {
  Serial.begin(115200);
  delay(200);  // USB-시리얼 라인 안정화 대기 (부팅 직후 전송 시작하면 첫 줄이 깨지는 경우 있음)
  bootId = esp_random();
  // 재부팅 여부를 눈으로 구분하기 위한 배너. 3일 방치 중 이 줄이 다시 뜨면
  // 중간에 리셋(브라운아웃 등)이 있었다는 뜻 — 단순 시리얼 모니터 렌더링 글리치와 구분 가능
  Serial.printf("\n===== 부팅됨 (boot_id=%lu) =====\n", (unsigned long)bootId);

  scale.begin(HX711_DOUT_PIN, HX711_SCK_PIN);
  scale.set_scale(DEFAULT_CALIBRATION_FACTOR);

  pinMode(BUTTON_TARE_PIN, INPUT_PULLUP);
  pinMode(BUTTON_SET_INITIAL_PIN, INPUT_PULLUP);

  loadInitialWeight();
  if (initialWeightG == 0) {
    Serial.println("기준 중량 미설정 상태 — GPIO19 버튼(또는 시리얼 'SET_INITIAL') 입력 필요");
  }

  connectWiFi();
  Serial.println("준비됨 — GPIO18=TARE(영점), GPIO19=SET_INITIAL(기준중량) / 시리얼 명령도 동일 동작");
}

void loop() {
  ensureWiFiConnected();
  handleSerialCommands();
  checkButtonPress();
  logCsvLine();

  if (monitorMode && millis() - lastMonitorMs >= 200) {
    lastMonitorMs = millis();
    Serial.println(scale.read_average(1));  // 진단용이라 평균 없이 순간값 그대로
  }

  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float weight = scale.get_units(AVG_SAMPLES);  // 노이즈 감소를 위해 다회 평균
  if (initialWeightG == 0) {
    // 기준 중량이 아직 설정 안 됐으면 현재값을 임시 기준으로 자동 저장
    // (담당자가 SET_INITIAL을 깜빡해도 손실률 계산 자체는 동작하게 하기 위함)
    saveInitialWeight(weight);
  }
  sendPacket(weight);
}