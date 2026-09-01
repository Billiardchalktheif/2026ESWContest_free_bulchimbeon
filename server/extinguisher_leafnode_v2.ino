/*
  불침번 — 소화기 리프노드 (ESP32-C3 SuperMini, 배터리 구동, USB로 상시 충전 가능)
  역할: MPU6500 가속도로 이동/충격을 감지해 ESP-NOW로 게이트웨이
        (extinguisher_gateway_v3.ino)에 전송한다.

  ⚠️ v3 설계 변경 — 딥슬립 구조 폐기:
  이전 버전은 배터리 절약을 위해 평상시 딥슬립 + MPU 인터럽트로 깨우는 구조였으나,
  그 구조 하나 때문에 발생한 문제가 너무 많았다 — ESP32-C3의 GPIO2 부트 스트래핑
  핀 충돌, ext0/ext1 부재로 인한 API 재작성, Native USB CDC가 딥슬립 중 포트
  자체를 내렸다 올리는 데서 오는 시리얼 디버깅 불가, while(!Serial) core
  3.3.5+ 회귀 버그 등. 이 배터리는 리튬폴리머 + TP4056 충전 모듈로 상시 재충전이
  가능하고, 이 프로젝트는 장기 무인 현장 배치가 아니라 대회 시연이 목적이므로
  "몇 달짜리 배터리 수명"보다 "안정적으로 계속 동작하는 것"이 우선이라고
  판단해 딥슬립을 완전히 제거했다.

  대신 loop()에서 주기적으로(POLL_INTERVAL_MS) 가속도를 직접 읽어 소프트웨어로
  임계값을 비교한다 — MPU 하드웨어 모션 인터럽트(INT_PIN_CFG/MOT_THR/MOT_DUR
  레지스터, INT 핀 배선)가 더는 필요 없다. GPIO4에 물려있는 MPU INT 라인은
  배선 그대로 둬도 되지만 코드에서는 아예 안 쓴다.

  전송 정책:
    - 가속도가 임계값을 넘고, 마지막 전송 후 쿨다운이 지났으면 즉시 전송(이벤트)
    - 그와 별개로 HEARTBEAT_INTERVAL_MS마다 무조건 한 번 전송(생존 신고 겸
      서버의 베이스라인/2단계 판정용 스트림 유지)

  워치독: 이제 나머지 6개 노드와 동일한 패턴이다 — loop() 매 반복 맨 처음에
  esp_task_wdt_reset()을 호출해 "나 아직 살아있음"을 알린다. I2C가 멈추는 등
  loop()이 응답을 못 하면 WDT_TIMEOUT_MS 후 강제 재부팅된다.

  boot_id/seq: 이제 항상 콜드부팅이므로(딥슬립 왕복이 없음) RTC_DATA_ATTR로
  유지할 이유가 없다 — setup()에서 매번 새로 생성한다.

  ⚠️ ESP-NOW 채널: 게이트웨이가 실제 공유기(WIFI_SSID)에 붙는 채널과 반드시
  일치해야 한다. 공유기 채널을 고정값으로 설정해두고(예: 6번) 아래
  WIFI_CHANNEL 상수를 그 값과 맞춰라. 게이트웨이 쪽도 동일한 채널로 고정하는
  작업이 별도로 필요하다(claude/ESP-NOW_WiFi_채널_불일치_진단.md 참고, 아직
  게이트웨이 코드에 미반영 상태 — 이 리프노드 작업과는 별개로 처리할 것).
*/

#include <esp_now.h>
#include <esp_wifi.h>
#include <WiFi.h>
#include <Wire.h>
#include "esp_task_wdt.h"  // 워치독 타이머 — Fail-Soft, 이제 나머지 6개 노드와 동일한 패턴

// ---- 노드 식별 (리프노드 4개마다 다르게 설정해서 플래싱) ----
const int NODE_INDEX = 0;   // 0~3 (ext_01~ext_04에 대응)

// ---- 게이트웨이 MAC 주소 (extinguisher_gateway_v3.ino 부팅 시 시리얼 출력으로 확인한 값) ----
uint8_t gatewayAddress[6] = {0x20, 0x50, 0x0D, 0xD2, 0x25, 0x70};

// ---- ESP-NOW 채널: 게이트웨이가 연결하는 공유기(WIFI_SSID) 채널과 반드시 일치 ----
// 실제 채널을 모르면 공유기 관리 페이지에서 채널을 고정값(예:6)으로 바꿔두고 여기 맞출 것.
const uint8_t WIFI_CHANNEL = 6;

// ---- 핀/상수 설정 ----
const int MPU_SDA_PIN = 8;
const int MPU_SCL_PIN = 9;
const int MPU_I2C_ADDR = 0x68;
// MPU_INT_PIN(GPIO4)은 하드웨어 배선은 그대로 둬도 되지만, 소프트웨어 폴링 방식으로
// 전환하면서 더는 코드에서 쓰지 않는다.

// ---- 폴링/전송 주기 ----
const unsigned long POLL_INTERVAL_MS = 200;         // 가속도 읽는 주기
const unsigned long HEARTBEAT_INTERVAL_MS = 60000;  // 움직임 없어도 최소 1분마다 한 번 전송
const unsigned long MOTION_SEND_COOLDOWN_MS = 2000; // 이벤트 전송 후 최소 이 시간은 재전송 안 함(스팸 방지)
const float ACCEL_THRESHOLD_MS2 = 11.5;             // 정지 시 ~9.8 m/s^2(1g) 기준, 실측 후 튜닝 필요

const unsigned long WDT_TIMEOUT_MS = 20000;  // loop()이 이 시간 안에 안 돌면 강제 재부팅

uint32_t bootId = 0;
uint32_t seqNum = 0;

unsigned long lastPollMs = 0;
unsigned long lastSendMs = 0;

typedef struct {
  uint8_t nodeIndex;
  uint32_t seq;
  uint32_t bootId;
  float accelMagnitude;
} ExtinguisherEspNowPacket;

// ---------------------------------------------------------------------------
// MPU6500 레지스터 직접 제어
// ---------------------------------------------------------------------------
void mpuWriteReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_I2C_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

uint8_t mpuReadReg(uint8_t reg) {
  Wire.beginTransmission(MPU_I2C_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_I2C_ADDR, 1);
  return Wire.available() ? Wire.read() : 0;
}

int16_t mpuReadReg16(uint8_t regHigh) {
  Wire.beginTransmission(MPU_I2C_ADDR);
  Wire.write(regHigh);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_I2C_ADDR, 2);
  int16_t value = 0;
  if (Wire.available() >= 2) {
    value = (Wire.read() << 8) | Wire.read();
  }
  return value;
}

// 절전모드 해제만 하면 된다 — 하드웨어 모션 인터럽트 레지스터는 더 이상 사용하지 않는다.
void wakeMpu() {
  mpuWriteReg(0x6B, 0x00);   // PWR_MGMT_1: 절전모드 해제
}

// 가속도 3축을 읽어 벡터 크기(m/s^2)를 계산한다.
// ACCEL_XOUT_H(0x3B)부터 6바이트가 X/Y/Z 순서. 감도는 기본 설정(±2g, 16384 LSB/g) 기준.
float measureAccelMagnitude() {
  int16_t ax = mpuReadReg16(0x3B);
  int16_t ay = mpuReadReg16(0x3D);
  int16_t az = mpuReadReg16(0x3F);
  const float LSB_PER_G = 16384.0;
  const float G_TO_MS2 = 9.80665;
  float gx = ax / LSB_PER_G;
  float gy = ay / LSB_PER_G;
  float gz = az / LSB_PER_G;
  return sqrt(gx * gx + gy * gy + gz * gz) * G_TO_MS2;
}

// ---------------------------------------------------------------------------
// ESP-NOW 송신
// ---------------------------------------------------------------------------
bool sendToGateway(float accelMagnitude) {
  ExtinguisherEspNowPacket pkt;
  pkt.nodeIndex = NODE_INDEX;
  pkt.seq = seqNum++;
  pkt.bootId = bootId;
  pkt.accelMagnitude = accelMagnitude;

  esp_err_t result = esp_now_send(gatewayAddress, (uint8_t*)&pkt, sizeof(pkt));
  return result == ESP_OK;
}

void setup() {
  Serial.begin(115200);
  unsigned long serialWaitStart = millis();
  while (!Serial && millis() - serialWaitStart < 5000) {
    delay(10);  // 최대 5초까지 PC와 USB 연결(시리얼 모니터 오픈)될 때까지 대기
  }

  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN);

  // --- 진단용: I2C 배선/주소 확인. MPU6500이면 0x70이 나와야 정상. ---
  Serial.print("WHO_AM_I: 0x");
  Serial.println(mpuReadReg(0x75), HEX);

  esp_task_wdt_config_t twdtConfig = {
    .timeout_ms = WDT_TIMEOUT_MS,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  if (esp_task_wdt_init(&twdtConfig) == ESP_ERR_INVALID_STATE) {
    esp_task_wdt_reconfigure(&twdtConfig);  // 프레임워크가 이미 초기화해둔 경우 재설정
  }
  esp_task_wdt_add(NULL);

  bootId = esp_random();
  seqNum = 0;
  Serial.println("콜드부팅 — 새 boot_id 생성 (딥슬립 없음, 항상 콜드부팅)");

  wakeMpu();

  WiFi.mode(WIFI_STA);  // ESP-NOW는 WiFi STA 모드 초기화가 필요하지만 AP 연결은 하지 않음
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);  // 게이트웨이와 채널 고정 일치

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW 초기화 실패 — 재부팅 필요");
    delay(1000);
    ESP.restart();
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, gatewayAddress, 6);
  peerInfo.channel = WIFI_CHANNEL;
  peerInfo.encrypt = false;
  esp_err_t peerResult = esp_now_add_peer(&peerInfo);
  Serial.printf("ESP-NOW 피어 등록 %s\n", peerResult == ESP_OK ? "성공" : "실패");

  lastPollMs = millis();
  lastSendMs = millis();
  Serial.println("준비됨 — 폴링 시작");
}

void loop() {
  esp_task_wdt_reset();  // "나 아직 살아있음" 신호 — 매 반복 맨 먼저 호출

  unsigned long now = millis();
  if (now - lastPollMs < POLL_INTERVAL_MS) {
    return;
  }
  lastPollMs = now;

  float accelMagnitude = measureAccelMagnitude();

  bool motionExceeded = accelMagnitude >= ACCEL_THRESHOLD_MS2;
  bool cooldownElapsed = (now - lastSendMs) >= MOTION_SEND_COOLDOWN_MS;
  bool heartbeatDue = (now - lastSendMs) >= HEARTBEAT_INTERVAL_MS;

  if ((motionExceeded && cooldownElapsed) || heartbeatDue) {
    bool ok = sendToGateway(accelMagnitude);
    Serial.printf("전송 %s (가속도=%.2f m/s^2, %s)\n",
                   ok ? "성공" : "실패",
                   accelMagnitude,
                   motionExceeded ? "이벤트" : "하트비트");
    lastSendMs = now;
  }
}
