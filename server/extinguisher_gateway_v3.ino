/*
  불침번 — 소화기 게이트웨이 (ESP32 DevKitC, 상시 전원, 센서 없음)
  역할: 리프노드 4개(extinguisher_leafnode.ino)가 ESP-NOW로 보내는 가속도
        데이터를 받아 JSON으로 변환한 뒤 WiFi/UDP로 라즈베리파이에 전달한다.
        순수 프로토콜 변환 역할만 하므로 배터리 절약 없이 상시 전원을 쓴다.

  ⚠️ 중요(v2): 이 게이트웨이는 전달하는 각 패킷에 자기 자신의 gateway_id를
  붙여서 보내야 한다. 서버(server/tamper_detection.py)가 "이벤트 발생 후에도
  같은 게이트웨이와 통신이 유지되는지"를 보고 오탐/이탈을 구분하기 때문에,
  이 값이 없으면 2단계 판정 자체가 불가능하다.

  주의: 리프노드와 달리 이 노드가 보내는 UDP 패킷의 node_id/seq/boot_id는
        "게이트웨이 자신의 것"이 아니라 "각 리프노드가 ESP-NOW로 보낸 값"을
        그대로 전달한다. 서버는 리프노드 각각을 독립된 노드로 감시해야 하기 때문
        (게이트웨이는 중계만 할 뿐, 논리적인 노드 단위가 아님).

  배선/설정 메모:
    - 이 스케치를 먼저 플래싱하고 시리얼 모니터에서 출력되는 자신의 MAC 주소를
      확인해 extinguisher_leafnode.ino의 gatewayAddress[]에 채워 넣을 것.
    - ESP-NOW는 WiFi STA와 같은 채널을 써야 하므로, WiFi 연결 후 채널이 바뀌지
      않도록 주의 (공유기 채널 고정 권장).

  견고화 내역: 다른 노드와 동일 패턴
    (millis() 논블로킹, WiFi 자동 재연결, ArduinoJson, boot_id, NTP)
    ESP-NOW 콜백은 인터럽트 컨텍스트와 유사하게 비동기로 호출되므로,
    콜백 안에서는 UDP 전송 같은 무거운 작업을 하지 않고 큐에만 쌓아둔 뒤
    loop()에서 순차적으로 처리한다 (콜백 내 블로킹 작업 금지 원칙).

  점검모드(Test Mode, 자탐1/자탐2와 동일 패턴 이식): 소화기 넘어뜨림/이동 시험
  (tamper 재현) 중에는 서버가 상시로 그대로 반응해서 진짜 이탈 경보를 내보낼 수
  있다 — 시험과 실제 상황을 구분 못 하는 문제. 그래서 물리 택트스위치로 "지금
  점검 중"을 서버에 알려주기만 하고, 판정 로직 자체(server/tamper_detection.py)는
  절대 안 바꾼다. 버튼을 누를 때마다 상태만 토글해서, 게이트웨이가 전달하는
  4개 리프노드 패킷 전부에 같이 실어 보낸다 — 별도 명령 왕복 없음.
  GPIO19 = TEST_MODE_PIN (자탐1/자탐2도 GPIO27을 쓰지만 서로 다른 보드라 충돌 없음).
*/

#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <esp_now.h>
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

// 게이트웨이가 여러 대로 늘어날 경우(장차 확장) 게이트웨이마다 다르게 설정할 값.
// 현재 배치는 1대뿐이라 항상 "gw_01"이지만, tamper_detection.py의 2단계 판정
// (같은/다른 게이트웨이 여부)이 성립하려면 이 값이 꼭 필요하다.
const char* GATEWAY_ID = "gw_01";

const unsigned long WIFI_CHECK_INTERVAL_MS = 5000;
const long NTP_GMT_OFFSET_SEC = 9 * 3600;
const time_t NTP_SYNCED_THRESHOLD = 1700000000;

// nodeIndex(0~3) -> node_id/zone 매핑 (extinguisher_leafnode.ino의 NODE_INDEX와 일치시킬 것)
const char* NODE_IDS[4] = {"ext_01", "ext_02", "ext_03", "ext_04"};
const char* ZONE_NAMES[4] = {"창고 1", "창고 2", "창고 3", "창고 4"};

// ---- 점검모드(Test Mode) 택트스위치 — 자탐1/자탐2와 동일 패턴 ----
const int TEST_MODE_PIN = 19;  // 게이트웨이는 센서 핀을 안 쓰므로 임의 여유 GPIO. 다른 핀 원하면 교체 가능

WiFiUDP udp;
uint32_t gatewayBootId;   // 게이트웨이 자신의 heartbeat(선택적 모니터링용)에만 사용
uint32_t gatewaySeq = 0;

unsigned long lastWifiCheckMs = 0;

bool testModeActive = false;    // 점검모드 상태 — 버튼 누를 때마다 토글, 전달하는 모든 패킷에 실어 보냄
bool lastButtonState = HIGH;    // INPUT_PULLUP이라 평소 HIGH, 누르면 LOW

typedef struct {
  uint8_t nodeIndex;
  uint32_t seq;
  uint32_t bootId;
  float accelMagnitude;
} ExtinguisherEspNowPacket;

// ESP-NOW 콜백은 무거운 작업(UDP 전송 등)을 바로 하지 않고 큐에 쌓아둔다.
// (콜백은 WiFi 드라이버 태스크 컨텍스트에서 호출되어, 여기서 블로킹하면
//  다른 ESP-NOW/WiFi 처리가 지연될 수 있다)
const int QUEUE_SIZE = 8;
ExtinguisherEspNowPacket packetQueue[QUEUE_SIZE];
volatile int queueHead = 0;
volatile int queueTail = 0;

void enqueuePacket(const ExtinguisherEspNowPacket& pkt) {
  int nextTail = (queueTail + 1) % QUEUE_SIZE;
  if (nextTail == queueHead) {
    Serial.println("[WARN] 큐가 가득 참 — 가장 오래된 패킷 덮어씀");
    queueHead = (queueHead + 1) % QUEUE_SIZE;
  }
  packetQueue[queueTail] = pkt;
  queueTail = nextTail;
}

bool dequeuePacket(ExtinguisherEspNowPacket& out) {
  if (queueHead == queueTail) return false;
  out = packetQueue[queueHead];
  queueHead = (queueHead + 1) % QUEUE_SIZE;
  return true;
}

// ESP-NOW 수신 콜백. (아두이노 ESP32 코어 버전에 따라 시그니처가 다를 수 있음 —
// 2.x 코어 기준. 3.x 코어로 빌드 시 esp_now_recv_info_t* 형태로 바뀌므로 확인 필요)
void onEspNowRecv(const esp_now_recv_info_t* recvInfo, const uint8_t* incomingData, int len) {
  if (len != sizeof(ExtinguisherEspNowPacket)) {
    Serial.println("[WARN] 예상과 다른 ESP-NOW 패킷 크기 — 무시");
    return;
  }
  ExtinguisherEspNowPacket pkt;
  memcpy(&pkt, incomingData, sizeof(pkt));
  enqueuePacket(pkt);
}

// 점검모드 버튼 폴링 — loop() 매 반복 초입에서 호출(자탐1/자탐2와 동일 패턴). 디바운스 포함.
// 판정 로직(server/tamper_detection.py)은 절대 건드리지 않는다 — 여기서 바뀌는 건
// testModeActive 플래그 하나뿐이고, 이 값은 그대로 전달 패킷에 실려 서버로 전달만 된다.
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
    Serial.println("게이트웨이 자신의 MAC 주소(리프노드 설정에 사용): " + WiFi.macAddress());
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

void forwardToServer(const ExtinguisherEspNowPacket& pkt) {
  if (pkt.nodeIndex >= 4) {
    Serial.println("[WARN] 알 수 없는 nodeIndex — 무시");
    return;
  }

  Serial.printf("[수신] node=%s seq=%u boot_id=%u accel=%.2f m/s^2\n", NODE_IDS[pkt.nodeIndex], pkt.seq, pkt.bootId, pkt.accelMagnitude);

  // test_mode 필드 추가분만큼 버퍼를 256->300으로 키웠다 (자탐1/자탐2와 동일한 이유).
  StaticJsonDocument<300> doc;
  doc["node_id"] = NODE_IDS[pkt.nodeIndex];
  doc["device_type"] = "extinguisher";
  doc["zone"] = ZONE_NAMES[pkt.nodeIndex];
  doc["seq"] = pkt.seq;          // 리프노드 자신의 seq (게이트웨이 seq 아님)
  doc["boot_id"] = pkt.bootId;   // 리프노드 자신의 boot_id
  doc["ts"] = getEpochSeconds(); // 리프노드엔 RTC/NTP가 없으므로 게이트웨이 수신시각으로 대체
  doc["accel_magnitude"] = pkt.accelMagnitude;
  doc["gateway_id"] = GATEWAY_ID;  // 2단계 판정(같은/다른 게이트웨이)의 핵심 필드
  doc["test_mode"] = testModeActive;  // 점검모드 상태 — 서버 tamper_detection.py가 분기

  // ArduinoJson v6는 용량이 부족해도 에러 없이 필드를 조용히 누락시킨다 — 다른
  // 노드들과 동일하게 여기서 바로 확인한다.
  if (doc.overflowed()) {
    Serial.println("⚠️ JSON 버퍼 부족! StaticJsonDocument 크기를 늘려야 함");
  }

  char buf[300];
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
  esp_task_wdt_add(NULL);  // 현재 태스크(메인 loop)를 감시 대상으로 등록(ESP-NOW 큐 처리 지연도 이걸로 잡힘, 이미 등록돼 있어도 무해)

  gatewayBootId = esp_random();

  pinMode(TEST_MODE_PIN, INPUT_PULLUP);  // 점검모드 택트스위치 — 평소 HIGH, 누르면 LOW

  connectWiFi();

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW 초기화 실패 — 재부팅 필요");
    return;
  }
  esp_now_register_recv_cb(onEspNowRecv);
  Serial.println("준비됨 — GPIO27=점검모드 토글 (누를 때마다 ON/OFF 전환, 전달되는 모든 리프노드 패킷에 반영)");
}

void loop() {
  esp_task_wdt_reset();  // "나 아직 살아있음" 신호 — 매 반복 맨 먼저 호출

  pollTestModeButton();  // 점검모드 버튼 폴링 — 매 반복 확인(디바운스 포함)
  ensureWiFiConnected();

  ExtinguisherEspNowPacket pkt;
  while (dequeuePacket(pkt)) {
    forwardToServer(pkt);
  }
}
