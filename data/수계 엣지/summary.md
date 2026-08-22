# 충압펌프 전류파형 데이터 채집 — Summary

**작성일**: 2026-08-22
**측정 방식**: INA219 전류센서, 1.5초 간격 200ms 윈도우(100샘플×2ms) RMS/Peak/Duty 계산
**총 유효 샘플**: 1,297개 (4개 클래스, `low_flow`의 12V 기준점 제외)

---

## 1. 클래스별 샘플 수 및 채집 시간

| 클래스 | 샘플 수 | 채집 시간 | 비고 |
|---|---|---|---|
| normal | 235 | 3조건 × 2분 | 밸브 완전개방/중간개방/완전폐쇄 |
| low_flow | 867 | 11개 전압대 × 2분 | 6.5V~11.5V (12V 기준점 제외, 6V는 start_fail로 재분류) |
| dryrun | 39 | 1분 | 흡입+토출 배관 모두 제거(no_piping) 채택 |
| start_fail | 156 | 전원분리 2분 + 6V 2분 | 6V는 원래 low_flow였으나 "완전 정지에 가까운 상태"로 재분류 |

**클래스 불균형 주의**: `dryrun`(39개)이 `low_flow`(867개) 대비 극히 적음. 학습 시 클래스 가중치 조정 또는 오버샘플링 고려 필요.

---

## 2. 클래스별 전류 특징 (RMS / Peak / Duty)

| 클래스 | RMS 평균(mA) | RMS 범위 | Peak 평균(mA) | Duty 평균 |
|---|---|---|---|---|
| dryrun | 81.3 | 72.0~101.9 | 142.3 | 0.67 |
| low_flow | 182.3 | 113.8~245.0 | 278.5 | 0.78 |
| normal | 209.8 | 139.7~261.4 | 350.7 | 0.71 |
| start_fail | 7.3 | 0.9~68.7 | 10.8 | 0.72(변동 큼) |

**파형 형태 특징(Peak/RMS 비율)**: 단순 RMS 크기만으로는 normal과 low_flow가 크게 겹치지만(아래 3번 참고), 이 비율은 클래스별로 다르게 나타남 — dryrun 1.76, normal 1.68, low_flow 1.52, start_fail 1.97(변동 큼). 분류기 입력 feature로 추가 활용 가치 있음.

---

## 3. ⚠️ 클래스 간 중첩(Overlap) — 반드시 인지할 것

- **normal과 low_flow의 RMS 값은 83.7%가 값 범위상 겹칩니다.** (low_flow 867개 중 726개가 normal의 RMS 최소~최대 범위 안에 들어감)
- 이는 RMS 단일 지표만으로는 두 클래스를 완벽히 분리하기 어렵다는 뜻이며, Peak/Duty/Peak-RMS비율까지 종합한 다변량 분류가 필요함을 시사함.
- 특히 **low_flow 9V대(183mA)와 dryrun_suction_only(참고용, 학습 제외)의 RMS(160~180mA)가 겹치는 것**이 확인되어, "흡입측만 제거" 조건은 학습 데이터에서 제외하고 "흡입+토출 모두 제거" 조건(75~90mA, 명확히 분리됨)만 채택함.

---

## 4. 조건별(전압/밸브개도) 세부 통계

전압 상승에 따라 RMS/Peak가 대체로 단조증가하는 패턴 확인됨(6.5V 126.9mA → 11.5V 235.9mA). 자세한 수치는 `summary_by_condition.csv` 참고.

`normal_valve_full_closed`(체절 상태)는 표준편차(21.2)가 다른 정상 조건(8~9대)보다 크게 나타남 — 막힌 상태에서 압력이 주기적으로 쌓였다 빠지며 생기는 진동성 부하로 추정.

---

## 5. 파일 목록

```
raw/
  normal_valve_full_open.csv
  normal_valve_mid_open.csv
  normal_valve_full_closed.csv
  low_flow_6.5V.csv ~ low_flow_12V.csv (12V는 참고용, 학습 제외)
  dryrun_no_piping.csv
  start_fail_power_disconnect.csv
  start_fail_undervoltage_6V.csv
  reference_excluded/
    dryrun_suction_only_OVERLAP_EXCLUDED.csv  ← 참고용, 학습 데이터 아님

summary/
  combined_all_labeled.csv   ← 학습용 통합 데이터셋 (label 컬럼 정정 완료)
  summary_by_class.csv
  summary_by_condition.csv
  summary.md (본 파일)

analysis/
  충압펌프_전류파형_분석_보고서.md
```
