# 불침번 SW — 개발 현황 (v7)

상시 소방설비 점검 시스템. 법정 점검(연 1~2회 "점")과 설비 열화(연속 "선") 사이의
갭을 상시 센서로 메워 소방시설관리사 업무를 보조한다. **관리사를 대체하지 않는다.**

**AI는 정확히 2곳에만 적용한다: ① 수계 주펌프 파형 분류, ② 자탐2(광전식) 비화재보 판별.**
그 외 설비(자탐1/가스계/유도등/소화기)는 전부 통계·규칙 기반이다.

## 구조

v7에서 `server/`를 receiver(수신) / storage(DB) / judge(판정) / dispatch(출력) 4개
서브패키지로 재편했다 — 파일이 커지면서 "이 파일이 통신 계층인지, 판정 로직인지,
출력 채널인지"가 이름만 봐도 구분되지 않는 문제가 있었다. 로직 자체는 옮기면서
바꾸지 않았고(버그 3건만 예외), 판정 모듈 임포트 경로만 이 구조에 맞게 갱신했다.

```
bulchimbeon_sw/
├── server/
│   ├── main.py                     # 서버 실행 진입점 — python server/main.py
│   ├── config.py                   # 경로 상수 + 가스/z-score 등 판정 임계값(cross-module)
│   ├── feature_extraction.py       # 파형 -> RMS/peak/duty_cycle 계산
│   ├── pump_performance_test.py    # 수계 — 성능시험(1차, 규칙기반) + 압력기반 밸브상태 추정
│   ├── receiver/
│   │   ├── udp_listener.py         # UDP 소켓 수신 루프 (통신 계층)
│   │   └── packet_parser.py        # 패킷 해석 + heartbeat 갱신 + 판정 모듈 호출
│   ├── storage/
│   │   ├── db.py                   # DB 생성 스크립트 — python server/storage/db.py
│   │   └── schema.sql              # 5개 설비 + heartbeat 테이블
│   ├── judge/
│   │   ├── rules.py                # 소화기 — 이탈감지 2단계 판정 (규칙 기반)
│   │   ├── regression.py           # 자탐 z-score + 가스계/유도등 선형회귀 (통계)
│   │   ├── classify.py             # 수계 파형분류 + 자탐2 비화재보 판별 AI 실시간 추론
│   │   └── ai_models/              # train_*.py가 저장하는 .joblib 위치
│   └── dispatch/
│       └── lcd_buzzer_output.py    # 출력채널 공통 진입점 — 부저 + I2C LCD
├── simulate/
│   ├── dummy_generator.py           # 실물 없이 5개 설비 신호 시뮬레이션
│   └── verify_fire_alarm_zscore.py  # 자탐 z-score 판정 검증 스크립트
├── ml/
│   ├── train_pump_classifier.py      # 수계 주펌프 5클래스 분류 모델 학습 (AI 지점 ①)
│   ├── train_nuisance_classifier.py  # 자탐2 비화재보 판별 모델 학습 (AI 지점 ②)
│   └── evaluate_predictions.py       # 가스계/유도등 회귀 예측 정확도(MAE) 검증
├── dashboard/
│   ├── app.py                # Flask 대시보드 (+ 수계 밸브 수동 트리거 라우트)
│   └── templates/index.html  # 5개 설비 카드 + heartbeat + 자탐2 AI 판정 + 성능시험 대조
└── esp32/
    ├── pump_node/                     # 수계 (충압+주펌프+성능시험, GPIO34/35/25/26/32 — 릴레이 2채널)
    ├── fire_alarm_differential_node/  # 자탐1: 차동식구역 (TS0202 온도 dT/dt, 규칙 기반)
    ├── fire_alarm_photoelectric_node/ # 자탐2: 광전식구역 (MQ-2+DHT22, 비화재보 판별 AI)
    ├── gas_node/                      # 가스계 (HX711)
    ├── extinguisher_leafnode/         # 소화기 리프노드 (ESP32-C3, MPU6500, ESP-NOW)
    ├── extinguisher_gateway/          # 소화기 게이트웨이 (ESP-NOW -> WiFi/UDP 중계)
    └── evac_light_node/               # 유도등 (CD74HC4067 멀티플렉서로 4개 통합)
```

`db/` 폴더는 v7에서 없어졌다 — DB 파일/스키마는 이제 `server/storage/`에 있다.

## 실행 순서

```bash
pip install -r requirements.txt

# 1) DB 생성
python server/storage/db.py

# 2) 수신 서버 실행 (터미널 A, 실제로는 라즈베리파이에서 상시 구동)
python server/main.py

# 3) 더미 데이터 전송 (터미널 B)
python simulate/dummy_generator.py --all

# 4) AI 모델 학습 (성능시험/비화재보 라벨 우선) -> server/judge/ai_models/에 저장됨
python ml/train_pump_classifier.py
python ml/train_nuisance_classifier.py

# 5) 대시보드 실행 (터미널 C) -> http://localhost:5000
python dashboard/app.py

# 6) 자탐 z-score 판정 검증
python simulate/verify_fire_alarm_zscore.py

# 7) 가스계/유도등 회귀 예측 정확도 검증 (개발완료보고서 근거자료)
python ml/evaluate_predictions.py
```

## 지금 상태

- [x] ESP32 -> UDP -> 라즈베리파이 수신 -> SQLite 적재 전체 경로 동작 확인
- [x] 5개 설비 전부 더미 데이터로 end-to-end 검증 완료 (재편된 폴더 구조 기준으로 재검증 완료)
- [x] 자탐 — **2구역이 센서 구성부터 완전히 분리됨(v4)**: 자탐1(차동식, TS0202 온도
      dT/dt, 규칙 기반)과 자탐2(광전식, MQ-2+DHT22, **비화재보 판별 AI**)가 서로 다른
      ESP32 펌웨어. 루프저항 z-score(지속성 조건, window=8)는 두 구역 공통
- [x] 자탐2 비화재보 판별 — temp_rise_rate/mq2_raw/humidity_pct 3개 feature로
      fire/cooking/normal 분류(RandomForest). "온도상승률 vs 습도상승" 조합이 핵심
      구분점 — 더미데이터로는 완전분리 확인, **실측 검증은 부품 수령 후 최우선 과제**
- [x] 가스계 — gas_type(co2/halon/inert)별 임계 손실률 선형회귀 예측. **v7 버그수정**:
      총중량이 아니라 약제중량(총중량-공병중량) 기준으로 손실률을 계산하도록 변경
      (`server/judge/regression.py`의 `_agent_loss_pct`) — 공병중량은 `server/config.py`의
      `DEFAULT_EMPTY_CONTAINER_WEIGHT_G`에서 가져오며, **반드시 설치 시 1회 실측 캘리브레이션 필요**
      (미실측 시 0.0으로 예전과 동일하게 과소평가됨)
- [x] 유도등 — `predicted_days_to_replace`(달력추세, 일)/`estimated_discharge_min`
      (데모모드 실측방전, 분) 필드 분리
- [x] 소화기 — 가속도+게이트웨이 연결상태 2단계 이탈감지
- [x] 수계 — **성능시험(1차, 규칙기반) + CT클램프 파형분류(2차, AI) 결합**, 그리고
      **밸브가 니들밸브(수동 조작)로 정정됨(v4)**: ESP32는 압력값만 계속 흘려보내고,
      서버가 압력 패턴(연속 3샘플 지속)으로 체절/부하 상태를 스스로 추정한다
      (`server/pump_performance_test.py`의 `infer_valve_state_from_pressure`).
      대시보드에 신뢰도가 낮을 때 쓸 수동 트리거 버튼도 대안으로 마련해둠
- [x] 수계 라벨 **v7에서 5클래스로 재정의**: `normal_operation`(정상운전)/
      `stall_operation`(체절운전)/`dry_run`(공회전)/`flow_reduced`(유량저하, 자리만 예약)/
      `startup_failure`(기동실패, 자리만 예약) — 라벨명이 "밸브 상태(물리적으로 확인된
      실험 조건)"만 가리키도록 바꿔, 펌프 종류에 따라 체절 시 전류가 낮게 나올 수도
      있다는 불확실성과 무관하게 라벨 체계가 안 흔들리게 했다. 지금 재현 가능한 실험
      조건(체절/부하/공회전)으로는 3클래스만 실제로 채워진다 — 나머지 둘은 추가 실험
      조건이 생기면 채울 자리
- [x] node_heartbeat로 노드 생사 확인 가능 (90초 미수신 시 offline), boot_id로 재부팅과
      시퀀스 역행(패킷 유실)을 구분
- [x] Flask 대시보드 — 5개 설비 카드 + heartbeat + 자탐2 AI 판정 + 수계 성능시험 대조
- [x] ESP32 펌웨어 7종 전부 작성 (확정 핀 배치 반영, 미검증·실물 미연결)
- [x] 가스계/유도등 예측 정확도 검증 스크립트 (MAE, 시드 고정 재현 가능, v5에서 유도등
      단위 불일치 버그 수정 — 분 단위로 잘못 내던 걸 predicted_days_to_replace와 맞는
      일 단위로 정정)
- [x] **출력채널은 시각(대시보드) + 청각·현장(부저+LCD) 2종(v6/v7)** —
      `server/dispatch/lcd_buzzer_output.py`의 `trigger_alert()` 하나로 통일, 자탐/수계/
      가스계/소화기/유도등 5개 판정 모듈이 전부 이 함수만 거친다. 같은 (설비,구역)에
      대한 반복 알림은 쿨다운(기본 5분)으로 억제. caution은 부저 없이 LCD만, alarm급은
      부저+LCD 전부. **이메일/텔레그램/PWA는 이번 범위에서 채택하지 않는다** — 원격
      알림(앱)은 별도 팀원이 진행하는 네이티브 앱 쪽 책임이며, 그 팀원의 API가 준비되면
      서버 쪽 연동을 별도로 진행할 예정이다. LCD/부저 실물이 없는 개발 환경에서는
      자동으로 콘솔 로그로 대체 — 검증 완료
- [x] heartbeat 시퀀스 역행 오탐 버그 수정(v5) — boot_id가 같을 때만 역행으로 판단하도록
      조건 보강, 더미 생성기가 같은 node_id로 여러 스트림을 순차 전송해도 오탐 없음
      재확인(v7 재검토 시에도 정상 동작 확인됨)
- [x] **폴더 구조 재편 완료(v7)** — `server/`를 `receiver/`(통신)/`storage/`(DB)/
      `judge/`(판정)/`dispatch/`(출력)로 나눔. 로직은 옮기면서 바꾸지 않았고, 재편 후
      전체 파이프라인(DB 생성 -> 서버 기동 -> 더미데이터 전송 -> 대시보드 렌더링)을
      다시 end-to-end로 검증해 import 경로 오류가 없음을 확인했다

## 다음 단계 (실물 연동 및 팀 논의 대기 항목)

1. **부품 수령 즉시 최우선 검증** — 자탐2 비화재보 판별의 3상태(fire/cooking/normal)
   재현 시나리오(열풍기/가습기/발연원, `ml/train_nuisance_classifier.py` 상단 참고)가
   실측에서도 실제로 분리되는지 확인. 신호가 겹치면 규칙 기반(습도 임계값 등)으로
   축소하는 대안을 준비해둘 것
2. **수계 실측 데이터 수집** — `pump_node.ino`의 성능시험(펌프 기동 + 발표자 수동
   밸브조작)을 실제 배관에 연결해 `RATED_PRESSURE_KPA`(server/pump_performance_test.py)를
   설치 현장 실측값으로 교체. 압력 자동추정(`VALVE_AUTO_DETECT_*` 상수들)이 실측에서도
   안정적인지 확인하고, 불안정하면 대시보드 수동 트리거 버튼으로 전환
3. **가스계 공병중량 실측 캘리브레이션** — `server/config.py`의
   `DEFAULT_EMPTY_CONTAINER_WEIGHT_G`를 설치할 실제 용기 무게로 채워야 손실률 계산이
   정확해진다(현재 기본값 0.0 = 미보정 상태)
4. **팀 확인/결정 대기 중인 항목** (결정되는 대로 아래 상수/로직만 수정하면 됨):
   - CO2 중량손실 기준: 1권 10%(예외 有) vs 2권 5% -> `server/config.py`의
     `GAS_LOSS_THRESHOLD_PCT["co2"]`
   - 소화기 이탈(missing) 확정 후 수동 복구 기능 — 현재 미구현(의도된 설계, 보안경보 성격)
   - 수계 성능시험 실행 주기 — 현재 `ENVIRONMENT="testbed"`(6시간 간격)로 설정됨.
     **실배포 전 반드시 `"deployment"`(180일, 법정 종합점검 주기)로 전환**할 것
     (`server/pump_performance_test.py` + `pump_node.ino`의 `PUMP_TEST_ENVIRONMENT_TESTBED` 매크로)
   - 체절 상태 전류가 실제로 "체절운전(stall_operation)"이라는 라벨명에 맞게 높게
     나오는지 펌프(DC모터) 특성상 검증 필요 — 반대로 나와도 라벨명 자체(§"수계 라벨" 참고)는
     안 바뀌므로 로직 자체는 무너지지 않음
   - 각종 임계값(가속도 15.0 m/s², 확인대기 60초, ADS1115 션트저항, 정격토출압력 700kPa,
     압력 자동추정 임계 102%/20% 등) 실측 후 재조정 필요 — 전부 파일 상단 상수로 분리해뒀음
5. ESP32 실물 배선 후 `boot_id`/`seq`/NTP 동기화, **압력센서 저항분압 배선**(0.5~4.5V
   출력을 그대로 물리면 ESP32 손상 위험, `pump_node.ino` 배선 메모 참고)이 실제로 잘
   되어 있는지 확인
6. **부저/LCD 실물 배선 및 GPIO 핀 확정** — `server/dispatch/lcd_buzzer_output.py`의
   `BUZZER_PIN`(BCM 17), `LCD_I2C_ADDRESS`(기본 0x27, PCF8574 백팩 흔한 기본값)는
   라즈베리파이 배선 여건에 맞춰 재조정 가능. I2C 주소는 실측 후 `i2cdetect -y 1`로
   확인해 교체할 것. 지금은 "경보 시 텍스트로 잠깐 표시 후 평상시 화면 복귀" 정도만
   구현했고, "평소 5개 설비 상태를 순환 표시"하는 기능은 아직 없음 — 필요하면 추후
   별도 작업으로 추가 가능(현재는 DB를 상시 폴링하는 백그라운드 스레드가 없는 구조)
7. **GitHub 동기화 필요** — 현재 GitHub 저장소에는 ESP32 `.ino` 펌웨어 7종만 올라가
   있고, 이 리포의 `server/`·`ml/`·`dashboard/`·`simulate/` 파이썬 코드는 아직 올라가
   있지 않다. v7 검토 과정에서 팀원이 "heartbeat 버그가 안 고쳐졌다"고 지적한 것도
   실제로는 이미 로컬에서 v5 때 수정된 상태를 GitHub의 오래된 스냅샷과 비교해서 생긴
   착오였다 — 다음 커밋에서 반드시 전체 소스를 올려 이런 혼선을 없앨 것

## 주의사항 (버그 아님 — 의도된 설계)

- ESP32 아날로그 **입력**은 반드시 **ADC1 채널(GPIO32~39)**만 사용 (WiFi 켜지면 ADC2 잠김).
  단, 디지털 출력(릴레이/멀티플렉서 채널선택 등)은 GPIO25/26/27/14를 써도 무방 —
  ADC2 제약은 아날로그 입력에만 해당한다.
- 자탐 노드는 내장 ADC 노이즈가 커서 **ADS1115(16bit I2C ADC)** 사용, 수계 CT는
  반대로 샘플링 속도가 중요해 **내장 ADC**를 그대로 사용 — 이 구분은 의도된 것.
- **자탐 2구역은 센서 구성이 완전히 다르다(v4).** 자탐1(차동식)은 TS0202 온도센서 +
  루프저항만 감시하고, 자탐2(광전식)만 MQ-2+DHT22로 비화재보 판별 AI를 돌린다.
  둘 다 열원/발연원은 ESP32에 연결되지 않은 완전 수동 조작(열풍기/가습기)이다.
- 자탐 감지기회로 전로저항은 **50Ω 이하가 법정 정상 기준**이며, `dummy_generator.py`의
  베이스라인은 20Ω 근처로 맞춰뒀다.
- 자탐 z-score의 alarm/caution 임계값(1.8/1.6)이 통상적인 3.0/2.0보다 낮은 이유는
  매직넘버가 아니라 수학적으로 유도된 값이다 — 이동평균 기반 z-score는 순수 선형
  드리프트에 대해 기울기가 아무리 커도 window 크기로 정해지는 점근값 이상 못 올라간다.
  자세한 유도 과정은 `server/judge/regression.py` docstring 참고.
- 유도등 조도센서 로직은 **2선식(평상시 상시점등)** 전제 — 3선식(평상시 소등이 정상)
  현장에 배포하면 오탐이 나므로 배포 전 반드시 확인할 것.
- 유도등의 `predicted_days_to_replace`(느린 전압추세, 일 단위)와 `estimated_discharge_min`
  (데모모드 실측 방전시험, 분 단위)은 **서로 다른 질문에 답하는 값이라 절대 비교하지
  말 것** — 법정 20분/60분 기준은 반드시 후자와 비교해야 한다.
- 소화기는 v2에서 압력 게이지 방식을 완전히 폐기했다 — 게이지 지름 30mm라 센서
  부착이 물리적으로 어렵고 카메라 방식은 대량배포 원가가 안 맞기 때문. 가속도+게이트웨이
  연결상태 조합으로 대체.
- **수계 밸브는 니들밸브(수동 조작)다(v4 정정) — 솔레노이드 자동화가 아니다.**
  발표자가 시연 중 직접 손으로 잠그고 연다. ESP32/서버는 압력값 패턴으로 체절/부하
  상태를 스스로 추정하며, 릴레이는 충압/주펌프 2채널로 충분하다(밸브용 채널 없음).
- 수계 성능시험(1차)과 CT클램프 AI 분류(2차)는 **순차 대체관계가 아니다** — AI는 상시로
  돌고, 성능시험은 주기적으로 짧게만 실행되며, 둘은 겹치는 순간에만 대조된다.
  "성능시험 실패시 AI로 넘어간다"는 식의 로직/주석은 넣지 않았다.
- `dummy_generator.py`로 대량 전송 시 `send()`의 `time.sleep(0.01)`을 지우지 말 것
  (지우면 UDP 버퍼 오버플로우로 패킷 유실 — 실물 노드는 원래 느리게 보내므로 이 문제 없음)
- **AI는 정확히 2곳에만 적용된다: 수계 주펌프 파형 분류 + 자탐2 비화재보 판별.**
  그 외(자탐1/가스계/소화기/유도등)는 전부 통계(z-score/선형회귀)·규칙 기반이며,
  이 설계 원칙은 변경하지 않는다.
- **가스계 손실률은 반드시 약제중량(총중량-공병중량) 기준으로 계산해야 한다(v7).**
  미니어처처럼 용기 자체 무게 비중이 크면 총중량 기준 계산은 손실률을 과소평가한다.
  `empty_container_weight_g`는 패킷이 아니라 `server/config.py`의 캘리브레이션 상수에서
  가져온다 — ESP32가 매번 알려줄 수 있는 값이 아니라 설치 시 1회 실측하는 값이기 때문.
- **출력채널은 반드시 `server/dispatch/lcd_buzzer_output.py`의 `trigger_alert()` 하나만
  거친다.** 판정 모듈(judge/regression·judge/rules·judge/classify·pump_performance_test)이
  부저·LCD 제어 코드를 각자 따로 들고 있으면 안 된다 — 반복 알림 억제(쿨다운)도 여기서
  중앙집중적으로 처리하므로, 호출하는 쪽은 "지금 알림 보낼 만한 상황인지"만 판단하면 된다.
- GPIO(RPi.GPIO)·LCD(RPLCD) 라이브러리가 없는 환경(개발 PC 등 라즈베리파이가 아닌
  곳)에서는 `lcd_buzzer_output.py`가 자동으로 콘솔 로그 대체 경로를 타서 서버가 죽지
  않는다 — 실제 라즈베리파이에 배포하면 자동으로 GPIO/I2C 경로가 활성화된다.
