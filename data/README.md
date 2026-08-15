# data/ — 실물 실험 원시 데이터

각 엣지 노드에서 실제 하드웨어로 측정한 원시 데이터와 요약·분석 결과를 엣지 → 클래스 단위 폴더로 정리했습니다.

## 광전식 엣지/노말(정상상태) 클래스/

자탐2(광전식) 노드를 평상시(화재·수증기 등 이벤트 없는) 상태로 두고 측정한 기준(baseline) 데이터입니다. AI 비화재보 판별 모델의 "정상" 라벨 학습·검증에 사용됩니다.

- **normal_raw_timeseries.csv** — 세션별 원시 시계열. 컬럼: `session_id, label, idx, loop_r_ohm(루프저항), mq2_raw(가스센서 원시값), temp_c(온도), humidity_pct(습도)`
- **normal_session_summary.csv** — 세션별 요약 통계. 컬럼: `session_id, n_samples, wifi_drop_count, mq2_start/peak/end/rise/rise_rate_per_sample, temp_avg_c/delta_c, humidity_avg_pct/delta_pct, loop_r_avg_ohm/delta_ohm`
- **normal_plateau_reference.csv** — 각 세션 후반 안정 구간(plateau)의 평균/최소/최대값. 컬럼: `session_id, label, plateau_mq2_avg/min/max, plateau_humidity_avg, plateau_temp_avg, n_tail_samples`

## 광전식 엣지/쿠킹(수증기) 클래스/

조리 시 발생하는 수증기를 화재 연기로 오인하지 않는지 검증하기 위해, 수증기량을 소(小)/중(中)/대(大) 3단계로 나눠 각 10회차씩(총 30개) 측정한 데이터입니다.

- **vapor_raw_data_소.csv / _중.csv / _대.csv** — 세기별 원시 시계열. 컬럼: `수증기량_구분, 회차, sample_idx, loop_ohm, mq2raw, temp_c, humidity_pct` (단, `_대.csv`는 로그 형식이 달라 타임스탬프가 포함돼 있으며 파싱 시 제외하고 동일 4항목만 사용)
- **vapor_summary_소.csv / _중.csv / _대.csv** — 회차별 요약 통계. 컬럼: `수증기량_구분, trial, n_points, humidity_baseline/peak/rise/peak_idx, mq2_baseline/peak/min/change_from_base_pct, loop_ohm_min/max/range/base, temp_min/max`
- **수증기_소중대_통합_분석_리포트.md** — 위 raw/summary 데이터를 바탕으로 한 최종 분석 리포트. 소/중/대 3구간 비교, 이상치(워밍업 아티팩트) 식별, 습도 상승폭의 단조 증가(dose-response) 경향과 소/중/대 판별 경계값 후보를 정리함.

## 유도등 엣지/

- **유도등 방전 실험(충전 1시간).txt** — 유도등 배터리를 1시간 충전 후 방전시험을 진행하며 3초 간격으로 기록한 전압(V/mV) 로그. 평상시 충전 구간과 방전시험 시작(`s` 입력) 이후 경과시간별 전압 추이가 순서대로 담겨 있음.
- **유도등 방전 실험(충전 2시간).txt** — 유도등 배터리를 2시간 충전 후 방전시험을 진행하며 3초 간격으로 기록한 전압(V/mV) 로그. 평상시 충전 구간과 방전시험 시작(`s` 입력) 이후 경과시간별 전압 추이가 순서대로 담겨 있음.
- **유도등 방전 실험(충전 4시간).txt** — 유도등 배터리를 4시간 충전 후 방전시험을 진행하며 3초 간격으로 기록한 전압(V/mV) 로그. 평상시 충전 구간과 방전시험 시작(`s` 입력) 이후 경과시간별 전압 추이가 순서대로 담겨 있음.
- **유도등 방전 실험(충전 6시간).txt** — 유도등 배터리를 6시간 충전 후 방전시험을 진행하며 3초 간격으로 기록한 전압(V/mV) 로그. 평상시 충전 구간과 방전시험 시작(`s` 입력) 이후 경과시간별 전압 추이가 순서대로 담겨 있음.
- **유도등 방전 실험(충전 8시간).txt** — 유도등 배터리를 8시간 충전 후 방전시험을 진행하며 3초 간격으로 기록한 전압(V/mV) 로그. 평상시 충전 구간과 방전시험 시작(`s` 입력) 이후 경과시간별 전압 추이가 순서대로 담겨 있음.
