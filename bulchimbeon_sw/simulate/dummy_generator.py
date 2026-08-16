"""
더미 데이터 생성기.

목적: 실물 테스트베드(펌프/배관/로드셀 등)가 아직 없어도,
UDP 수신 서버(udp_receiver.py) ~ DB ~ 학습 파이프라인 전체가
정상 동작하는지 미리 검증하기 위함.

사용법:
  1) 터미널 A: python server/udp_receiver.py
  2) 터미널 B: python simulate/dummy_generator.py --all

실물이 준비되는 대로, 이 스크립트가 보내는 UDP 패킷 포맷 그대로
ESP32 펌웨어가 보내도록 맞추면 서버 쪽은 코드 변경 없이 그대로 재사용된다.
"""
import argparse
import json
import socket
import sys
import time
from pathlib import Path

import numpy as np

sys.path.append(str(Path(__file__).parent.parent / "server"))
from feature_extraction import extract_features  # noqa: E402
from config import LOOP_FIXED_OFFSET_OHM  # noqa: E402

UDP_IP = "127.0.0.1"
UDP_PORT = 9000

RNG = np.random.default_rng(42)


def send(sock, pkt: dict):
    sock.sendto(json.dumps(pkt).encode("utf-8"), (UDP_IP, UDP_PORT))
    time.sleep(0.01)  # 서버가 커밋을 따라올 시간을 줌 (테스트용 벌크 전송이라 필요; 실제 노드는 원래 느림)


# ---------------------------------------------------------------------------
# 수계 — 주펌프 파형 3클래스 (정상 / 과부하 / 공회전)
# ---------------------------------------------------------------------------
def generate_pump_waveform(cls: str, n_samples: int = 500) -> np.ndarray:
    """
    실제 CT클램프 파형을 흉내낸 정류된 전류 포락선.
    normal_operation(정상운전): 중간 진폭, 안정적, duty cycle 높음
    stall_operation(체절운전): 큰 진폭, 지속적으로 높게 유지
    dry_run(공회전): 작은 진폭, 불안정하고 듀티가 낮음(부하가 없어 짧게 끊김)

    v7: 라벨을 5클래스 체계로 재정의(파일 하단 stream_pump_performance_test 참고)하며
    이 함수의 클래스 선택 문자열도 새 라벨명으로 통일했다 — "정상/과부하/공회전"이라는
    이름이 실측 전류 높낮이를 미리 단정하고 있었는데, 실제로는 펌프 종류에 따라 체절 시
    전류가 오히려 낮게 나올 수도 있어서, 라벨명 자체는 "물리적으로 확인된 실험 상태"만
    가리키도록 바꿨다(server/pump_performance_test.py 상단 설명 참고).
    """
    t = np.linspace(0, 2 * np.pi * 5, n_samples)
    if cls == "normal_operation":
        base, noise = 2.0, 0.08
        env = 0.5 * (1 + np.sign(np.sin(t)))  # 구형파 형태의 안정적 온/오프
    elif cls == "stall_operation":
        base, noise = 3.5, 0.12
        env = 0.5 * (1 + np.sign(np.sin(t))) * 1.05
        env = np.clip(env, 0, 1.1)
    elif cls == "dry_run":
        base, noise = 0.9, 0.25
        env = 0.5 * (1 + np.sign(np.sin(t + RNG.normal(0, 0.5, n_samples))))
    else:
        raise ValueError(cls)

    waveform = base * env + RNG.normal(0, noise, n_samples)
    return np.abs(waveform)


def stream_pump_classifier_data(sock, n_per_class: int = 60):
    classes = ["normal_operation", "stall_operation", "dry_run"]  # v7: 5클래스 라벨 체계
    seq = 0
    ts = time.time()
    for cls in classes:
        for _ in range(n_per_class):
            wf = generate_pump_waveform(cls)
            feats = extract_features(wf)
            pkt = {
                "node_id": "pump_main_01",
                "device_type": "water_pump",
                "pump_type": "main",
                "seq": seq,
                "ts": ts,
                "label": cls,
                **feats,
            }
            send(sock, pkt)
            seq += 1
            ts += 3.0  # 사이클 간 3초 가정
    print(f"[수계-분류] {n_per_class * len(classes)}개 파형 전송 완료 (정상/과부하/공회전 각 {n_per_class}개)")


def stream_jockey_pump_leak_demo(sock, n_cycles: int = 40):
    """
    충압펌프 기동주기 — 처음엔 정상 간격(느슨), 뒤로 갈수록
    미세누수로 인해 간격이 짧아지는 시나리오. 시연/검증용.
    """
    seq = 0
    ts = time.time()
    interval = 600.0  # 시작 10분 간격
    for i in range(n_cycles):
        # 후반부로 갈수록 간격이 지수적으로 짧아짐 (누수 진행 시뮬레이션)
        if i > n_cycles // 2:
            interval *= 0.94
        jitter = RNG.normal(0, interval * 0.05)
        pkt = {
            "node_id": "pump_jockey_01",
            "device_type": "water_pump",
            "pump_type": "jockey",
            "seq": seq,
            "ts": ts,
            "cycle_interval_sec": max(interval + jitter, 10),
        }
        send(sock, pkt)
        seq += 1
        ts += interval
    print(f"[수계-충압펌프] 기동주기 {n_cycles}회 전송 완료 (후반부 미세누수 재현)")


# ---------------------------------------------------------------------------
# 수계 — 성능시험(1차, 규칙기반) + CT클램프 파형(2차, AI) 결합 시뮬레이션 (v3 §4 핵심)
# 밸브를 체절/부하로 전환하며 그 순간의 압력값과 CT 파형을 "동시에" 캡처하고,
# valve_state를 정답 라벨로 자동 부여한다(label_source='performance_test') — 사람이
# "아마 이게 과부하겠지"라고 추측하던 수동 라벨(stream_pump_classifier_data)보다
# 신뢰도 높은 학습 데이터를 만드는 게 이 시뮬레이션의 목적.
# ---------------------------------------------------------------------------
def stream_pump_performance_test(sock, n_per_state: int = 40, rated_pressure_kpa: float = 700.0):
    """
    v4 정정: 밸브가 니들밸브(수동 조작)로 바뀌어 pump_node.ino가 더 이상 valve_state를
    직접 보내지 않는다(dryrun 제외). 그래서 이 시뮬레이션도 실제 펌프 노드와 동일하게
    pressure_kpa만 연속으로 흘려보내고, 서버(server/pump_performance_test.py의
    determine_valve_state/infer_valve_state_from_pressure)가 압력값 추세만으로
    체절/부하 상태를 스스로 추정하는지를 검증한다.
    """
    seq = 0
    ts = time.time()
    node_id = "pump_main_01"

    # 체절 상당 — 압력 최대. 발표자가 니들밸브를 잠갔다고 가정한 구간(법정기준 140% 이내로 재현).
    for _ in range(n_per_state):
        wf = generate_pump_waveform("stall_operation")
        feats = extract_features(wf)
        pressure_kpa = rated_pressure_kpa * (1.10 + RNG.normal(0, 0.03))
        send(sock, {
            "node_id": node_id, "device_type": "water_pump", "pump_type": "main",
            "seq": seq, "ts": ts, "pressure_kpa": pressure_kpa,
            **feats,
        })
        seq += 1
        ts += 2.0

    # 부하 상당 — 압력 정상범위. 발표자가 니들밸브를 다시 연 구간(법정기준 65% 이상으로 재현).
    for _ in range(n_per_state):
        wf = generate_pump_waveform("normal_operation")
        feats = extract_features(wf)
        pressure_kpa = rated_pressure_kpa * (0.95 + RNG.normal(0, 0.03))
        send(sock, {
            "node_id": node_id, "device_type": "water_pump", "pump_type": "main",
            "seq": seq, "ts": ts, "pressure_kpa": pressure_kpa,
            **feats,
        })
        seq += 1
        ts += 2.0

    # 공회전(dryrun) — 흡입측 차단/탱크 비움. 밸브 개폐와 무관한 별도 물리 조작이라
    # (압력 패턴으로 추정 불가) ESP32가 valve_state="dryrun"을 명시적으로 보낸다.
    for _ in range(n_per_state):
        wf = generate_pump_waveform("dry_run")
        feats = extract_features(wf)
        send(sock, {
            "node_id": node_id, "device_type": "water_pump", "pump_type": "main",
            "seq": seq, "ts": ts, "valve_state": "dryrun",
            **feats,
        })
        seq += 1
        ts += 2.0

    print(
        f"[수계-성능시험] 체절/부하/공회전 각 {n_per_state}개 전송 완료 "
        "(valve_state는 서버가 압력값으로 추정 - dryrun만 예외)"
    )


# ---------------------------------------------------------------------------
# 자탐 — 루프 저항 베이스라인 (정상 노드 1 + 서서히 열화되는 노드 1)
# 법정 기준: 감지기회로 전로저항은 50Ω 이하가 정상(소방시설 점검관리 매뉴얼) — 단, 이
# 기준은 server/judge/regression.py의 evaluate_loop_resistance가 "보정저항"(raw -
# LOOP_FIXED_OFFSET_OHM)에 대해 적용한다. 더미 생성기는 실제 ESP32처럼 raw(오프셋 포함)
# 값을 그대로 보내야 하므로, 이 baseline도 실물 회로의 R_loop 고정저항분(config.py의
# LOOP_FIXED_OFFSET_OHM)을 더한 raw 스케일이다 — 숫자를 여기 또 하드코딩하지 않고
# config.py에서 직접 import해서 쓴다. 오프셋을 안 더하면 서버가 그만큼 깎아버려서
# corrected 값이 음수 근처로 나오고 판정 자체가 무의미해진다.
# 열화가 진행돼도 법정 한계까지는 한참 남은 상태에서 2층(추세) 판정이 먼저 잡아내는
# 것을 보여주는 게 이 시스템의 핵심 가치이므로, "50Ω(보정 후)을 넘기는 것"이 아니라
# "서서히 상승하는 추세 자체"를 재현하는 데 집중한다.
# ---------------------------------------------------------------------------
FIRE_ALARM_BASELINE_OHM = 20.0 + LOOP_FIXED_OFFSET_OHM  # 20Ω(보정 후 목표 baseline) + 실측 오프셋(config.py 참고, 숫자를 여기 다시 적지 않음)
FIRE_ALARM_NOISE_STD = 0.3          # ADS1115(16bit) 기준 — 내장 12bit ADC보다 노이즈 훨씬 작음
# 상수를 그대로 더하는 오프셋 보정은 절편만 바꿀 뿐 기울기에는 영향이 없으므로
# DEGRADE_SLOPE(스텝당 상승분)는 그대로 유지한다. 1시간 간격 스텝 기준 0.2Ω/step =
# 4.8Ω/day로, LOOP_TREND_ALARM_SLOPE(2.0Ω/day)를 넉넉히 넘어 2층 판정이 안정적으로
# alarm까지 전이되는 것을 확인했다(simulate/verify_loop_resistance_judgment.py 참고).
FIRE_ALARM_DEGRADE_SLOPE = 0.2      # 스텝당 저항 상승분 (열화재현용)


def stream_fire_alarm(sock, n_points: int = 100):
    seq = 0
    ts = time.time()
    for i in range(n_points):
        healthy = FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        degrading = (
            FIRE_ALARM_BASELINE_OHM
            + i * FIRE_ALARM_DEGRADE_SLOPE
            + RNG.normal(0, FIRE_ALARM_NOISE_STD)
        )
        send(sock, {
            "node_id": "fire_zone_A", "device_type": "fire_alarm", "zone_type": "differential",
            "zone": "A구역(정상)", "seq": seq, "ts": ts, "loop_resistance_ohm": healthy,
        })
        send(sock, {
            "node_id": "fire_zone_B", "device_type": "fire_alarm", "zone_type": "differential",
            "zone": "B구역(열화재현)", "seq": seq, "ts": ts, "loop_resistance_ohm": degrading,
        })
        seq += 1
        ts += 3600  # 1시간 간격 가정 (실제론 상시로깅, 압축 시뮬레이션)
    print(f"[자탐] {n_points}개 시점 전송 완료 (A=정상 / B=점진적 열화)")


# ---------------------------------------------------------------------------
# 자탐2(광전식/연기구역) — 비화재보 판별 AI 학습데이터 시뮬레이션 (v4 §4)
# fire/cooking/normal 3상태를 온도상승률·가스농도·습도 조합으로 재현한다.
# 핵심 구분 포인트: 화재는 온도상승률이 크고 습도는 안 오르는 반면, 조리수증기는
# 습도가 크게 오르고 온도상승은 미미하다 — 실측에서 이 조합이 실제로 겹치지 않는지가
# 부품 수령 후 가장 먼저 검증해야 할 항목이다(ml/train_nuisance_classifier.py 상단 참고).
# 아래 범위는 재현 시나리오 설계값이며, 실측 후 신호가 겹치면 재조정이 필요하다.
# ---------------------------------------------------------------------------
NUISANCE_FEATURE_RANGES = {
    "fire": {"temp_rise_rate": (0.5, 1.5), "mq2_raw": (1500, 2500), "humidity_pct": (30, 45)},
    "cooking": {"temp_rise_rate": (0.02, 0.12), "mq2_raw": (700, 1200), "humidity_pct": (70, 90)},
    "normal": {"temp_rise_rate": (-0.02, 0.02), "mq2_raw": (350, 550), "humidity_pct": (35, 55)},
}


def stream_nuisance_alarm(sock, n_per_class: int = 60):
    seq = 0
    ts = time.time()
    node_id = "fire_zone_photoelectric_01"
    for label, ranges in NUISANCE_FEATURE_RANGES.items():
        for _ in range(n_per_class):
            temp_rise_rate = RNG.uniform(*ranges["temp_rise_rate"])
            mq2_raw = RNG.uniform(*ranges["mq2_raw"])
            humidity_pct = RNG.uniform(*ranges["humidity_pct"])
            send(sock, {
                "node_id": node_id, "device_type": "fire_alarm",
                "zone": "광전식구역", "zone_type": "photoelectric",
                "seq": seq, "ts": ts, "loop_resistance_ohm": FIRE_ALARM_BASELINE_OHM + RNG.normal(0, FIRE_ALARM_NOISE_STD),
                "temp_rise_rate": temp_rise_rate, "mq2_raw": mq2_raw, "humidity_pct": humidity_pct,
                "label": label,
            })
            seq += 1
            ts += 5.0
    print(f"[자탐2-비화재보] fire/cooking/normal 각 {n_per_class}개 전송 완료 (label 자동부여)")


# ---------------------------------------------------------------------------
# 가스계 — 중량 점진적 손실
# ---------------------------------------------------------------------------
# v3: 재보정 — 기존 0.0009(하루 0.09%)는 60일 누적시 loss_pct가 5.4%까지 가버려서,
# 임계(5%)를 이미 넘은 상태로 시연을 시작하게 됐다(predicted_days_to_5pct가 항상 0.0으로
# 나와서 "앞으로 며칠 남았다"는 핵심 화면 자체를 볼 수 없었음). 0.0005(하루 0.05%)로
# 낮춰서 60일 시점에도 아직 2.5~3.5% 구간(목표 중앙값 3%)에 머물도록 재조정 —
# "남은 일수 예측"이 실제로 보이는 상태를 유지한다. 노이즈도 같은 비율로 축소.
def stream_gas(sock, n_points: int = 60, initial_weight: float = 47000.0, gas_type: str = "co2"):
    seq = 0
    ts = time.time()
    weight = initial_weight
    daily_loss = initial_weight * 0.0005  # 하루 약 0.05% 손실 가정 (60일 누적 시 약 3%)
    for _ in range(n_points):
        weight -= daily_loss + RNG.normal(0, initial_weight * 0.0001)
        send(sock, {
            "node_id": "gas_co2_01", "device_type": "gas", "zone": "가스계실",
            "gas_type": gas_type,
            "seq": seq, "ts": ts, "weight_g": weight, "initial_weight_g": initial_weight,
        })
        seq += 1
        ts += 86400  # 1일 간격
    print(f"[가스계] {n_points}일치 중량 데이터 전송 완료 (gas_type={gas_type})")


# ---------------------------------------------------------------------------
# 유도등 — 배터리 전압 점진적 열화
# ---------------------------------------------------------------------------
def stream_evac_light(sock, n_points: int = 60, initial_v: float = 4.2):
    seq = 0
    ts = time.time()
    v = initial_v
    for _ in range(n_points):
        v -= 0.004 + RNG.normal(0, 0.001)
        send(sock, {
            "node_id": "evac_light_01", "device_type": "evac_light", "zone": "1층 복도",
            "seq": seq, "ts": ts, "battery_voltage": max(v, 2.5), "lux": int(RNG.uniform(80, 120)),
        })
        seq += 1
        ts += 86400
    print(f"[유도등] {n_points}일치 배터리 전압 데이터 전송 완료")


# ---------------------------------------------------------------------------
# 소화기 — 이탈감지 시나리오 (v2: 압력 폐기, 가속도+게이트웨이 연결상태 기반)
# 4개 노드 중 2개는 완전 평온, 1개는 "살짝 건드림"(같은 게이트웨이 유지 -> 오탐으로
# 무시되어야 함), 1개는 "들고 나감"(다른 게이트웨이로 연결 전환 -> 이탈 확정돼야 함)
# ---------------------------------------------------------------------------
GATEWAY_ID_DEFAULT = "gw_01"
ACCEL_BASELINE_G = 9.8   # 정지 상태 중력가속도 근사(m/s^2)
ACCEL_EVENT_PEAK = 25.0  # 이동/충격 시 튀는 값 (server/tamper_detection.py의 임계 15.0 초과)


def stream_extinguisher(sock, n_nodes: int = 4, n_baseline_points: int = 5):
    seq = 0
    ts = time.time()

    scenarios = {0: "quiet", 1: "false_positive", 2: "quiet", 3: "removed"}

    for node_i in range(n_nodes):
        node_id = f"ext_{node_i+1:02d}"
        zone = f"창고 {node_i+1}"
        scenario = scenarios[node_i]
        node_ts = ts

        # 평상시 baseline heartbeat — 같은 게이트웨이, 가속도는 중력값 근처에서만 흔들림
        for _ in range(n_baseline_points):
            send(sock, {
                "node_id": node_id, "device_type": "extinguisher", "zone": zone,
                "seq": seq, "ts": node_ts,
                "accel_magnitude": ACCEL_BASELINE_G + RNG.normal(0, 0.2),
                "gateway_id": GATEWAY_ID_DEFAULT,
            })
            seq += 1
            node_ts += 3600  # 평상시엔 1시간 간격

        if scenario == "quiet":
            continue

        # 이동/충격 이벤트 발생 — 가속도 급등, 아직은 1차 플래그(moved)일 뿐 경보 아님
        event_ts = node_ts
        send(sock, {
            "node_id": node_id, "device_type": "extinguisher", "zone": zone,
            "seq": seq, "ts": event_ts,
            "accel_magnitude": ACCEL_EVENT_PEAK, "gateway_id": GATEWAY_ID_DEFAULT,
        })
        seq += 1
        node_ts += 12

        # 확인 대기(60초) 동안 후속 패킷을 촘촘히 전송 — 실제 이벤트 직후엔 확인을 위해
        # 평소보다 자주 통신한다고 가정. false_positive는 같은 게이트웨이 유지,
        # removed는 다른 게이트웨이로 연결이 전환된 것으로 재현.
        follow_up_gateway = GATEWAY_ID_DEFAULT if scenario == "false_positive" else "gw_02"
        while node_ts - event_ts <= 70:
            send(sock, {
                "node_id": node_id, "device_type": "extinguisher", "zone": zone,
                "seq": seq, "ts": node_ts,
                "accel_magnitude": ACCEL_BASELINE_G + RNG.normal(0, 0.2),
                "gateway_id": follow_up_gateway,
            })
            seq += 1
            node_ts += 12

    print(
        f"[소화기] {n_nodes}개 노드 이탈감지 시나리오 전송 완료 "
        "(ext_02=오탐으로 무시되어야 함 / ext_04=이탈 확정되어야 함)"
    )


# ---------------------------------------------------------------------------
# 유도등 — 데모모드 방전시험 시뮬레이션 (v3 §1 버그수정 검증용)
# 실제 방전시험은 배터리를 진짜로 부하 상태에 두고 관찰하는 것이라, 45초 같은 짧은
# 구간 안에서는 전압 변화가 원래 아주 작다(20분짜리 완만한 방전 곡선을 45초만 잘라보면
# 그 구간의 기울기 자체가 작을 수밖에 없음 — 시뮬레이션 편의가 아니라 물리적으로 그렇다).
# 노이즈도 이 작은 신호 스케일에 비례해서 줄였다. 실측 시엔 ADC 분해능이 이 정도로
# 미세한 변화를 잡아낼 수 있는지 반드시 확인 필요 (개발완료보고서에 측정 민감도
# 한계로 명시할 것).
# ---------------------------------------------------------------------------
def stream_evac_light_demo_discharge_test(
    sock, node_id: str = "evac_light_01", zone: str = "1층 복도",
    demo_duration_sec: float = 45.0, n_samples: int = 30,
    final_voltage: float = 3.2, target_remaining_min: float = 18.0,
):
    discharge_threshold_v = 3.0  # server/regression_forecast.py의 EVAC_DISCHARGE_THRESHOLD_V와 일치
    gap = final_voltage - discharge_threshold_v
    total_drop = gap * demo_duration_sec / (target_remaining_min * 60)
    slope = -total_drop / demo_duration_sec
    start_voltage = final_voltage - slope * demo_duration_sec
    noise_std = total_drop * 0.05

    seq = 0
    ts = time.time()
    sample_interval = demo_duration_sec / n_samples
    # 주의: 노이즈를 v_true에 누적시키면(v += ... + noise) 랜덤워크가 되어, 30번 누적된
    # 노이즈의 표준편차(~noise_std*sqrt(n))가 45초짜리 짧은 구간의 원래 작은 신호(total_drop)를
    # 압도해버려 회귀 기울기가 완전히 틀어진다. 그래서 "진짜 추세"(v_true)는 노이즈 없이
    # 순수 선형으로 진행시키고, 매 샘플마다 독립적인 측정노이즈만 더해서 전송한다
    # (실제 센서 노이즈도 매 측정마다 독립적이지 누적되지 않는다).
    v_true = start_voltage
    for _ in range(n_samples):
        v_true += slope * sample_interval
        observed_v = v_true + RNG.normal(0, noise_std)
        send(sock, {
            "node_id": node_id, "device_type": "evac_light", "zone": zone,
            "seq": seq, "ts": ts, "battery_voltage": observed_v,
            "lux": int(RNG.uniform(80, 120)), "demo_mode": 1,
        })
        seq += 1
        ts += sample_interval
    print(
        f"[유도등-데모방전시험] {node_id} {n_samples}개 샘플 전송 완료 "
        f"(목표 잔여시간 약 {target_remaining_min:.0f}분 재현)"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="5개 설비 전부 전송")
    parser.add_argument("--pump-only", action="store_true", help="수계(분류용+누수시연)만 전송")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    if args.pump_only or args.all:
        stream_pump_classifier_data(sock)
        stream_jockey_pump_leak_demo(sock)
        stream_pump_performance_test(sock)
    if args.all:
        stream_fire_alarm(sock)
        stream_nuisance_alarm(sock)
        stream_gas(sock)
        stream_evac_light(sock)
        stream_evac_light_demo_discharge_test(sock)
        stream_extinguisher(sock)

    if not (args.all or args.pump_only):
        print("옵션을 지정하세요: --all 또는 --pump-only")


if __name__ == "__main__":
    main()
