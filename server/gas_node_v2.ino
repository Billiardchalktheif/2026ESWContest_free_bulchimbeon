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
    - LOG_INTERVAL_MS 신설: 5분마다 시리얼에 CSV 한 줄씩 출력 (로컬 raw 데이터 백업용,
      CoolTerm 등으로 캡처해서 사용)
      * 분사 주기(7분30초=450초)보다 짧은 5분(300초)으로 잡아서, 한 로그 구간에
        분사가 2번 겹쳐 잡히는 걸 방지 (구간당 분사 0회 또는 1회로만 나오게)
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
const float DEFAULT_CALIBRATION_FACTOR = 420.0;  // 실측 후 재조정 (기준 분동으로 캘리브레이션)

const unsigned long SEND_INTERVAL_MS = 1800000UL;  // 30분 (서버 UDP 전송용, 3일 테스트 기준 조정됨)
const unsigned long LOG_INTERVAL_MS = 300000UL;     // 5분 (시리얼 CSV 로컬 로그용)
const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

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
  time_t now;
  time(&now);
  if (now < NTP_SYNCED_THRESHOLD) {
    return millis() / 1000.0;
  }
  return (double)now;
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

// 시리얼 명령 처리 — 설치/점검 담당자가 의도적으로 캘리브레이션할 때만 사용.
// 자동으로 실행되지 않는다(이유는 파일 상단 설계 노트 참고).
void handleSerialCommands() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd == "TARE") {
    scale.tare();
    Serial.println("영점(tare) 재조정 완료 — 용기를 올리기 전 빈 상태에서만 실행할 것");
  } else if (cmd == "SET_INITIAL") {
    float w = scale.get_units(10);
    saveInitialWeight(w);
  } else if (cmd == "RAW") {
    Serial.print("RAW 평균값: ");
    Serial.println(scale.read_average(10));
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

// 5분마다 시리얼에 CSV 한 줄 출력 — CoolTerm 등으로 캡처해서 로컬 raw 데이터로 활용
void logCsvLine() {
  if (!csvHeaderPrinted) {
    Serial.println("epoch_sec,raw,weight_g,loss_pct");
    csvHeaderPrinted = true;
  }
  if (millis() - lastLogMs < LOG_INTERVAL_MS) return;
  lastLogMs = millis();

  long raw = scale.read_average(10);
  float weight = scale.get_units(10);
  float lossPct = (initialWeightG > 0) ? (initialWeightG - weight) / initialWeightG * 100.0 : 0;
  double epoch = getEpochSeconds();

  Serial.printf("%.0f,%ld,%.2f,%.2f\n", epoch, raw, weight, lossPct);
}

void setup() {
  Serial.begin(115200);
  bootId = esp_random();

  scale.begin(HX711_DOUT_PIN, HX711_SCK_PIN);
  scale.set_scale(DEFAULT_CALIBRATION_FACTOR);

  loadInitialWeight();
  if (initialWeightG == 0) {
    Serial.println("기준 중량 미설정 상태 — 설치 후 시리얼로 'SET_INITIAL' 입력 필요");
  }

  connectWiFi();
}

void loop() {
  ensureWiFiConnected();
  handleSerialCommands();
  logCsvLine();

  if (millis() - lastSendMs < SEND_INTERVAL_MS) return;
  lastSendMs = millis();

  float weight = scale.get_units(10);  // 노이즈 감소를 위해 10회 평균
  if (initialWeightG == 0) {
    // 기준 중량이 아직 설정 안 됐으면 현재값을 임시 기준으로 자동 저장
    // (담당자가 SET_INITIAL을 깜빡해도 손실률 계산 자체는 동작하게 하기 위함)
    saveInitialWeight(weight);
  }
  sendPacket(weight);
}
