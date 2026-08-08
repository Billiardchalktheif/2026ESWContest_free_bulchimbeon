"""
수계 주펌프 — 성능시험(1차, 규칙기반) 판정 + AI 학습라벨 자동 부여 (v3 §4 핵심).

배경: 기존 CT클램프 파형분류(AI) 하나만으로는 "정상/과부하/공회전" 학습 데이터를
사람이 추측으로 라벨링해야 했다 — 미니어처 테스트베드에서 이 3상태를 재현 가능하고
신뢰할 수 있게 만들기 어렵다는 근본적 약점이 있었다. 반대로 성능시험(압력 기준)만
쓰면 법정기준(140%/65%)과 직접 비교 가능해 결과가 명확하지만, 그것만 하면 AI
적용처가 프로젝트 전체에서 0개가 되어 "법정시험의 단순 자동화"로 비칠 위험이 있다.

결합안: 밸브로 압력 상태를 확정적으로 만들고, 그 순간의 CT클램프 전류 파형에 그
압력 상태(valve_state)를 정답 라벨로 자동으로 붙인다. 즉 성능시험이 "AI 학습
데이터의 신뢰성 문제"를 해결하는 도구가 된다.
  - valve_state='closed' (체절운전) -> label='stall_operation'(체절운전)으로 매칭
  - valve_state='open'   (부하/정격토출) -> label='normal_operation'(정상운전)으로 매칭
  - valve_state='dryrun' (흡입측 차단/탱크 비움, 밸브 개폐와 무관한 별도 트리거) -> label='dry_run'(공회전)
  (v7: 라벨 5클래스 체계 — 상세 근거는 VALVE_STATE_TO_LABEL 정의부 주석 참고)

⚠️ v4 정정: 밸브는 솔레노이드(자동)가 아니라 **니들밸브(수동)** 다 — 발표자가 시연
중 직접 손으로 잠그고 연다(§5). 그래서 ESP32/서버는 "지금이 체절인지 부하인지"를
직접 통보받을 방법이 없다. 대신 아래 두 가지 방식으로 valve_state를 알아낸다
(우선순위 순, determine_valve_state()가 이 순서로 시도한다):
  1) infer_valve_state_from_pressure() — 압력값 패턴 기반 자동 추정(권장). 압력이
     급격히 올라 일정 시간 이상 고압을 유지하면 체절 진입으로, 다시 정상범위로
     떨어지면 부하/평상 복귀로 추정한다. 사람이 버튼을 누를 필요가 없다.
  2) consume_manual_valve_trigger() — 대시보드 수동 트리거(대안). 1번의 신뢰도가
     낮을 경우를 대비해 분리해뒀다. 발표자가 밸브를 잠근/연 직후 대시보드에서
     버튼을 누르면 그 값이 다음 패킷 하나에 반영된다.
  dryrun만 예외 — 흡입측 차단은 압력 패턴과 무관한 별도 물리 조작이라, ESP32가
  시리얼 명령으로 명시적으로 보내는 valve_state="dryrun"을 그대로 사용한다.

⚠️ 두 판정의 관계 (v3 패치로 명확화 — 절대 순차/대체 관계로 구현하지 말 것):
  - AI 판정(2차, server/judge/classify.py)은 성능시험 여부와 무관하게 항상 상시로 돈다.
    이게 이 프로젝트의 존재 이유(점검 사이 사각지대를 상시 센서로 메움)다.
  - 성능시험(1차, 이 파일)은 밸브를 인위적으로 조작해야 하므로 상시로 돌릴 수 없고,
    정해진 주기에만 잠깐 실행된다 (아래 ENVIRONMENT 참고).
  - 둘은 "성능시험이 실패하면 AI로 넘어간다" 같은 관계가 아니라, 성능시험이 도는
    그 짧은 순간에만 같은 행(row)에 두 값(label/predicted_label)이 함께 채워져
    "겹쳐서 대조"된다 — 이 대조는 AI 모델의 신뢰도를 검증하기 위함이지, AI가 뭔가를
    대신 처리해주는 게 아니다. 일치하면 신뢰도 확인 배지, 불일치하면 재점검 플래그.

실행 주기 — 테스트베드 vs 실배포:
  실제 소방펌프를 자주 체절/정격토출 시험하면 펌프 마모·수도비용이 누적되고, 법정
  종합점검 자체가 연 1~2회로 정해져 있다(매뉴얼 기준). 반면 테스트베드는 개발 기간
  내 AI 학습데이터를 충분히 확보해야 하므로 훨씬 자주 돌려도 무방하다. 이 주기는
  ESP32(pump_node.ino)가 자체 타이머로 실행하며, 여기 상수는 그 주기를 문서화하고
  대시보드에서 "마지막 시험이 그 주기 대비 너무 오래됐는지"를 보여주는 데 쓰인다.
"""
import json
import time
from pathlib import Path

from dispatch.lcd_buzzer_output import trigger_alert

# ---- 성능시험 실행 주기 (환경별 분리 — 하드코딩된 단일 주기 금지) ----
ENVIRONMENT = "testbed"  # "testbed" | "deployment"
TESTBED_TRIGGER_INTERVAL_HOURS = 6       # 개발/학습데이터 수집 단계 — 자주 실행
DEPLOYMENT_TRIGGER_INTERVAL_DAYS = 180   # 실배포 가정 — 법정 종합점검 주기(연 1~2회)에 근접


def get_trigger_interval_sec() -> float:
    """현재 ENVIRONMENT 설정에 따른 성능시험 실행 주기(초)를 반환"""
    if ENVIRONMENT == "deployment":
        return DEPLOYMENT_TRIGGER_INTERVAL_DAYS * 86400.0
    return TESTBED_TRIGGER_INTERVAL_HOURS * 3600.0


def describe_environment() -> str:
    """대시보드/보고서에 표시할 한 줄 설명"""
    if ENVIRONMENT == "deployment":
        return f"배포 모드 — {DEPLOYMENT_TRIGGER_INTERVAL_DAYS}일 간격(법정 종합점검 주기)"
    return f"테스트베드 모드 — {TESTBED_TRIGGER_INTERVAL_HOURS}시간 간격(학습데이터 수집 목적, 실배포 방식 아님)"


# ---- 법정기준(§4) — 매직넘버 금지, 실측 후 조정 가능하게 상수로 분리 ----
RATED_PRESSURE_KPA = 700.0        # 정격토출압력(설치시 캘리브레이션 값) — 실측 후 교체
CHOKED_PRESSURE_LIMIT_PCT = 140.0  # 체절운전: 정격토출압력의 140% 이하가 정상
LOAD_PRESSURE_MIN_PCT = 65.0       # 정격토출량 150% 운전: 정격토출압력의 65% 이상이 정상

# v7: 라벨 5클래스 체계로 재정의. 기존 3클래스("정상/과부하/공회전")는 펌프 종류에 따라
# 체절 시 전류가 오히려 낮게 나올 수도 있다는 불확실성을 라벨명 자체가 감추고 있었다
# ("과부하"라는 이름이 이미 "전류가 높다"는 결론을 전제하고 있었음). 그래서 라벨명이
# "밸브 상태(전류 높낮이 예단 없이 물리적으로 확인된 실험 상태)"만 그대로 가리키도록
# 바꿨다 — 체절 시 전류가 실측으로 낮게 나오더라도 stall_operation이라는 라벨명 자체는
# 안 바뀌므로, 실측 결과와 무관하게 라벨 체계를 다시 안 짜도 되는 게 이 변경의 핵심 이점.
# flow_reduced(유량저하)/startup_failure(기동실패)는 지금 3가지 실험 조건(체절/부하/공회전)
# 으로는 재현되지 않는 클래스라 매핑에 없음 — 나중에 부분개도 등 추가 실험 조건이 생기면
# 그때 새 valve_state 키와 함께 채워질 자리로 비워둔다. RandomForest는 클래스 수에 자동
# 대응되므로(ml/train_pump_classifier.py 참고) 그 시점에 이 dict만 확장하면 된다.
VALVE_STATE_TO_LABEL = {
    "closed": "stall_operation",   # 체절운전 — 밸브 완전잠금 상태
    "open": "normal_operation",    # 정상운전
    "dryrun": "dry_run",           # 공회전
}

# ---- v4 §5-1: 압력값 기반 밸브 상태 자동추정 (권장안) ----
# 니들밸브가 수동이라 ESP32가 직접 알려줄 수 없으므로, 최근 압력값이 연속으로
# 어느 구간에 있는지를 보고 서버가 스스로 추정한다. 실측 후 조정 가능하게 상수로 분리.
VALVE_AUTO_DETECT_MIN_SUSTAINED_SAMPLES = 3   # 이 개수만큼 연속으로 같은 구간이어야 확정
VALVE_AUTO_DETECT_CLOSED_PCT_THRESHOLD = 102.0  # 정격 대비 이 %를 초과 유지하면 체절로 추정
VALVE_AUTO_DETECT_IDLE_PCT_THRESHOLD = 20.0     # 정격 대비 이 %미만이면 "펌프 정지"로 간주
                                                 # (시험 데이터 아님 — 이 문턱이 없으면 펌프가
                                                 # 그냥 꺼져있는 평상시 압력 0을 "부하 통과"로
                                                 # 잘못 라벨링하게 된다)

# ---- v4 §5-2: 대시보드 수동 트리거 (대안) ----
# 1번(자동추정)의 신뢰도가 낮을 경우를 대비해 분리해뒀다. receiver/packet_parser.py와
# dashboard/app.py는 서로 다른 프로세스라 메모리를 공유하지 못하므로, 이 작은 파일을 통해
# 신호를 주고받는다. v7: db/ 폴더가 없어지고 server/storage/로 통합되면서 경로도 함께 옮김.
MANUAL_TRIGGER_PATH = Path(__file__).parent / "storage" / "_pump_manual_valve_trigger.json"
MANUAL_TRIGGER_FRESHNESS_SEC = 120  # 이 시간 안에 들어온 다음 패킷에만 수동 트리거를 적용


def classify_performance_result(valve_state, rated_pressure_pct):
    """
    체절/부하 각각 다른 법정기준(§4)과 비교해 통과/경보 문자열을 반환한다.
    대시보드가 그대로 재사용할 수 있도록 여기 한 곳에만 판정 로직을 둔다.
    dryrun 등 압력 기준이 없는 상태거나 압력값이 없으면 None.
    """
    if rated_pressure_pct is None:
        return None
    if valve_state == "closed":
        return "경보(140% 초과)" if rated_pressure_pct > CHOKED_PRESSURE_LIMIT_PCT else "통과"
    if valve_state == "open":
        return "경보(65% 미만)" if rated_pressure_pct < LOAD_PRESSURE_MIN_PCT else "통과"
    return None


def infer_valve_state_from_pressure(conn, node_id: str, pressure_kpa):
    """
    §5-1(권장안): 니들밸브가 수동이라 지금이 체절인지 부하인지 ESP32/서버가 직접
    알 방법이 없다. 그래서 최근 압력값이 연속으로 어느 구간에 있었는지를 보고
    스스로 추정한다:
      - 최근 VALVE_AUTO_DETECT_MIN_SUSTAINED_SAMPLES개가 전부 CLOSED_PCT_THRESHOLD
        초과 -> "closed"(체절 진입 추정)
      - 전부 IDLE_PCT_THRESHOLD 이상 ~ CLOSED_PCT_THRESHOLD 이하(펌프가 켜진 채
        정상 부하 범위) -> "open"(부하/평상 복귀 추정)
      - 그 외(샘플 부족, 구간이 섞여있는 전환 중, 또는 펌프 자체가 꺼져 있어
        IDLE_PCT_THRESHOLD 미만인 경우) -> None, 즉 "성능시험 데이터 아님"으로 처리.
        IDLE 문턱이 없으면 펌프가 그냥 꺼져있는 평상시 압력 0을 "부하 통과"로
        잘못 라벨링하게 되므로 반드시 필요하다.
    """
    if pressure_kpa is None:
        return None
    current_pct = pressure_kpa / RATED_PRESSURE_KPA * 100.0

    rows = conn.execute(
        """SELECT pressure_kpa FROM water_pump_log
           WHERE node_id=? AND pump_type='main' AND pressure_kpa IS NOT NULL
           ORDER BY id DESC LIMIT ?""",
        (node_id, VALVE_AUTO_DETECT_MIN_SUSTAINED_SAMPLES - 1),
    ).fetchall()
    recent_pcts = [p / RATED_PRESSURE_KPA * 100.0 for (p,) in rows] + [current_pct]

    if len(recent_pcts) < VALVE_AUTO_DETECT_MIN_SUSTAINED_SAMPLES:
        return None  # 아직 판단할 만큼 이력이 쌓이지 않음

    window = recent_pcts[-VALVE_AUTO_DETECT_MIN_SUSTAINED_SAMPLES:]
    if all(p > VALVE_AUTO_DETECT_CLOSED_PCT_THRESHOLD for p in window):
        return "closed"
    if all(VALVE_AUTO_DETECT_IDLE_PCT_THRESHOLD <= p <= VALVE_AUTO_DETECT_CLOSED_PCT_THRESHOLD for p in window):
        return "open"
    return None  # 전환 중이거나 펌프 정지 상태 — 확정하지 않음


def mark_valve_state_manual(valve_state: str):
    """
    §5-2(대안): 대시보드에서 발표자가 "체절시험 시작"/"부하시험 시작" 버튼을 누르면
    호출된다. receiver/packet_parser.py(별도 프로세스)가 다음 패킷 처리 시 이 값을 읽어갈 수
    있도록 작은 파일에 기록해둔다 — 자동추정(§5-1)의 신뢰도가 낮다고 판단되면
    determine_valve_state()에서 이 값을 우선 사용하도록 이미 구조가 분리돼 있다.
    """
    MANUAL_TRIGGER_PATH.write_text(
        json.dumps({"valve_state": valve_state, "ts": time.time()}), encoding="utf-8"
    )


def consume_manual_valve_trigger():
    """
    아직 소비되지 않은(=MANUAL_TRIGGER_FRESHNESS_SEC 이내에 눌린) 수동 트리거가
    있으면 그 valve_state를 반환하고 파일을 지워서 한 번만 적용되게 한다.
    없거나 너무 오래됐으면 None.
    """
    if not MANUAL_TRIGGER_PATH.exists():
        return None
    try:
        data = json.loads(MANUAL_TRIGGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    finally:
        MANUAL_TRIGGER_PATH.unlink(missing_ok=True)

    if time.time() - data.get("ts", 0) > MANUAL_TRIGGER_FRESHNESS_SEC:
        return None
    return data.get("valve_state")


def determine_valve_state(conn, node_id: str, pressure_kpa):
    """
    valve_state를 알아내는 단일 진입점 — §5-2(수동 트리거)를 먼저 확인하고,
    없으면 §5-1(압력 자동추정)로 넘어간다. receiver/packet_parser.py는 이 함수 하나만
    호출하면 된다(우선순위 전환은 이 함수 안에서만 처리).
    """
    manual = consume_manual_valve_trigger()
    if manual is not None:
        return manual
    return infer_valve_state_from_pressure(conn, node_id, pressure_kpa)


def evaluate_pump_performance_sample(conn, row_id: int, valve_state: str, pressure_kpa,
                                      node_id: str = None):
    """
    성능시험 판정 대상(valve_state가 확정된) 패킷 하나에 대해:
      1) rated_pressure_pct(정격 대비 %) 계산
      2) valve_state -> label 자동 변환, label_source='performance_test' 기록
    을 수행한다. valve_state는 v4부터 ESP32가 보낸 값이 아니라 determine_valve_state()가
    추정/수동트리거로 알아낸 값일 수 있어서(§5), INSERT 시점엔 비어있었을 수 있으므로
    여기서 다시 한번 확실히 채워 넣는다. AI 추론(predicted_label)은 이 함수가 아니라
    receiver/packet_parser.py에서 이 함수와 별개로(상시) 실행되므로 여기서는 손대지 않는다 —
    두 판정을 섞지 않는 게 이 모듈의 핵심 원칙(파일 상단 설명 참고).

    법정기준(140%/65%) 경보면 공통 출력 채널로 알림을 보낸다. water_pump_log에는
    zone 컬럼이 없어서(다른 설비와 달리 구역 개념이 없음) node_id를 알림 식별자로 쓴다.
    """
    rated_pct = (pressure_kpa / RATED_PRESSURE_KPA * 100.0) if pressure_kpa is not None else None
    label = VALVE_STATE_TO_LABEL.get(valve_state)

    conn.execute(
        """UPDATE water_pump_log
           SET valve_state=?, rated_pressure_pct=?, label=?, label_source='performance_test'
           WHERE id=?""",
        (valve_state, rated_pct, label, row_id),
    )
    conn.commit()

    result = classify_performance_result(valve_state, rated_pct)
    if result is not None and result.startswith("경보"):
        print(f"[WARN] 수계 성능시험 경보 - valve_state={valve_state}, "
              f"rated_pressure_pct={rated_pct:.1f}% ({result})")
        trigger_alert(
            "water_pump", node_id or "pump_main_01",
            f"성능시험 경보 - valve_state={valve_state}, rated_pressure_pct={rated_pct:.1f}% ({result})",
            severity="alarm",
        )


def last_test_freshness_days(conn) -> float:
    """
    가장 최근 성능시험(label_source='performance_test') 시각으로부터 며칠이 지났는지 반환.
    대시보드의 "마지막 성능시험 실행: N일 전" 표시 및 주기 대비 지연 여부 확인에 쓰인다.
    시험 이력이 아예 없으면 None.
    """
    row = conn.execute(
        "SELECT MAX(ts) FROM water_pump_log WHERE label_source='performance_test'"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    # 더미 시뮬레이션은 ts를 미래 방향으로 미리 증가시켜 보내기도 해서(실제 하드웨어는
    # 그럴 일 없음) 아주 드물게 음수가 나올 수 있다 — "N일 전" 표시가 음수로 보이는
    # 어색함을 막기 위해 0 이상으로 clamp.
    return max((time.time() - row[0]) / 86400.0, 0.0)
