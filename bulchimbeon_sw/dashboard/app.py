"""
불침번 대시보드 — 5개 설비 최신 상태를 카드 형태로 보여주는 단일 페이지.

목적: 관리사가 한 화면에서 자탐/수계/가스계/소화기/유도등의 현재 상태와
노드 생사(heartbeat)를 바로 확인할 수 있게 한다.
디자인보다 정보 가독성 우선 — Flask + 서버사이드 렌더링 + 짧은 폴링 새로고침으로 충분.

사용법: python dashboard/app.py  (기본 포트 5000)
"""
import sqlite3
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

sys.path.append(str(Path(__file__).parent.parent / "server"))
from pump_performance_test import (  # noqa: E402
    classify_performance_result, describe_environment, last_test_freshness_days,
    mark_valve_state_manual,
)
from judge.regression import EVAC_MIN_DISCHARGE_MIN  # noqa: E402
from config import DB_PATH, LOOP_FIXED_OFFSET_OHM, is_node_online  # noqa: E402

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_heartbeats(conn):
    """전체 노드의 생사 상태. last_seen이 오래됐으면 화면에서도 offline으로 재판정
    (receiver/udp_listener.py의 5초 폴링 타이밍과 어긋나도 대시보드가 항상 최신으로 보이게).
    타임아웃 기준은 device_type마다 다르므로(가스계는 실배포 기준 1일 간격) config.py의
    is_node_online()을 packet_parser.py의 mark_offline_nodes()와 공유해서 쓴다 — 두 곳이
    따로 기준을 들고 있으면 서버 판정과 화면 표시가 어긋날 수 있기 때문."""
    rows = conn.execute(
        "SELECT node_id, device_type, last_seen, status FROM node_heartbeat ORDER BY device_type, node_id"
    ).fetchall()
    now = time.time()
    result = {}
    for r in rows:
        online = is_node_online(r["device_type"], r["last_seen"], now)
        result[r["node_id"]] = {
            "device_type": r["device_type"],
            "online": online,
            "seconds_ago": int(now - r["last_seen"]),
        }
    return result


def get_fire_alarm_cards(conn, heartbeats):
    """
    node_id별 최신 1건씩. v4: 자탐 2구역이 센서 구성이 완전히 달라(§3), zone_type에
    따라 카드에 보여줄 핵심 수치도 다르다 — differential(차동식)은 온도상승률(dT/dt),
    photoelectric(광전식)은 비화재보 판별 AI 결과(predicted_label/confidence)를 보여준다.
    루프저항 2층 판정(절대임계값+선형회귀)은 두 구역 공통이라 항상 표시한다.
    z-score 폐기 이후 loop_trend_slope_ohm_per_day(Ω/day 추세)/loop_rttf_days(잔여
    고장시간 예측)를 대신 노출한다 — baseline_ohm/z_score는 레거시 컬럼이라 더 이상
    새로 채워지지 않는다(server/storage/schema.sql 참고).

    카드 기본 화면엔 raw가 아니라 보정값(loop_resistance_corrected_ohm =
    raw - LOOP_FIXED_OFFSET_OHM)을 메인으로 보여준다 — 실제로 법정기준(50Ω)과
    비교되는 건 보정값이지 raw가 아니므로, raw를 기본으로 보여주면 관리사가
    "50Ω 넘었네 정상이네"를 raw 숫자만 보고 착각할 위험이 있다. raw/오프셋/계산식은
    평소엔 숨겨두고(<details>) 필요할 때만 펼쳐보게 한다(server/judge/regression.py의
    evaluate_loop_resistance와 동일한 보정 방식).
    """
    rows = conn.execute(
        """SELECT f.* FROM fire_alarm_log f
           INNER JOIN (
               SELECT node_id, MAX(id) AS max_id FROM fire_alarm_log GROUP BY node_id
           ) latest ON f.id = latest.max_id
           ORDER BY f.node_id"""
    ).fetchall()
    cards = []
    for r in rows:
        raw_ohm = r["loop_resistance_ohm"]
        # DB에는 원본(음수 가능) raw 값만 저장돼 있고, 회귀분석(loop_trend_slope_ohm_per_day/
        # loop_rttf_days)도 server/judge/regression.py 안에서 이 원본으로 계산된다 —
        # 그쪽은 절대 건드리지 않는다(2026-08-16: 회귀 입력을 0으로 클램핑했다가 비대칭
        # 왜곡으로 가짜 열화 추세가 생긴 사례 있음). 여기서는 오직 "사람이 보는 화면"에만
        # 쓸 값을 계산 직후 바로 클램핑한다 — 물리적으로 말이 안 되는 음수(-3.8Ω 등)가
        # 카드에 노출되지 않게 하기 위함이고, 이 클램핑은 DB나 다른 계산에 전혀 영향을
        # 주지 않는다.
        display_corrected_ohm = max(0, raw_ohm - LOOP_FIXED_OFFSET_OHM)

        # RTTF(잔여 고장시간 예측)는 "이미 법정기준을 넘어선 뒤에도 여전히 상승 중"인
        # 표본에서는 (50 - 보정저항)이 음수가 되어 "마이너스 며칠"처럼 보이는 문제가
        # 있었다 — 이미 넘은 상태에 "앞으로 며칠 남았다"는 표현 자체가 성립하지
        # 않으므로, 음수면 명시적으로 "이미 기준 초과"로 바꿔서 보여준다.
        rttf = r["loop_rttf_days"]
        if rttf is not None and rttf < 0:
            loop_rttf_display = "이미 기준 초과"
        elif rttf is not None:
            loop_rttf_display = f"{rttf:.1f}일"
        else:
            loop_rttf_display = "N/A"

        cards.append({
            "node_id": r["node_id"],
            "zone": r["zone"],
            "zone_type": r["zone_type"],
            "loop_resistance_ohm": raw_ohm,                        # 상세 펼침용 raw
            "loop_resistance_offset_ohm": LOOP_FIXED_OFFSET_OHM,   # 상세 펼침용 오프셋
            "loop_resistance_corrected_ohm": display_corrected_ohm,  # 기본 표시 — 화면용으로만 클램핑된 값
            "loop_trend_slope_ohm_per_day": r["loop_trend_slope_ohm_per_day"],
            "loop_rttf_days": rttf,
            "loop_rttf_display": loop_rttf_display,
            "status": r["status"],
            "temp_rise_rate": r["temp_rise_rate"],
            "mq2_raw": r["mq2_raw"],
            "humidity_pct": r["humidity_pct"],
            "nuisance_predicted_label": r["predicted_label"],
            "nuisance_confidence": r["confidence"],
            "heartbeat": heartbeats.get(r["node_id"]),
        })
    return cards


def _get_performance_test_summary(conn):
    """
    v3 §4: 성능시험(1차, 규칙기반) 최신 결과 + AI(2차) 판정과의 일치 여부.
    체절/부하는 별도 시점에 캡처되는 별개의 측정이라 각각 가장 최근 것을 따로 찾는다.
    "일치" 배지는 predicted_label(AI, 상시)과 label(성능시험 정답, 그 순간 확정)이
    같은 행(row)에 함께 채워진 가장 최근 성능시험 표본을 비교해서 만든다 — 두 판정이
    실패시 서로 대체하는 관계가 아니라 접점에서만 대조하는 관계임을 그대로 반영한다.
    """
    closed = conn.execute(
        """SELECT ts, pressure_kpa, rated_pressure_pct FROM water_pump_log
           WHERE pump_type='main' AND valve_state='closed' ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    open_ = conn.execute(
        """SELECT ts, pressure_kpa, rated_pressure_pct FROM water_pump_log
           WHERE pump_type='main' AND valve_state='open' ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    match_row = conn.execute(
        """SELECT label, predicted_label FROM water_pump_log
           WHERE pump_type='main' AND label_source='performance_test' AND predicted_label IS NOT NULL
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()

    return {
        "closed": dict(closed) if closed else None,
        "closed_result": classify_performance_result("closed", closed["rated_pressure_pct"]) if closed else None,
        "open": dict(open_) if open_ else None,
        "open_result": classify_performance_result("open", open_["rated_pressure_pct"]) if open_ else None,
        "ai_match": (match_row["label"] == match_row["predicted_label"]) if match_row else None,
        "last_test_days_ago": last_test_freshness_days(conn),
        "environment_desc": describe_environment(),
    }


def get_water_pump_card(conn, heartbeats):
    main = conn.execute(
        "SELECT * FROM water_pump_log WHERE pump_type='main' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    jockey = conn.execute(
        "SELECT * FROM water_pump_log WHERE pump_type='jockey' ORDER BY id DESC LIMIT 1"
    ).fetchone()

    history_rows = conn.execute(
        """SELECT ts, COALESCE(predicted_label, label) AS shown_label, confidence
           FROM water_pump_log WHERE pump_type='main' AND COALESCE(predicted_label, label) IS NOT NULL
           ORDER BY id DESC LIMIT 30"""
    ).fetchall()
    history = list(reversed(history_rows))

    # v7: 라벨 5클래스 체계(server/pump_performance_test.py 참고). flow_reduced/
    # startup_failure는 지금 실험 조건으로는 안 나오는 클래스라 항상 0으로 뜨는 게 정상.
    label_counts = {
        "normal_operation": 0, "stall_operation": 0, "dry_run": 0,
        "flow_reduced": 0, "startup_failure": 0,
    }
    for r in history:
        if r["shown_label"] in label_counts:
            label_counts[r["shown_label"]] += 1

    return {
        "main": dict(main) if main else None,
        "main_shown_label": (main["predicted_label"] or main["label"]) if main else None,
        "jockey": dict(jockey) if jockey else None,
        "history": [dict(r) for r in history],
        "label_counts": label_counts,
        "heartbeat_main": heartbeats.get(main["node_id"]) if main else None,
        "heartbeat_jockey": heartbeats.get(jockey["node_id"]) if jockey else None,
        "performance_test": _get_performance_test_summary(conn),
    }


def get_gas_cards(conn, heartbeats):
    rows = conn.execute(
        """SELECT g.* FROM gas_log g
           INNER JOIN (
               SELECT node_id, MAX(id) AS max_id FROM gas_log GROUP BY node_id
           ) latest ON g.id = latest.max_id
           ORDER BY g.node_id"""
    ).fetchall()
    cards = []
    for r in rows:
        cards.append({
            "node_id": r["node_id"],
            "zone": r["zone"],
            "gas_type": r["gas_type"],
            "weight_g": r["weight_g"],
            "initial_weight_g": r["initial_weight_g"],
            "loss_pct": r["loss_pct"],
            "predicted_days_to_5pct": r["predicted_days_to_5pct"],
            "heartbeat": heartbeats.get(r["node_id"]),
        })
    return cards


# 소화기는 압력이 아니라 이탈감지 상태를 보여준다(v2 설계 변경 — server/judge/rules.py 참고)
EXTINGUISHER_STATUS_LABELS = {
    "normal": "정상",
    "moved": "이동 감지(확인 중)",
    "missing": "이탈 확정",
}


def get_extinguisher_cards(conn, heartbeats):
    rows = conn.execute(
        """SELECT e.* FROM extinguisher_log e
           INNER JOIN (
               SELECT node_id, MAX(id) AS max_id FROM extinguisher_log GROUP BY node_id
           ) latest ON e.id = latest.max_id
           ORDER BY e.node_id"""
    ).fetchall()
    cards = []
    for r in rows:
        cards.append({
            "node_id": r["node_id"],
            "zone": r["zone"],
            "accel_magnitude": r["accel_magnitude"],
            "gateway_id": r["gateway_id"],
            "status": r["status"],
            "status_label": EXTINGUISHER_STATUS_LABELS.get(r["status"], r["status"]),
            "heartbeat": heartbeats.get(r["node_id"]),
        })
    return cards


def get_evac_light_cards(conn, heartbeats):
    """
    v3: predicted_days_to_replace(느린 달력추세)와 estimated_discharge_min(데모모드
    실측 방전시험)은 서로 다른 질문에 답하는 값이라 절대 섞지 않는다(§1 버그수정).
    후자는 실측 순간에만 갱신되므로, "최신 행"이 아니라 "estimated_discharge_min이
    NULL이 아닌 가장 최근 행"을 별도로 찾아 "마지막 측정: N일 전"과 함께 보여준다 —
    그래야 실시간 값이 아니라 최근 실측값이라는 게 화면에서 명확해진다.
    """
    rows = conn.execute(
        """SELECT v.* FROM evac_light_log v
           INNER JOIN (
               SELECT node_id, MAX(id) AS max_id FROM evac_light_log GROUP BY node_id
           ) latest ON v.id = latest.max_id
           ORDER BY v.node_id"""
    ).fetchall()

    now = time.time()
    cards = []
    for r in rows:
        node_id = r["node_id"]
        last_test = conn.execute(
            """SELECT ts, estimated_discharge_min, status FROM evac_light_log
               WHERE node_id=? AND estimated_discharge_min IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (node_id,),
        ).fetchone()

        cards.append({
            "node_id": node_id,
            "zone": r["zone"],
            "battery_voltage": r["battery_voltage"],
            "lux": r["lux"],
            "predicted_days_to_replace": r["predicted_days_to_replace"],
            "last_test_estimated_min": last_test["estimated_discharge_min"] if last_test else None,
            "last_test_status": last_test["status"] if last_test else None,
            # 더미 시뮬레이션은 ts를 미래 방향으로 미리 증가시켜 보내기도 해서 아주 드물게
            # 음수가 나올 수 있다(pump_performance_test.last_test_freshness_days와 동일 이슈) —
            # "N일 전" 표시가 음수로 보이지 않게 0 이상으로 clamp.
            "last_test_days_ago": max((now - last_test["ts"]) / 86400.0, 0.0) if last_test else None,
            "demo_mode": bool(r["demo_mode"]),
            "heartbeat": heartbeats.get(node_id),
        })
    return cards


@app.route("/")
def index():
    conn = get_db()
    try:
        heartbeats = get_heartbeats(conn)
        context = {
            "fire_alarm_cards": get_fire_alarm_cards(conn, heartbeats),
            "water_pump": get_water_pump_card(conn, heartbeats),
            "gas_cards": get_gas_cards(conn, heartbeats),
            "extinguisher_cards": get_extinguisher_cards(conn, heartbeats),
            "evac_light_cards": get_evac_light_cards(conn, heartbeats),
            "evac_min_discharge_min": EVAC_MIN_DISCHARGE_MIN,
        }
    finally:
        conn.close()
    return render_template("index.html", **context)


@app.route("/pump/manual_valve_trigger", methods=["POST"])
def pump_manual_valve_trigger():
    """
    §5-2 대안: 압력 자동추정(§5-1, 기본으로 사용 중)의 신뢰도가 낮을 경우를 대비한
    수동 트리거. 발표자가 니들밸브를 잠근/연 직후 이 버튼을 누르면, receiver/packet_parser.py가
    다음 water_pump 'main' 패킷 하나에 그 값을 그대로 적용한다
    (server/pump_performance_test.py의 determine_valve_state 참고).
    """
    valve_state = request.form.get("valve_state")
    if valve_state not in ("closed", "open"):
        return jsonify({"ok": False, "error": "valve_state는 closed 또는 open만 가능"}), 400
    mark_valve_state_manual(valve_state)
    return jsonify({"ok": True, "valve_state": valve_state})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
