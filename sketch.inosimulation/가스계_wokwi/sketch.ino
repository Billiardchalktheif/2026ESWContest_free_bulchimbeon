#include "HX711.h"

#define HX711_DT  4
#define HX711_SCK 5

HX711 scale;

const float CALIBRATION_FACTOR = 420.0;
const float EMPTY_CONTAINER_WEIGHT = 0.0;
float initialTotalWeight = 0;
bool baselineSet = false;

const float LOSS_THRESHOLD_PERCENT = 10.0;

void setup() {
  Serial.begin(115200);
  scale.begin(HX711_DT, HX711_SCK);
  scale.set_scale(CALIBRATION_FACTOR);
  scale.tare();
  Serial.println("준비 완료. 슬라이더로 초기 무게(예:5kg) 맞춘 뒤 시리얼모니터에 's' 입력하면 기준값이 찍힙니다.");
}

void loop() {
  // 's' 입력 받으면 그 순간 값을 기준값으로 저장
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 's') {
      initialTotalWeight = scale.get_units(10);
      baselineSet = true;
      Serial.print(">> 기준값 설정됨: ");
      Serial.print(initialTotalWeight);
      Serial.println(" g");
    }
  }

  if (baselineSet) {
    float currentTotalWeight = scale.get_units(5);
    float initialAgentWeight = initialTotalWeight - EMPTY_CONTAINER_WEIGHT;
    float currentAgentWeight = currentTotalWeight - EMPTY_CONTAINER_WEIGHT;
    float lossPercent = (initialAgentWeight - currentAgentWeight) / initialAgentWeight * 100.0;

    Serial.print("현재: ");
    Serial.print(currentTotalWeight);
    Serial.print(" g | 손실률: ");
    Serial.print(lossPercent);
    Serial.print(" % | ");
    Serial.println(lossPercent >= LOSS_THRESHOLD_PERCENT ? "⚠ 재충전 필요" : "정상");
  }

  delay(1000);
}
