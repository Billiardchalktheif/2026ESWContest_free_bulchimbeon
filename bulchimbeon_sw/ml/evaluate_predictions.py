"""
가스계/유도등 선형회귀 예측(server/judge/regression.py)의 사후 정확도 검증.

방법: 재현 가능하도록 시드를 고정한 뒤 각 설비의 긴 시계열을 합성 생성하고,
앞 70%(TRAIN_FRACTION)만 회귀에 사용해 "임계치 도달까지 남은 시간"을 예측한다.
그 다음 나머지 30% 구간을 포함한 전체 시계열에서 실제로 임계치에 도달한 시점을
찾아 예측과 비교, 오차(가스계=일, 유도등=일, %)를 계산한다.

검증 대상: 가스계 predicted_days_to_5pct, 유도등 predicted_days_to_replace(평상시
전압의 느린 달력 추세) — 둘 다 "긴 시계열을 앞/뒤로 나눠 역산"하는 회귀 예측이라
이 70/30 사후검증 방법론이 적용된다. 유도등 estimated_discharge_min(데모모드 실측
방전시간)은 검증 대상이 아니다 — 짧은 실측 구간에서 바로 계산되는 값이라 이런
장기 시계열 분할 검증 자체가 방법론적으로 안 맞기 때문(evaluate_evac_scenarios
docstring 참고).

여러 시나리오(누출/방전 속도가 다른 여러 케이스)에 대해 이 과정을 반복해
평균절대오차(MAE)를 낸다 — 케이스 하나만으로는 우연히 잘 맞을 수도 있어서
여러 케이스 평균이 개발완료보고서에 쓸 신뢰할 만한 근거가 된다.

출력: 콘솔 요약 + ml/prediction_accuracy.json + ml/prediction_accuracy.csv
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent / "server"))
from judge.regression import (  # noqa: E402
    DEFAULT_GAS_TYPE, EVAC_DISCHARGE_THRESHOLD_V, GAS_LOSS_THRESHOLD_PCT,
    _fit_slope_intercept,
)

RESULT_JSON_PATH = Path(__file__).parent / "prediction_accuracy.json"
RESULT_CSV_PATH = Path(__file__).parent / "prediction_accuracy.csv"

SEED = 42                # 재현성을 위해 고정 — 이 결과가 보고서 근거자료이므로 재현 가능해야 함
TRAIN_FRACTION = 0.7      # 앞 70%로 예측, 뒤 30%로 검증
N_POINTS = 150            # 시나리오당 시계열 길이 (일 단위 포인트 수)


def _find_crossing_day(ts, values, target, decreasing=True):
    """전체 시계열에서 target을 처음 넘는 시점을 찾아 ts[0] 기준 며칠째인지 반환. 못 찾으면 None."""
    for i in range(len(values)):
        crossed = (values[i] <= target) if decreasing else (values[i] >= target)
        if crossed:
            return (ts[i] - ts[0]) / 86400.0
    return None


def _evaluate_one_case(ts, values, target, unit_scale_sec):
    """
    (ts, values) 합성 시계열 하나에 대해 앞 70%로 예측, 전체로 검증.
    unit_scale_sec: 결과를 어떤 시간 단위로 낼지 (일=86400, 분=60)
    반환: dict 또는 평가 불가 케이스면 None
    """
    split = int(len(ts) * TRAIN_FRACTION)
    train_ts, train_values = ts[:split], values[:split]

    slope, _ = _fit_slope_intercept(train_ts, train_values)
    if slope >= 0:
        return None  # 감소 추세가 아니면애초에 예측 대상이 아님 (server 로직과 동일 조건)

    as_of_ts = train_ts[-1]
    as_of_value = train_values[-1]

    if as_of_value <= target:
        predicted = 0.0
    else:
        predicted = (target - as_of_value) / slope / unit_scale_sec

    actual_crossing_day = _find_crossing_day(ts, values, target, decreasing=True)
    if actual_crossing_day is None:
        return None  # 전체 기간 안에 임계치 도달을 안 한 케이스는 비교 대상에서 제외

    as_of_day = (as_of_ts - ts[0]) / 86400.0
    actual_remaining = (actual_crossing_day - as_of_day) * (86400.0 / unit_scale_sec)
    if actual_remaining < 0:
        return None  # 학습구간 안에서 이미 넘어버린 케이스는 "0으로 예측"이 자명해 정확도 비교 의미가 적어 제외

    error = abs(predicted - actual_remaining)
    error_pct = (error / actual_remaining * 100.0) if actual_remaining > 0 else None

    return {
        "predicted": predicted,
        "actual": actual_remaining,
        "error": error,
        "error_pct": error_pct,
    }


def evaluate_gas_scenarios(rng, gas_type=DEFAULT_GAS_TYPE, n_scenarios=8):
    """가스계: 손실 속도가 서로 다른 여러 시나리오를 생성해 검증 (단위: 일)"""
    target_pct = GAS_LOSS_THRESHOLD_PCT.get(gas_type, GAS_LOSS_THRESHOLD_PCT[DEFAULT_GAS_TYPE])
    results = []
    for i in range(n_scenarios):
        initial_weight = 47000.0
        # 손실 속도는 "임계치 도달 시점이 검증구간(뒤 30%, 약 106~150일째)에 오도록"
        # 역산해서 범위를 잡았다 — 너무 빠르면 학습구간 안에서 이미 넘어버려서
        # 정확도 비교 자체가 성립하지 않는다(70/30 백테스트의 핵심 전제).
        daily_loss_frac = rng.uniform(target_pct / 100.0 / 149, target_pct / 100.0 / 106)
        ts = np.arange(N_POINTS) * 86400.0
        weight = initial_weight - np.cumsum(np.full(N_POINTS, initial_weight * daily_loss_frac))
        weight += rng.normal(0, initial_weight * 0.0002, N_POINTS)

        target_weight = initial_weight * (1 - target_pct / 100.0)
        result = _evaluate_one_case(ts, weight, target_weight, unit_scale_sec=86400.0)
        if result is not None:
            result.update({"scenario": f"gas_{gas_type}_{i}", "daily_loss_frac": daily_loss_frac})
            results.append(result)
    return results


def evaluate_evac_scenarios(rng, n_scenarios=8):
    """
    유도등: 방전 속도가 서로 다른 여러 시나리오를 생성해 검증 (단위: 일).

    v5 버그수정: 여기서 검증하는 건 평상시 전압의 느린 달력 추세로 계산하는
    predicted_days_to_replace(배터리 교체 예상 시점, 일 단위)다. 이 시나리오 자체가
    하루 간격 시계열(ts = 하루*86400초)이므로 결과도 일 단위로 내야 맞는데, 예전엔
    unit_scale_sec=60.0(분 단위)으로 잘못 내고 있었다 — 회귀 계산 자체는 수학적으로
    틀리지 않았지만, 실제 DB 필드(predicted_days_to_replace)의 단위와 검증 스크립트의
    출력 단위가 달라서 개발완료보고서에 이 MAE를 인용할 때 혼동될 수 있었다.

    ⚠️ estimated_discharge_min(데모모드 실측 방전시험, 분 단위)은 이 스크립트로 검증하지
    않는다 — 못 하는 게 아니라 방법론 자체가 안 맞다. 이 스크립트는 "긴 시계열의 앞
    70%로 예측하고 뒤 30%의 실제 도달 시점과 비교"하는 사후검증인데,
    estimated_discharge_min은 애초에 장기 시계열을 앞/뒤로 나눠 역산하는 값이 아니라
    데모모드의 짧은 실측 구간에서 그 자리에서 바로 계산되는 값이다(server/judge/
    regression.py의 evaluate_evac_discharge_capacity 참고). 검증 대상이 아니라
    적용 범위 밖이라는 뜻 — "왜 이 필드는 검증 안 하냐"는 질문에 이 설명으로 답할 것.
    """
    results = []
    for i in range(n_scenarios):
        initial_v = 4.2
        # 가스계와 동일한 이유로 임계치 도달 시점이 검증구간(뒤 30%)에 오도록 역산
        voltage_range = initial_v - EVAC_DISCHARGE_THRESHOLD_V
        daily_drop = rng.uniform(voltage_range / 149, voltage_range / 106)
        ts = np.arange(N_POINTS) * 86400.0
        voltage = initial_v - np.cumsum(np.full(N_POINTS, daily_drop))
        voltage += rng.normal(0, 0.001, N_POINTS)

        result = _evaluate_one_case(ts, voltage, EVAC_DISCHARGE_THRESHOLD_V, unit_scale_sec=86400.0)
        if result is not None:
            result.update({"scenario": f"evac_{i}", "daily_drop": daily_drop})
            results.append(result)
    return results


def _print_summary(name, results, unit_label):
    if not results:
        print(f"[{name}] 평가 가능한 케이스 없음")
        return
    errors = [r["error"] for r in results]
    pct_errors = [r["error_pct"] for r in results if r["error_pct"] is not None]
    mae = sum(errors) / len(errors)
    print(f"[{name}] 케이스 {len(results)}개 / MAE = {mae:.2f}{unit_label}", end="")
    if pct_errors:
        print(f" (평균 오차율 {sum(pct_errors)/len(pct_errors):.1f}%)")
    else:
        print()


def main():
    rng = np.random.default_rng(SEED)

    gas_results = evaluate_gas_scenarios(rng)
    evac_results = evaluate_evac_scenarios(rng)

    print("=" * 60)
    print("예측 정확도 검증 - 앞 70% 데이터로 예측, 뒤 30%로 실측 비교")
    print("=" * 60)
    _print_summary("가스계", gas_results, "일")
    _print_summary("유도등(predicted_days_to_replace)", evac_results, "일")

    output = {
        "train_fraction": TRAIN_FRACTION,
        "seed": SEED,
        "gas": gas_results,
        "evac_light": evac_results,
    }
    with open(RESULT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nJSON 저장: {RESULT_JSON_PATH}")

    with open(RESULT_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["device", "scenario", "predicted", "actual", "error", "error_pct"])
        for r in gas_results:
            writer.writerow(["gas", r["scenario"], r["predicted"], r["actual"], r["error"], r["error_pct"]])
        for r in evac_results:
            writer.writerow(["evac_light", r["scenario"], r["predicted"], r["actual"], r["error"], r["error_pct"]])
    print(f"CSV 저장: {RESULT_CSV_PATH}")


if __name__ == "__main__":
    main()
