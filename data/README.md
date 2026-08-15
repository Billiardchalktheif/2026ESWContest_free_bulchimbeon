# data/ — 실물 실험 원시 데이터

각 엣지 노드에서 실제 하드웨어로 측정한 원시 데이터와 요약·분석 결과를 엣지 → 클래스 단위 폴더로 정리했습니다.

## 광전식 엣지/노말(정상상태) 클래스/

자탐2(광전식) 노드를 평상시(화재·수증기 등 이벤트 없는) 상태로 두고 측정한 기준(baseline) 데이터입니다. AI 비화재보 판별 모델의 "정상" 라벨 학습·검증에 사용됩니다.

- **normal_raw_timeseries.csv** — 세션별 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm(루프저항), mq2_raw(가스센서 원시값), temp_c(온도), humidity_pct(습도)`
- **normal_session_summary.csv** — 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise/rise_rate_per_sample, temp_avg_c/delta_c, humidity_avg_pct/delta_pct, loop_r_avg_ohm/delta_ohm`
- **normal_plateau_reference.csv** — 각 세션 후반 안정 구간(plateau)의 평균/최소/최대값. 컬럼: `session_id, label, plateau_mq2_avg/min/max, plateau_humidity_avg, plateau_temp_avg, n_tail_samples`

## 광전식 엣지/쿠킹(수증기_소중대) 클래스/

조리 시 발생하는 수증기를 화재 연기로 오인하지 않는지 검증하기 위해, 수증기량을 소(小)/중(中)/대(大) 3단계로 나눠 각 10회차씩(총 30개) 측정한 데이터입니다.

- **vapor_raw_data_소.csv / _중.csv / _대.csv** — 세기별 원시 시계열. 컬럼: `수증기량_구분, 회차, sample_idx, loop_ohm, mq2raw, temp_c, humidity_pct` (단, `_대.csv`는 로그 형식이 달라 타임스탬프가 포함돼 있으며 파싱 시 제외하고 동일 4항목만 사용)
- **vapor_summary_소.csv / _중.csv / _대.csv** — 회차별 요약 통계. 컬럼: `수증기량_구분, trial, n_points, humidity_baseline/peak/rise/peak_idx, mq2_baseline/peak/min/change_from_base_pct, loop_ohm_min/max/range/base, temp_min/max`
- **수증기_소중대_통합_분석_리포트.md** — 위 raw/summary 데이터를 바탕으로 한 최종 분석 리포트. 소/중/대 3구간 비교, 이상치(워밍업 아티팩트) 식별, 습도 상승폭의 단조 증가(dose-response) 경향과 소/중/대 판별 경계값 후보를 정리함.

## 광전식 엣지/스모킹(연초_전자담배) 클래스/

흡연(연초 야외 10세션, 전자담배 실내 거리별 10세션)이 화재 연기와 어떻게 다른 신호 패턴을 보이는지 검증한 데이터입니다.

- **tobacco_raw_timeseries.csv** — 연초 세션 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct`
- **tobacco_session_summary.csv** — 연초 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise/rise_rate_per_sample, humidity_start/peak/delta/avg, temp_avg_c/delta_c, loop_r_avg_ohm`
- **tobacco_delta_reference.csv** — 연초 세션별 시작 대비 상승률 기준값. 컬럼: `session_id, label, n_samples, mq2_rise_pct/abs/rise_rate_per_sample_pct, humidity_rise_pct/abs_pp, temp_delta_c_within_session`
- **ecig_raw_timeseries.csv** — 전자담배 세션 원시 시계열(거리별: 가까이/중간/멀리서). 컬럼: `session_id, label, distance, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct`
- **ecig_session_summary.csv** — 전자담배 세션별 요약 통계. 컬럼: `session_id, distance, n_samples, wifi_drop_count, mq2_start/peak/end/rise_abs/rise_pct, humidity_start/peak/rise_abs_pp/rise_pct, temp_avg_c/delta_c`
- **smoking_class_combined.csv** — 연초+전자담배 20세션 통합 비교표. 컬럼: `session_id, label, subtype, condition, n_samples, mq2_rise_pct/abs, humidity_rise_pct/abs_pp, temp_delta_c_within_session`
- **스모킹_클래스_분석보고서.md** — 연초(야외)·전자담배(실내, 거리별) 통합 분석 리포트. 측정 환경 차이 때문에 절대값 대신 "세션 시작 대비 상승률(%)" 기준으로 통일 비교했으며, 전자담배는 거리에 따른 선형적 용량-반응(가까이 > 중간 > 멀리서) 패턴을 확인함.

## 광전식 엣지/화재(열원_연기) 클래스/

향(인센스) 연기를 이용해 실제 열원·연기에 대한 반응을 측정한 데이터입니다.

- **incense_raw_timeseries.csv** — 세션별 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm, mq2_raw, temp_c, humidity_pct`
- **incense_session_summary.csv** — 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise_abs/rise_pct, humidity_start/peak/rise_abs_pp/rise_pct, temp_start_c/end_c/delta_c`

## 유도등 엣지/

유도등 예비전원(Ni-Cd 단일셀, 1.2V)을 충전시간별(1h/2h/4h/6h/8h)로 완충한 뒤 방전시험을 진행해, 정전 시 법정 유지시간(20분) 충족에 필요한 최소 충전시간과 판정 임계전압을 검증한 데이터입니다.

- **evac_discharge_1h_charged.csv / _2h_ / _4h_ / _6h_ / _8h_charged.csv** — 충전시간별 방전시험 원시 시계열(1초 간격). 컬럼: `session, elapsed_sec, battery_voltage_v, note` (2h·4h는 시험 중 재투입 이력이 있어 `session` 컬럼으로 세션이 구분됨)
- **evac_discharge_summary.csv** — 충전시간별 요약 통계. 컬럼: `charge_duration, n_sessions_in_file, main_session_id, main_session_total_sec, sec_below_300mV/200mV/100mV, voltage_at_300mV/200mV/100mV_cross, voltage_at_1200sec_20min`
- **유도등_방전시험_통합_분석_리포트.md** — 충전시간별 방전 특성 통합 분석 리포트. 6h·8h는 법정기준(20분) 충족, 1h·2h는 미충족, 4h는 세션이 짧게 끊겨 재시험 권장으로 정리됨.
