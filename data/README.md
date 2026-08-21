# data/ — 실물 실험 원시 데이터

각 엣지 노드에서 실제 하드웨어로 측정한 원시 데이터와 요약·분석 결과를 엣지 → 클래스 단위 폴더로 정리했습니다.

## 광전식 엣지/ — 실험 완료 (최종본)

자탐2(광전식) 노드 비화재보(오작동) 판별 실험이 모두 완료되어, 아래 4개 클래스 폴더와 최상위 통합 파일이 최종 데이터입니다.

- **photoelectric_all_classes_combined.csv** — normal/smoking(연초+전자담배)/incense/heat/fire **5개 클래스, 총 64세션**을 상승률(%) 기준 대표값 1행/세션으로 통합한 메인 학습 파일. 컬럼: `session_id, label, subtype, condition, n_samples, mq2_rise_pct, humidity_change_pct, temp_rise_c, note`
- **자탐2_광전식_최종분석보고서.md** — 5개 클래스 최종 통합 분석 리포트. 핵심 결론: **습도 변화 방향(상승/하강)이 1차 분리 기준** — 연기·흡연 계열(normal/smoking)은 습도 상승, 열이 개입되면(heat/fire) 습도 하강. 그 안에서 MQ2 반응 유무로 2차 분리(normal↔smoking, heat↔fire). incense는 MQ2만 단독으로 크게 뛰고 온도·습도는 거의 안 움직이는 예외 케이스로 분리 필요. 이상치(tobacco_09, incense_07)는 원인 미확인 상태로 포함 여부 결정 필요.
- ⚠️ **쿠킹(수증기) 클래스는 이 5-클래스 통합 분석에 포함되지 않음** — 조리 수증기 오작동 여부는 별도의 습도 임계값 기반 판별 리포트(아래 `쿠킹(수증기_소중대) 클래스/` 참고)로 독립적으로 다룸.

### 노말(정상상태) 클래스/

자탐2(광전식) 노드를 평상시(화재·수증기 등 이벤트 없는) 상태로 두고 측정한 기준(baseline) 데이터입니다. AI 비화재보 판별 모델의 "정상" 라벨 학습·검증에 사용됩니다.

- **normal_raw_timeseries.csv** — 세션별 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm(루프저항), mq2_raw(가스센서 원시값), temp_c(온도), humidity_pct(습도)`
- **normal_session_summary.csv** — 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise/rise_rate_per_sample, temp_avg_c/delta_c, humidity_avg_pct/delta_pct, loop_r_avg_ohm/delta_ohm`
- **normal_plateau_reference.csv** — 각 세션 후반 안정 구간(plateau)의 평균/최소/최대값. 컬럼: `session_id, label, plateau_mq2_avg/min/max, plateau_humidity_avg, plateau_temp_avg, n_tail_samples`

### 쿠킹(수증기_소중대) 클래스/

조리 시 발생하는 수증기를 화재 연기로 오인하지 않는지 검증하기 위해, 수증기량을 소(小)/중(中)/대(大) 3단계로 나눠 각 10회차씩(총 30개) 측정한 데이터입니다.

- **vapor_raw_data_소.csv / _중.csv / _대.csv** — 세기별 원시 시계열. 컬럼: `수증기량_구분, 회차, sample_idx, loop_ohm, mq2raw, temp_c, humidity_pct` (단, `_대.csv`는 로그 형식이 달라 타임스탬프가 포함돼 있으며 파싱 시 제외하고 동일 4항목만 사용)
- **vapor_summary_소.csv / _중.csv / _대.csv** — 회차별 요약 통계. 컬럼: `수증기량_구분, trial, n_points, humidity_baseline/peak/rise/peak_idx, mq2_baseline/peak/min/change_from_base_pct, loop_ohm_min/max/range/base, temp_min/max`
- **수증기_소중대_통합_분석_리포트.md** — 위 raw/summary 데이터를 바탕으로 한 최종 분석 리포트. 소/중/대 3구간 비교, 이상치(워밍업 아티팩트) 식별, 습도 상승폭의 단조 증가(dose-response) 경향과 소/중/대 판별 경계값 후보를 정리함.

### 스모킹(연초_전자담배) 클래스/

흡연(연초 야외 10세션, 전자담배 실내 거리별 10세션)이 화재 연기와 어떻게 다른 신호 패턴을 보이는지 검증한 데이터입니다.

- **tobacco_raw_timeseries.csv** — 연초 세션 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct`
- **tobacco_session_summary.csv** — 연초 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise/rise_rate_per_sample, humidity_start/peak/delta/avg, temp_avg_c/delta_c, loop_r_avg_ohm`
- **tobacco_delta_reference.csv** — 연초 세션별 시작 대비 상승률 기준값. 컬럼: `session_id, label, n_samples, mq2_rise_pct/abs/rise_rate_per_sample_pct, humidity_rise_pct/abs_pp, temp_delta_c_within_session`
- **ecig_raw_timeseries.csv** — 전자담배 세션 원시 시계열(거리별: 가까이/중간/멀리서). 컬럼: `session_id, label, distance, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct`
- **ecig_session_summary.csv** — 전자담배 세션별 요약 통계. 컬럼: `session_id, distance, n_samples, wifi_drop_count, mq2_start/peak/end/rise_abs/rise_pct, humidity_start/peak/rise_abs_pp/rise_pct, temp_avg_c/delta_c`
- **smoking_class_combined.csv** — 연초+전자담배 20세션 통합 비교표. 컬럼: `session_id, label, subtype, condition, n_samples, mq2_rise_pct/abs, humidity_rise_pct/abs_pp, temp_delta_c_within_session`
- **스모킹_클래스_분석보고서.md** — 연초(야외)·전자담배(실내, 거리별) 통합 분석 리포트. 측정 환경 차이 때문에 절대값 대신 "세션 시작 대비 상승률(%)" 기준으로 통일 비교했으며, 전자담배는 거리에 따른 선형적 용량-반응(가까이 > 중간 > 멀리서) 패턴을 확인함.

### 화재(열원_연기) 클래스/

연기·열원을 분리해서 각각 단독으로, 그리고 함께(화재 모의) 반응을 측정한 3종 하위 실험입니다. 연기만 있을 때(incense)와 열만 있을 때(heat), 둘 다 있을 때(fire=화재모의)의 신호 차이를 구분하는 것이 목적입니다.

- **incense_raw_timeseries.csv / incense_session_summary.csv** — 향(인센스) 단독 연기, 10세션. 컬럼(raw): `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct` / 컬럼(summary): `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise_abs/rise_pct, humidity_start/peak/rise_abs_pp/rise_pct, temp_start_c/end_c/delta_c`. 결과: MQ2 +37.61%(가장 큼)로 뚜렷이 반응하지만 습도·온도는 거의 안 움직임 — "MQ2만 단독으로 크게 뛴다"가 핵심 시그니처.
- **heat_raw_timeseries.csv / heat_session_summary.csv** — 열풍기 단독(연소 없이 열만), 10세션. 컬럼(raw): `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct` / 컬럼(summary): `session_id, n_samples, loop_r_avg_ohm, mq2_start/peak/rise_pct, temp_start/peak/rise_c, hum_start/min/drop_pp/drop_pct`. 결과: MQ2 +2.86%(거의 무반응), 습도 -29.01%(하강), 온도 +10.32°C — 열만 있고 연소 부산물이 없으면 MQ2가 반응하지 않는다는 대조군.
- **fire_raw_timeseries.csv / fire_session_summary.csv** — 향+열원 동시(화재 모의), 원래 10세션에서 1·2회차 중복이 확인돼 병합한 9세션. 컬럼(raw): `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct` / 컬럼(summary): `session_id, n_samples, mq2_start/peak/rise_pct, temp_start/peak/rise_c, hum_start/min/drop_pct`. 결과: MQ2 +23.85%, 습도 -35.94%(전체 클래스 중 최대 하강), 온도 +13.06°C — heat 대비 MQ2 반응과 습도 하강폭이 더 커서 heat와 구분됨.

## 가스계 엣지/

가스저장용기(CO2) 무게를 로드셀(HX711)로 상시 측정해 약제 손실률을 추적하는 실험입니다. 실물 CO2 용기 대신 자동 방향제 분사기(7분 30초 간격 분사)로 무게 손실을 흉내내 약 3.85일간 5분 간격으로 로깅했습니다.

- **gas_node_log.csv** — 원본 로그, 817개 유효 포인트(2026-08-18 16:51 ~ 2026-08-21 12:51, 5분 간격). 컬럼: `datetime, raw(로드셀 원시값), weight_g(환산 중량), loss_pct(초기 대비 손실률)`
- **gas_regression_chart.png** — 회귀검증 차트(원시 데이터, 온도 안정 구간, 예측/실제 임계값 도달 크로싱 포인트, 실측 대조점 포함)
- **가스계_캘리브레이션_결과_정리.md** — 캘리브레이션·검증 최종 정리 리포트. 핵심 내용:
  - 로드셀 신호선 접촉불량 발견·납땜으로 해결(편차 ±162g → ±16g), 최종 캘리브레이션 계수 **87.9**(501g 실측 기준)
  - **온도 드리프트 이슈(중요)**: 에어컨 on/off에 따라 로드셀 판독값이 최대 ±50g 요동 — 배율 오차가 아닌 영점(TARE) 오차라 %손실 계산에서 상쇄되지 않음. 온도 안정 구간만 분리해 분석했으며, 온도보상 로직은 이번 일정상 미구현(향후 과제로 명시)
  - 온도 안정 구간(22.8시간) 앞 70%로 회귀 학습 → 10% 손실 임계값 도달 시점 예측, 실제 도달 시점과 대조한 결과 **예측 오차 약 49분**(R²=0.951)
  - 실측 anchor 기준 3.85일간 손실률 8.58%(환산 2.23%/day), 이 속도 유지 시 10% 도달까지 약 4.48일 소요 예상
  - ⚠️ 공병중량(EMPTY_CONTAINER_WEIGHT_G) 미차감 상태로 측정됨 — 실제 CO2 용기 설치 시 공병중량 실측·반영 필수. 손실률 임계값도 매뉴얼 기준(1권 10% vs 2권 5%) 확정 대기 중(현재는 10% 기준으로 검증)

## 유도등 엣지/

유도등 예비전원(Ni-Cd 단일셀, 1.2V)을 충전시간별(1h/2h/4h/6h/8h)로 완충한 뒤 방전시험을 진행해, 정전 시 법정 유지시간(20분) 충족에 필요한 최소 충전시간과 판정 임계전압을 검증한 데이터입니다.

- **evac_discharge_1h_charged.csv / _2h_ / _4h_ / _6h_ / _8h_charged.csv** — 충전시간별 방전시험 원시 시계열(1초 간격). 컬럼: `session, elapsed_sec, battery_voltage_v, note` (2h·4h는 시험 중 재투입 이력이 있어 `session` 컬럼으로 세션이 구분됨)
- **evac_discharge_summary.csv** — 충전시간별 요약 통계. 컬럼: `charge_duration, n_sessions_in_file, main_session_id, main_session_total_sec, sec_below_300mV/200mV/100mV, voltage_at_300mV/200mV/100mV_cross, voltage_at_1200sec_20min`
- **유도등_방전시험_통합_분석_리포트.md** — 충전시간별 방전 특성 통합 분석 리포트. 6h·8h는 법정기준(20분) 충족, 1h·2h는 미충족, 4h는 세션이 짧게 끊겨 재시험 권장으로 정리됨.
