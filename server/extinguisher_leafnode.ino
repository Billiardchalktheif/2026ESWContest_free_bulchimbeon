/*
  불침번 — 소화기 리프노드 (ESP32-C3 SuperMini, 배터리 구동)
  역할: MPU6500 가속도로 이동/충격을 감지해 ESP-NOW로 게이트웨이
        (extinguisher_gateway)에 전송한다. WiFi/UDP를 직접 쓰지 않는 이유:
        배터리 절약. ESP-NOW는 AP 연결 없이 저전력으로 짧은 패킷을 주고받을 수 있다.

  v2 설계 변경: 압력센서는 완전히 폐기됐다(§3) — 게이지 지름이 30mm라 센서
  부착이 물리적으로 어렵고, 카메라 방식은 대량 배포 원가가 안 맞기 때문이다.
  대신 "들리면 가속도가 튄다"는 물리적 사실만으로 이동/이탈을 감지하고,
  1차 판정(가속도)과 2차 확인(게이트웨이 연결상태 유지 여부)을 조합해 오탐을
  거른다. 실제 판정(2단계 확정)은 전부 라즈베리파이(server/tamper_detection.py)
  쪽에서 하고, 이 노드는 가속도 값과 임계 초과 여부만 보내는 역할만 한다.

  확정 핀 배치(§5, ESP32-C3 기준):
    - MPU6500 SDA -> GPIO8
    - MPU6500 SCL -> GPIO9
    - MPU6500 INT -> GPIO2 (딥슬립 ext1 웨이크 소스로 사용)

  전력 설계 — 딥슬립 필수:
    - 평상시: 1시간마다 타이머로 깨서(esp_sleep_enable_timer_wakeup) 측정 후 즉시 재취침
    - 예외: MPU6500이 움직임을 감지하면 인터럽트 핀으로 즉시 깨움
      ⚠️ ESP32-C3는 클래식 ESP32와 달리 ext0 웨이크소스가 없다(칩 자체 제약,
      버그 아님). 대신 ext1(다중 RTC GPIO 지원)을 하나의 핀만 써서 사용한다:
      esp_sleep_enable_ext1_wakeup(BIT(GPIO2), ESP_EXT1_WAKEUP_ANY_HIGH)

  MPU6500 접근 방식: 전용 라이브러리가 표준화돼 있지 않아 레지스터를 직접
  읽고 쓰는 방식으로 구현했다 (Motion Detection 인터럽트 레지스터 사용).

  boot_id/seq 관리: 딥슬립은 RTC 메모리(RTC_DATA_ATTR)를 유지한 채로 깨어난다.
  esp_sleep_get_wakeup_cause()로 "콜드부팅"과 "딥슬립 웨이크업"을 구분해서,
  콜드부팅일 때만 새 boot_id를 생성하고 seq를 0으로 리셋한다.
*/

#include <esp_now.h>
#include <esp_sleep.h>
#include <WiFi.h>
#include <Wire.h>

// ---- 노드 식별 (리프노드 4개마다 다르게 설정해서 플래싱) ----
const int NODE_INDEX = 0;   // 0~3 (ext_01~ext_04에 대응)

// ---- 게이트웨이 MAC 주소 (extinguisher_gateway.ino 플래싱 후 시리얼 출력으로 확인해 채울 것) ----
uint8_t gatewayAddress[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};

// ---- 핀/상수 설정 (실측 후 재조정) ----
const int MPU_SDA_PIN = 8;
const int MPU_SCL_PIN = 9;
const int MPU_INT_PIN = 2;    // ext1 딥슬립 웨이크 소스
const int MPU_I2C_ADDR = 0x68;

const uint64_t DEEP_SLEEP_INTERVAL_US = 3600ULL * 1000000ULL;  // 1시간

// 딥슬립 동안에도 유지되는 RTC 메모리 — boot_id/seq를 여기 저장
RTC_DATA_ATTR uint32_t bootId = 0;
RTC_DATA_ATTR uint32_t seqNum = 0;
RTC_DATA_ATTR bool initialized = false;

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

// 이탈/충격 감지용 모션 인터럽트를 설정한다. 값은 실측 후 감도 튜닝 필요.
void configureMpuMotionInterrupt() {
  mpuWriteReg(0x6B, 0x00);   // PWR_MGMT_1: 절전모드 해제(wake up)
  mpuWriteReg(0x1F, 20);     // MOT_THR: 모션 감지 임계값 (실측 후 튜닝)
  mpuWriteReg(0x20, 40);     // MOT_DUR: 임계값 유지 시간(ms 단위, 실측 후 튜닝)
  mpuWriteReg(0x37, 0xA0);   // INT_PIN_CFG: active-high, latch(읽기 전까지 유지)
  mpuWriteReg(0x38, 0x40);   // INT_ENABLE: MOT_EN 비트 활성화
  mpuReadReg(0x3A);          // INT_STATUS 읽어서 인터럽트 플래그 클리어
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

void goToDeepSleep() {
  esp_sleep_enable_timer_wakeup(DEEP_SLEEP_INTERVAL_US);
  // ESP32-C3는 ext0가 없어 ext1(비트마스크 방식)으로 단일 핀 웨이크소스를 설정한다.
  esp_sleep_enable_ext1_wakeup(1ULL << MPU_INT_PIN, ESP_EXT1_WAKEUP_ANY_HIGH);
  Serial.println("딥슬립 진입");
  Serial.flush();
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN);

  esp_sleep_wakeup_cause_t wakeCause = esp_sleep_get_wakeup_cause();
  bool isColdBoot = (wakeCause == ESP_SLEEP_WAKEUP_UNDEFINED) || !initialized;
  if (isColdBoot) {
    bootId = esp_random();
    seqNum = 0;
    initialized = true;
    Serial.println("콜드부팅 — 새 boot_id 생성");
  } else {
    Serial.printf("딥슬립 웨이크업 (원인=%d)\n", (int)wakeCause);
  }

  configureMpuMotionInterrupt();
  float accelMagnitude = measureAccelMagnitude();

  WiFi.mode(WIFI_STA);  // ESP-NOW는 WiFi STA 모드 초기화가 필요하지만 AP 연결은 하지 않음
  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW 초기화 실패 — 이번 측정 전송 없이 재취침");
    goToDeepSleep();
    return;
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, gatewayAddress, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;
  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("ESP-NOW 피어 등록 실패");
  }

  bool ok = sendToGateway(accelMagnitude);
  Serial.printf("전송 %s (가속도=%.2f m/s^2)\n", ok ? "성공" : "실패", accelMagnitude);

  delay(100);  // ESP-NOW 전송 완료 대기 (아주 짧은 시간이라 배터리 영향 미미)
  goToDeepSleep();
}

void loop() {
  // 딥슬립 기반 구조라 loop()는 사용하지 않음 — 모든 동작은 setup()에서 완료 후 재취침
}
