#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

#define GATEWAY_BUTTON_PIN 4

Adafruit_MPU6050 mpu;

bool accelEventDetected = false;
unsigned long eventStartTime = 0;
const unsigned long CONFIRM_WINDOW_MS = 5000;

float baselineAccel = 0;  // 정지 상태 기준값 - setup에서 직접 측정
const float ACCEL_THRESHOLD = 2.0;

void setup() {
  Serial.begin(115200);
  Serial.println("소화기 리프노드 시작");

  if (!mpu.begin()) {
    Serial.println("MPU6050 연결 실패!");
    while (1) delay(10);
  }

  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  pinMode(GATEWAY_BUTTON_PIN, INPUT_PULLUP);

  // 정지 상태 기준값 측정 (10회 평균)
  delay(500);
  float sum = 0;
  for (int i = 0; i < 10; i++) {
    sensors_event_t a, g, temp;
    mpu.getEvent(&a, &g, &temp);
    float mag = sqrt(a.acceleration.x * a.acceleration.x +
                      a.acceleration.y * a.acceleration.y +
                      a.acceleration.z * a.acceleration.z);
    sum += mag;
    delay(100);
  }
  baselineAccel = sum / 10.0;
  Serial.print("기준값 측정 완료: ");
  Serial.println(baselineAccel);
}

void loop() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  float accelMagnitude = sqrt(a.acceleration.x * a.acceleration.x +
                               a.acceleration.y * a.acceleration.y +
                               a.acceleration.z * a.acceleration.z);
  float accelDelta = abs(accelMagnitude - baselineAccel);  // 하드코딩 9.8 대신 측정된 기준값 사용

  if (!accelEventDetected && accelDelta > ACCEL_THRESHOLD) {
    accelEventDetected = true;
    eventStartTime = millis();
    Serial.println(">> [1단계] 가속도 이벤트 감지! 게이트웨이 연결상태 확인 중...");
  }

  if (accelEventDetected) {
    bool gatewayConnected = (digitalRead(GATEWAY_BUTTON_PIN) == HIGH);
    if (millis() - eventStartTime > CONFIRM_WINDOW_MS) {
      if (gatewayConnected) {
        Serial.println(">> [판정] 같은 층 연결 유지됨 → 오탐 처리, 무시");
      } else {
        Serial.println(">> [판정] 게이트웨이 연결 끊김 → ⚠ 이탈 확정! 경보 발생");
      }
      accelEventDetected = false;
    }
  }

  delay(200);
}