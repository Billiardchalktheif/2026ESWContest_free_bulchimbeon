"""
UDP 패킷 1개 해석 + 판정 파이프라인 호출.
v7 폴더 재편으로 udp_receiver.py의 handle_packet()/upsert_heartbeat()/
mark_offline_nodes()를 이 위치(server/receiver/packet_parser.py)로 분리했다.
로직은 그대로이고, 판정 모듈 임포트 경로만 v7 재편 결과(judge/*, 그대로 server/에
남은 pump_performance_test.py)에 맞게 바꿨다. 소켓 수신 루프는
server/receiver/udp_listener.py가 맡고, 이 파일은 "패킷 하나를 받으면 어떻게
해석/적재/판정할지"만 담당한다.

패킷 포맷 예시 (수계 - 주펌프 파형):
{
  "node_id": "pump_main_01",
  "device_type": "water_pump",
  "pump_type": "main",
  "seq": 42,
  "ts": 1735500000.0,
  "rms": 2.1, "peak": 2.6, "duty_cycle": 0.82,
  "label": "normal"        # 학습 데이터 수집시에만, 실측시 생략
}

패킷 포맷 예시 (수계 - 충압펌프 기동):
{
  "node_id": "pump_jockey_01",
  "device_type": "water_pump",
  "pump_type": "jockey",
  "seq": 7,
  "ts": 1735500010.0,
  "cycle_interval_sec": 540.0
}
"""
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from judge.classify import handle_nuisance_prediction, predict_nuisance_label, predict_pump_label  # noqa: E402
from judge.regression import (  # noqa: E402
    evaluate_evac_discharge_capacity, evaluate_evac_light, evaluate_loop_resistance, evaluate_gas,
)
from judge.rules import evaluate_tamper  # noqa: E402
from pump_performance_test import determine_valve_state, evaluate_pump_performance_sample  # noqa: E402
from config import is_node_online  # noqa: E402

# ESP32 쪽 NTP 동기화가 아직 안 됐을 때(부팅 직후, 또는 NTP 서버 접속 실패 지속)
# getEpochSeconds()가 millis()/1000.0(부팅 후 경과초, 예: 12.5)을 그대로 보낸다 —
# 각 .ino 파일도 이 값을 판단 기준(NTP_SYNCED_THRESHOLD)으로 쓰고 있어 서버도 동일한
# 값을 쓴다. ESP32의 NTP 재시도 로직을 붙잡고 씨름하는 대신, 서버가 이 값을 그대로
# 믿지 않고 "패킷을 받은 이 순간의 서버 시각"으로 갈아치운다 — 라즈베리파이는 상시
# 네트워크에 연결돼 있어 시각을 이미 신뢰할 수 있는 쪽이기 때문. 루프저항 추세/
# 가스계/유도등 선형회귀는 전부 ts 기반 시계열이라, 1970년대 근처 값이 하나라도
# 섞이면 기울기 계산이 통째로 깨진다 — 이 보정이 없으면 그 오류를 데이터 쪽에서
# 걸러낼 방법이 없다.
NTP_SYNCED_THRESHOLD_TS = 1700000000


def upsert_heartbeat(conn, node_id, device_type, seq, ts, boot_id=None):
    """
    노드 생사 기록을 갱신한다. boot_id가 바뀌었으면(=재부팅) seqNum이 0으로 리셋되는 게
    정상이므로 그냥 정보 로그만 남기고, boot_id가 같은데 seq가 뒤로 갔으면 패킷 유실/
    재전송 의심으로 경고 로그를 남긴다. boot_id 없는 구형 패킷(예: 더미 생성기)은
    비교 없이 조용히 통과시킨다.
    """
    prev = conn.execute(
        "SELECT boot_id, last_seq FROM node_heartbeat WHERE node_id=?", (node_id,)
    ).fetchone()
    if prev is not None:
        prev_boot_id, prev_seq = prev
        if boot_id is not None and prev_boot_id is not None and boot_id != prev_boot_id:
            print(f"[INFO] 노드 재부팅 감지: {node_id} (boot_id {prev_boot_id} -> {boot_id})")
        elif (
            boot_id is not None and prev_boot_id is not None and boot_id == prev_boot_id
            and prev_seq is not None and seq is not None and seq < prev_seq
        ):
            # v5 버그수정: boot_id가 같을 때만 진짜 시퀀스 역행(패킷 유실/재전송)으로 판단한다.
            # boot_id가 아예 없는 구형/테스트 패킷(예: 더미 생성기가 같은 node_id로 여러
            # 스트림 함수를 순차 호출하며 seq가 0으로 재시작되는 경우)까지 역행으로 오인하면
            # 실제로 아무 문제 없는데 경고만 계속 찍히는 오탐이 된다.
            print(f"[WARN] {node_id} 시퀀스 역행 감지: {prev_seq} -> {seq} (패킷 유실/재전송 의심)")

    conn.execute(
        """
        INSERT INTO node_heartbeat (node_id, device_type, last_seen, last_seq, boot_id, status)
        VALUES (?, ?, ?, ?, ?, 'online')
        ON CONFLICT(node_id) DO UPDATE SET
            last_seen=excluded.last_seen,
            last_seq=excluded.last_seq,
            boot_id=excluded.boot_id,
            status='online'
        """,
        (node_id, device_type, ts, seq, boot_id),
    )


def handle_packet(conn, pkt: dict):
    device = pkt.get("device_type")
    node_id = pkt.get("node_id", "unknown")
    ts = pkt.get("ts", time.time())
    if ts < NTP_SYNCED_THRESHOLD_TS:
        # NTP 미동기화 상태로 보낸 패킷 — ts를 그대로 저장하면 안 되므로 서버 수신
        # 시각으로 대체한다(파일 상단 NTP_SYNCED_THRESHOLD_TS 설명 참고).
        ts = time.time()
    seq = pkt.get("seq", -1)
    boot_id = pkt.get("boot_id")

    upsert_heartbeat(conn, node_id, device, seq, ts, boot_id)

    if device == "fire_alarm":
        loop_resistance = pkt["loop_resistance_ohm"]
        zone = pkt.get("zone", node_id)
        zone_type = pkt.get("zone_type")  # 'differential' / 'photoelectric' — v4: 2구역 센서구성 분리
        label = pkt.get("label")  # 광전식구역 학습 데이터 수집시에만 실림(더미 생성기 등)
        cur = conn.execute(
            """INSERT INTO fire_alarm_log
               (ts, node_id, zone, zone_type, loop_resistance_ohm,
                temp_rise_rate, mq2_raw, temp_c, humidity_pct, label)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                ts, node_id, zone, zone_type, loop_resistance,
                pkt.get("temp_rise_rate"),
                # MQ-2/DHT22는 광전식구역 비화재보 판별(AI)의 입력 feature
                pkt.get("mq2_raw"), pkt.get("temp_c"), pkt.get("humidity_pct"), label,
            ),
        )
        # 적재 직후 루프저항 2층 판정 (법정 절대임계값 + 선형회귀 추세) — 두 구역 공통
        evaluate_loop_resistance(conn, node_id, cur.lastrowid, loop_resistance, zone)

        # 비화재보 판별 AI(2번째 AI 적용 지점) — 광전식구역이면서 label이 없는
        # 경우(=학습 데이터 수집이 아니라 실측 운영 데이터)에만 추론을 돌린다.
        # 수계와 동일한 원칙: label이 실려오면 학습용이므로 추론하지 않는다.
        if zone_type == "photoelectric" and label is None:
            predicted_label, confidence = predict_nuisance_label(
                pkt.get("temp_rise_rate"), pkt.get("mq2_raw"), pkt.get("humidity_pct")
            )
            if predicted_label is not None:
                conn.execute(
                    "UPDATE fire_alarm_log SET predicted_label=?, confidence=? WHERE id=?",
                    (predicted_label, confidence, cur.lastrowid),
                )
                handle_nuisance_prediction(zone, predicted_label, confidence)
    elif device == "water_pump":
        label = pkt.get("label")
        pump_type = pkt.get("pump_type")
        pressure_kpa = pkt.get("pressure_kpa")
        # v4: 밸브는 니들밸브(수동 조작)로 정정됨 — ESP32는 체절/부하를 직접 알려줄 수
        # 없다. dryrun만 ESP32가 명시적으로 보내고(흡입측 차단은 압력과 무관한 별도
        # 물리조작), 체절/부하는 압력값 패턴으로 서버가 추정한다(pump_performance_test.py).
        explicit_valve_state = pkt.get("valve_state")  # 지금은 "dryrun"만 여기로 들어옴
        # label이 패킷에 실려오면(구형 방식) 사람이 직접 붙인 수동 라벨로 간주한다.
        # 성능시험 라벨(label_source='performance_test')은 device가 보내는 게 아니라
        # valve_state를 보고 이 서버가 직접 채운다 — evaluate_pump_performance_sample 참고.
        label_source = "manual" if label is not None else None
        cur = conn.execute(
            """INSERT INTO water_pump_log
               (ts, node_id, pump_type, cycle_interval_sec, rms, peak, duty_cycle,
                label, label_source, valve_state, pressure_kpa)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts, node_id, pump_type,
                pkt.get("cycle_interval_sec"), pkt.get("rms"),
                pkt.get("peak"), pkt.get("duty_cycle"), label, label_source,
                explicit_valve_state, pressure_kpa,
            ),
        )

        if explicit_valve_state is not None:
            valve_state = explicit_valve_state
        elif pump_type == "main":
            # 압력 자동추정(우선) 또는 대시보드 수동 트리거(대안) — determine_valve_state
            # 안에서 우선순위가 처리된다. 둘 다 없으면 None(=평상시 모니터링).
            valve_state = determine_valve_state(conn, node_id, pressure_kpa)
        else:
            valve_state = None

        if valve_state is not None:
            # 성능시험(1차, 규칙기반) — valve_state를 정답 라벨로 자동 변환해 기록
            evaluate_pump_performance_sample(conn, cur.lastrowid, valve_state, pressure_kpa, node_id)
        # AI(2차, CT클램프 파형분류)는 성능시험 여부와 무관하게 항상 상시로 돈다(v3 패치).
        # "성능시험이 실패하면 AI로 넘어간다"는 순차 관계가 아니다 — label이 없는 모든
        # 실측 데이터(성능시험 중이든 평상시든)에 대해 매번 독립적으로 추론을 돌리고,
        # 성능시험 중이었다면 같은 행에 채워진 label(성능시험 정답)과 predicted_label(AI
        # 추론)을 나중에 대시보드가 비교해 일치/불일치를 보여준다.
        if pkt.get("pump_type") == "main" and label is None and pkt.get("rms") is not None:
            predicted_label, confidence = predict_pump_label(
                pkt["rms"], pkt["peak"], pkt["duty_cycle"]
            )
            if predicted_label is not None:
                conn.execute(
                    "UPDATE water_pump_log SET predicted_label=?, confidence=? WHERE id=?",
                    (predicted_label, confidence, cur.lastrowid),
                )
    elif device == "gas":
        weight_g = pkt["weight_g"]
        initial_weight_g = pkt["initial_weight_g"]
        gas_type = pkt.get("gas_type", "co2")  # 종류별로 기준이 다름 — 미지정시 co2로 간주
        zone = pkt.get("zone", node_id)
        cur = conn.execute(
            """INSERT INTO gas_log (ts, node_id, zone, gas_type, weight_g, initial_weight_g)
               VALUES (?,?,?,?,?,?)""",
            (ts, node_id, zone, gas_type, weight_g, initial_weight_g),
        )
        # 적재 직후 선형회귀로 gas_type별 임계 손실률 도달 예상 일수 계산. empty_container_weight_g는
        # 패킷이 아니라 config.py의 캘리브레이션 상수를 쓴다(evaluate_gas 기본값) — 용기 무게는
        # ESP32가 매번 알려줄 수 있는 값이 아니라 설치시 1회 실측하는 값이기 때문.
        evaluate_gas(conn, node_id, cur.lastrowid, ts, weight_g, initial_weight_g, gas_type, zone)
    elif device == "extinguisher":
        accel_magnitude = pkt.get("accel_magnitude")
        gateway_id = pkt.get("gateway_id")
        zone = pkt.get("zone", node_id)
        cur = conn.execute(
            """INSERT INTO extinguisher_log (ts, node_id, zone, accel_magnitude, gateway_id)
               VALUES (?,?,?,?,?)""",
            (ts, node_id, zone, accel_magnitude, gateway_id),
        )
        # 적재 직후 이탈감지 2단계 판정 (가속도 임계 + 게이트웨이 연결상태, 규칙 기반)
        evaluate_tamper(conn, node_id, cur.lastrowid, accel_magnitude, gateway_id, ts, zone)
    elif device == "evac_light":
        battery_voltage = pkt["battery_voltage"]
        demo_mode = pkt.get("demo_mode", 0)
        zone = pkt.get("zone", node_id)
        cur = conn.execute(
            "INSERT INTO evac_light_log (ts, node_id, zone, battery_voltage, lux, demo_mode) VALUES (?,?,?,?,?,?)",
            (ts, node_id, zone, battery_voltage, pkt.get("lux"), demo_mode),
        )
        if demo_mode:
            # 데모모드(실제 방전시험) 구간 — 짧은 구간 실측으로 estimated_discharge_min 계산
            evaluate_evac_discharge_capacity(conn, node_id, cur.lastrowid, ts, battery_voltage, zone=zone)
        else:
            # 평상시 — 느린 달력 추세로 predicted_days_to_replace(배터리 교체 예상 시점) 계산
            evaluate_evac_light(conn, node_id, cur.lastrowid, ts, battery_voltage)
    else:
        print(f"[WARN] 알 수 없는 device_type: {device}, 패킷 무시됨")
        return

    conn.commit()


def mark_offline_nodes(conn):
    """
    heartbeat 타임아웃(device_type별 기준, config.py의 is_node_online) 넘은 노드를
    offline으로 표시한다. device_type마다 정상 전송 주기가 크게 달라서(가스계는
    실배포 기준 1일 간격) 단일 cutoff로는 못 걸러내므로, 온라인 노드를 전부 읽어와
    device_type별 기준으로 하나씩 판정한다.
    """
    now = time.time()
    rows = conn.execute(
        "SELECT node_id, device_type, last_seen FROM node_heartbeat WHERE status='online'"
    ).fetchall()
    stale_node_ids = [
        node_id for node_id, device_type, last_seen in rows
        if not is_node_online(device_type, last_seen, now)
    ]
    if not stale_node_ids:
        return
    conn.executemany(
        "UPDATE node_heartbeat SET status='offline' WHERE node_id=?",
        [(node_id,) for node_id in stale_node_ids],
    )
    conn.commit()
