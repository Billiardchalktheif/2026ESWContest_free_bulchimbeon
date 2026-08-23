-- 불침번 시스템 DB 스키마
-- 설비별 물리량이 전부 다르므로 테이블도 설비별로 분리한다.
-- 공통 컬럼: id, ts(unix timestamp, 초), node_id, zone(구역명)

-- 자탐 — v4에서 2구역이 완전히 다른 센서 구성으로 확정됨(§3):
--   zone_type='differential'(차동식): TS0202 온도센서로 온도상승률(dT/dt) 측정, 화재 재현은
--     열풍기(수동 조작)로 함. 판정은 여전히 규칙 기반(임계값).
--   zone_type='photoelectric'(광전식/연기): MQ-2+DHT22 조합으로 "비화재보(오작동) 판별"을
--     한다 — 이 구역만 AI(RandomForest) 적용 대상이다(§4). 온도상승률/가스농도/습도 세 값을
--     동시에 봐야 화재/조리연기/평상시가 구분되는 패턴 문제라 단순 임계값으로는 부족함.
-- 두 구역 다 루프저항 판정은 공통으로 적용한다 — 법정 절대임계값(50Ω, 1층) +
-- 선형회귀 추세(Ω/day, 2층)의 2층 구조(evaluate_loop_resistance, server/judge/regression.py).
-- 이동평균 z-score 판정은 폐기됐다 — baseline이 서서히 상승하는 저항값을 따라가버려서
-- 진짜 "가속 열화"를 정상 범위로 오판하는 문제가 실측 검증 중 발견됐기 때문.
CREATE TABLE IF NOT EXISTS fire_alarm_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    zone_type TEXT,                      -- 'differential' / 'photoelectric' — 어느 판정 로직을 적용할지
    loop_resistance_ohm REAL NOT NULL,   -- 루프 저항 raw 실측값 (두 구역 공통, 고정저항 오프셋 미차감)
    baseline_ohm REAL,                   -- [레거시] 이동평균 z-score 판정 폐기(2026-08-09) 이전 데이터에만 값이 있음
    z_score REAL,                        -- [레거시] 이동평균 z-score 판정 폐기(2026-08-09) 이전 데이터에만 값이 있음
    loop_trend_slope_ohm_per_day REAL,   -- 2층: 선형회귀 기울기 (Ω/day, 고정저항 보정된 값 기준)
    loop_rttf_days REAL,                 -- 2층: 잔여 고장시간 예측 (일), 기울기<=0이면 NULL
    status TEXT DEFAULT 'normal',        -- normal / caution / alarm (루프저항 2층 판정)
    temp_rise_rate REAL,                 -- 차동식구역: 온도 상승률(°C/초) — 화재 재현 판정 근거
    temp_raw_adc INTEGER,                -- 차동식구역: TS0202 raw ADC (판정은 이 값 기준, temp_c 신뢰 금지)
    temp_v REAL,                         -- 차동식구역: raw 전압값 (디버그/캘리브레이션용)
    boot_elapsed_ms INTEGER,             -- 차동식구역: 부팅 후 경과 ms (NTP 미동기화 판단 보조)
    temp_status TEXT,                    -- 차동식구역: 온도상승률 판정 결과 (evaluate_temp_rise_rate)
                                          -- — status(루프저항 전용) 컬럼과 별개, 절대 혼용하지 말 것.
                                          -- DEFAULT 없음: NULL="아직 판정 안 됨"(펌웨어 미반영 등)과
                                          -- 'normal'="판정했더니 정상"을 구분해야 하므로, INSERT 시점엔
                                          -- 항상 비워두고 evaluate_temp_rise_rate()의 UPDATE만 값을 채운다.
    test_mode INTEGER DEFAULT 0,         -- 점검모드 여부 (노드 물리 택트스위치 값 그대로, 상시 상태 저장 안 함)
    mq2_raw INTEGER,                     -- 광전식구역: MQ-2 가스센서 원시값 (ADS1115 A0)
    temp_c REAL,                         -- 광전식구역: DHT22 온도
    humidity_pct REAL,                   -- 광전식구역: DHT22 습도
    label TEXT,                           -- 광전식구역: 학습용 정답 라벨('fire'/'cooking'/'normal',
                                          -- 더미 생성기/수동 라벨링 절차로 채워짐, 실측 운영시엔 NULL)
    predicted_label TEXT,                -- 광전식구역: 비화재보 판별 AI 추론 결과
                                          -- (water_pump_log와 동일 패턴, server/judge/classify.py)
    confidence REAL                      -- 광전식구역: 위 추론의 신뢰도
);
-- node_id 기준 조회(회귀 이력 조회, 추세값 재사용 lookback, 하트비트 갱신 등)가
-- 패킷마다 반복되는데, 인덱스가 없으면 테이블이 커질수록(장기 운영 시 수십만 행)
-- 매번 전체 스캔이 발생해 사실상 O(n^2)로 느려지는 문제가 결선 전용 재조정
-- 검증(simulate/verify_loop_resistance_judgment.py B시나리오, 12만개+ 행) 중 실측으로
-- 확인됐다(2026-08-16). id를 포함해 "최근 N개" 조회(ORDER BY id DESC LIMIT)도 같이 커버한다.
CREATE INDEX IF NOT EXISTS idx_fire_alarm_log_node_id ON fire_alarm_log(node_id, id);

-- 수계 주펌프: v3에서 "파형분석(AI) + 성능시험" 결합안 도입 (§4).
-- 성능시험(밸브+압력, 규칙기반)이 확정적 판정을 주고, 그 순간 CT클램프로 캡처한 파형에
-- valve_state를 정답 라벨로 자동으로 붙여 AI 학습 데이터의 신뢰도를 높인다.
-- AI(2차)는 성능시험과 무관하게 항상 상시로 돈다 — 성능시험이 도는 짧은 순간에만
-- 서로 대조해서 AI 신뢰도를 검증하는 병행 구조이지, 순차적 대체관계가 아니다.
-- v4: 밸브는 니들밸브(수동 조작)로 정정됨 — ESP32는 밸브를 직접 제어하지 않고, 압력값
-- 패턴을 보고 서버가 valve_state를 "추정"한다(§5). 그래서 valve_state는 ESP32가 보낸
-- 패킷 필드가 아니라 server/pump_performance_test.py가 pressure_kpa로부터 채워 넣는 값이다.
-- v8: 주펌프는 전류센서(INA219) 미장착으로 AI 분류 대상에서 제외됨 — 압력 기반
-- 규칙판정(server/pump_performance_test.py, valve_state/pressure_kpa/rated_pressure_pct)만
-- 적용된다. rms/peak/duty_cycle/label/predicted_label은 pump_type='jockey' 행에만
-- 채워지며, 라벨 도메인은 normal/low_flow/dryrun/start_fail 4클래스다(체절 없음 —
-- 충압펌프 특성상 배제, data/수계 엣지/충압펌프_전류파형_분석_보고서.md 참고).
-- 자세한 학습 로직은 ml/train_pump_classifier.py 참고.
CREATE TABLE IF NOT EXISTS water_pump_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    pump_type TEXT NOT NULL,             -- 'jockey'(충압) / 'main'(주펌프)
    -- 충압펌프용: 기동 이벤트 카운트
    cycle_interval_sec REAL,             -- 이전 기동과의 간격
    -- 주펌프용: 파형 feature (ESP32에서 추출되어 전송됨)
    rms REAL,
    peak REAL,
    duty_cycle REAL,
    label TEXT,                          -- 'normal_operation'/'stall_operation'/'dry_run'/
                                          -- 'flow_reduced'/'startup_failure' (학습용 정답 라벨)
    label_source TEXT,                   -- 'performance_test'(성능시험으로 확정) / 'manual'(수동 라벨)
                                          -- — 학습 데이터 신뢰도 구분용, train_pump_classifier.py가
                                          -- performance_test를 우선한다
    predicted_label TEXT,                -- 모델 추론 결과 (AI, 상시)
    confidence REAL,
    valve_state TEXT,                    -- 2026-08-23부터 4단계 체계: 'shutoff'(체절) /
                                          -- 'rated_100pct'(정격) / 'overload_150pct'(과부하) /
                                          -- 'transition'(구간 사이 이동 중) / 'dryrun'(공회전) /
                                          -- NULL(평상시 모니터링, 성능시험 아님). 옛 'closed'/'open'
                                          -- 값은 과거 데이터에만 남아있고 새 패킷에선 더 안 옴.
    pressure_kpa REAL,                   -- 성능시험 중 압력센서 실측값 (평상시엔 NULL)
    rated_pressure_pct REAL,             -- 정격토출압력 대비 백분율 — 140%(체절)/65%(과부하) 기준과 비교
    flow_lpm REAL                        -- 성능시험 중 유량센서 실측값(L/min, 평상시엔 NULL) —
                                          -- ESP32가 이 값으로 valve_state를 직접 분류해서 같이 보냄
);

-- v7 버그수정: 손실률은 총중량이 아니라 "약제중량"(총중량 - 공병중량) 기준으로 계산해야
-- 한다. 미니어처처럼 용기 자체 무게 비중이 크면, 총중량 기준 계산은 실제 손실률을
-- 과소평가해서 임계치 판정이 늦게 뜬다. empty_container_weight_g는 캘리브레이션 시 1회
-- 실측해서 넣는 값 — 반드시 실측 필요하며, 미입력(기본값 0.0)이면 예전과 동일하게
-- 총중량 기준으로 계산돼 손실률이 과소평가된다는 걸 인지할 것.
CREATE TABLE IF NOT EXISTS gas_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    gas_type TEXT NOT NULL DEFAULT 'co2', -- 'co2' / 'halon' / 'inert' — 종류별 임계값이 달라 분기 필요(§4)
    weight_g REAL NOT NULL,
    initial_weight_g REAL NOT NULL,
    empty_container_weight_g REAL DEFAULT 0.0,  -- 공병(약제 없는 용기)중량 — 캘리브레이션 시 실측 필수
    loss_pct REAL,                        -- 약제중량 기준 손실률(%) — (initial_agent-current_agent)/initial_agent*100
    predicted_days_to_5pct REAL           -- 회귀 외삽 결과 (필드명은 유지하되 실제 임계값은
                                           -- gas_type별로 다름 — server/judge/regression.py 참고)
);

-- 소화기 이탈/도난 감지 (v2에서 압력게이지 방식 폐기 -> 가속도+게이트웨이 연결상태 방식으로 전면 교체)
-- 이유: 게이지 지름 30mm라 센서 부착이 물리적으로 어렵고, 카메라 방식은 대량 배포시 원가가
-- "저비용 상시점검"이라는 프로젝트 취지에 안 맞음. 자세한 내용은 server/judge/rules.py 참고.
CREATE TABLE IF NOT EXISTS extinguisher_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    accel_magnitude REAL,          -- 가속도 벡터 크기
    accel_event INTEGER DEFAULT 0, -- 임계 초과 시 1 (1단계: 이동 이벤트 발생, 아직 경보 아님)
    gateway_id TEXT,               -- 이 패킷을 중계한 게이트웨이 (2단계 오탐필터에 사용)
    status TEXT DEFAULT 'normal'   -- normal / moved(1차플래그) / missing(이탈확정)
);

CREATE TABLE IF NOT EXISTS evac_light_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    node_id TEXT NOT NULL,
    zone TEXT NOT NULL,
    battery_voltage REAL NOT NULL,
    lux INTEGER,                          -- 조도센서(점등확인). 2선식(상시점등) 기준 로직임 —
                                           -- 3선식(평상시 소등이 정상)이면 이 값으로 오탐 발생하니 주의
    -- v3: 아래 두 필드는 서로 다른 질문에 답한다 (§1 버그수정 배경 참고).
    -- 절대 하나로 합치거나 서로 비교하지 말 것 — 단위(일 vs 분)도 다르고 계산 근거도 다르다.
    predicted_days_to_replace REAL,      -- "배터리가 달력 시간 기준 노후화로 언제 교체 필요한가"
                                          -- (평상시 전압 추세의 느린 회귀, 단위: 일)
    estimated_discharge_min REAL,        -- "지금 당장 정전나면 몇 분 버티는가" (데모모드 실측
                                          -- 방전시험 구간에서만 계산됨, 단위: 분, 평상시엔 NULL)
    status TEXT DEFAULT 'normal',        -- normal / warning(estimated_discharge_min이 법정
                                          -- 최소 작동시간 미달로 실측된 경우에만 갱신됨)
    demo_mode INTEGER DEFAULT 0           -- 결선 실시간 데모모드 여부
);

-- 점검모드(Test Mode) 판정 기록 — 자탐1 최초 적용, 추후 자탐2/소화기 확장 대상
-- (인수인계 문서 §7-4). 합격/불합격 자동판정 없음, 사람이 로그를 보고 직접 해석한다.
CREATE TABLE IF NOT EXISTS test_mode_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    device_type TEXT NOT NULL,
    zone TEXT NOT NULL,
    node_id TEXT,
    judged_status TEXT NOT NULL       -- evaluate_*()가 낸 원래 판정 (normal/caution/alarm) — 그대로 기록만
);

CREATE TABLE IF NOT EXISTS node_heartbeat (
    node_id TEXT PRIMARY KEY,
    device_type TEXT NOT NULL,
    last_seen REAL NOT NULL,
    last_seq INTEGER,
    boot_id INTEGER,                      -- 부팅마다 새로 생성되는 난수. 재부팅으로 인한
                                           -- seqNum 리셋을 진짜 시퀀스 역행(패킷 유실)과 구분하는 용도
    status TEXT DEFAULT 'online'          -- online / offline
);
