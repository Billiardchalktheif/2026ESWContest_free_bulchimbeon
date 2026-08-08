"""
불침번 현장 출력 채널 — 부저 + LCD.

v7 폴더 재편으로 alert_output.py에서 이 위치(server/dispatch/lcd_buzzer_output.py)로
옮기며 내용도 정리했다:
  - LED -> **LCD**(16x2/20x4, I2C)로 교체. LED는 "정상/주의/경보" 3색 표시만 가능했지만
    LCD는 "어느 설비의 어느 구역이 왜 경보인지"를 텍스트로 바로 보여줄 수 있어 시연에서
    더 실용적이다.
  - **이메일(SMTP) 발송 코드는 제거했다.** 원격 알림(앱 팝업/푸시 등)은 이제 별도
    팀원이 진행하는 네이티브 앱 쪽 책임이다 — 그 팀원의 api.py가 완성되면 서버 쪽
    연결 작업을 별도로 진행할 예정이며, 지금은 이 서버가 원격 알림을 직접 보내지
    않는다. 텔레그램·PWA도 마찬가지로 채택하지 않는다.

배경(팀 이해자료 §5): 원래 기획엔 "화면 대시보드 / 경고음 / 스마트폰 알림" 3중 출력이
있었다. 대시보드(시각)는 이미 있고, 이 파일은 그중 "현장(청각+시각 텍스트)" 담당이다
— 경보 시 실제 소리·화면으로 임베디드 시스템의 실체를 보여주는 역할.

설계: 모든 설비(자탐1/2, 수계, 가스계, 소화기, 유도등)의 경보 상태 전환 시점은
반드시 이 파일의 trigger_alert() 하나만 거치도록 통일한다 — 각 판정 모듈
(server/judge/regression.py, server/judge/rules.py, server/judge/classify.py,
server/pump_performance_test.py)이 부저/LCD 제어 코드를 저마다 따로 들고 있지 않게
하기 위함이다. 반복 알림 억제(쿨다운)도 이 함수 안에서 중앙집중적으로 처리한다 —
그래야 호출하는 쪽에서는 "지금 알림을 보낼 만한 상황인지"만 판단하면 되고, "너무
자주 보내진 않는지"는 신경 쓸 필요가 없다.

실물(부저/LCD)이 연결 안 된 개발 환경에서는 자동으로 콘솔 로그로 대체된다 — 그래야
개발 PC에서도 서버가 죽지 않고 계속 검증할 수 있다.
"""
import threading
import time

SERVER_DIR_NAME = __name__  # (미사용, 그대로 두면 flake8 무해)

# ---- 반복 알림 억제(쿨다운) ----
# 같은 (device, zone)에 대해 이 시간 안에는 재알림하지 않는다. z-score의 caution이나
# 성능시험 경보처럼 같은 원인으로 여러 패킷에 걸쳐 계속 "경보 조건"이 유지되는 경우,
# 쿨다운이 없으면 그 기간 내내 패킷마다 부저/LCD가 반복 갱신되는 스팸이 된다. 진짜
# 사고라면 쿨다운이 지난 뒤 다시 알림이 가므로 놓치지 않는다.
ALERT_COOLDOWN_SEC = 300  # 5분 — 실측 후 조정 가능

# ---- 라즈베리파이 GPIO 핀 배치 (BCM 번호). 배선 여건에 따라 재조정 가능 ----
BUZZER_PIN = 17
BUZZER_ON_DURATION_SEC = 1.0  # 부저 울리는 시간 — 논블로킹으로 이 시간 뒤 자동으로 끔

# ---- I2C LCD 설정 ----
LCD_I2C_ADDRESS = 0x27   # PCF8574 백팩 흔한 기본 주소 — 실측 후 i2cdetect로 확인/교체
LCD_COLS = 16
LCD_ROWS = 2
LCD_DISPLAY_DURATION_SEC = 5.0  # 경보 메시지를 LCD에 띄워두는 시간 — 지나면 평상시 화면 복귀
LCD_BLINK_DELAY_SEC = 0.3       # 경보 시 백라이트를 짧게 껐다 켜서 "깜빡임" 효과를 줌

DEVICE_LABELS = {
    "fire_alarm": "자탐",
    "water_pump": "수계",
    "gas": "가스계",
    "extinguisher": "소화기",
    "evac_light": "유도등",
}

_last_alert_time: dict = {}  # (device, zone) -> 마지막 알림 시각(time.time())

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    # 개발 PC(라즈베리파이가 아닌 환경)에서는 항상 이 경로를 탄다 — 콘솔 로그로만 대체하고
    # 서버가 죽지 않게 한다. 실물 라즈베리파이에 배포하면 자동으로 GPIO 경로가 활성화된다.
    _GPIO_AVAILABLE = False

try:
    from RPLCD.i2c import CharLCD
    _LCD_LIB_AVAILABLE = True
except ImportError:
    _LCD_LIB_AVAILABLE = False

_gpio_initialized = False
_lcd = None
_lcd_init_attempted = False


def _ensure_gpio_initialized():
    global _gpio_initialized
    if _gpio_initialized or not _GPIO_AVAILABLE:
        return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUZZER_PIN, GPIO.OUT, initial=GPIO.LOW)
        _gpio_initialized = True
    except Exception as e:
        print(f"[WARN] GPIO 초기화 실패(배선 확인 필요): {e}")


def _get_lcd():
    """LCD를 최초 1회만 초기화해 캐싱한다. 실패하거나 라이브러리가 없으면 None."""
    global _lcd, _lcd_init_attempted
    if _lcd_init_attempted:
        return _lcd
    _lcd_init_attempted = True
    if not _LCD_LIB_AVAILABLE:
        return None
    try:
        _lcd = CharLCD(i2c_expander="PCF8574", address=LCD_I2C_ADDRESS, cols=LCD_COLS, rows=LCD_ROWS)
        _show_idle_message()
    except Exception as e:
        print(f"[WARN] LCD 초기화 실패(배선/I2C 주소 확인 필요): {e}")
        _lcd = None
    return _lcd


def _show_idle_message():
    """평상시 화면 — 경보 표시 시간(LCD_DISPLAY_DURATION_SEC)이 지나면 여기로 복귀한다."""
    lcd = _lcd
    if lcd is None:
        return
    try:
        lcd.clear()
        lcd.write_string("Bulchimbeon")
        lcd.crlf()
        lcd.write_string("5 devices OK")
    except Exception:
        pass  # 화면 갱신 실패는 알림 자체를 막을 이유가 안 됨(무시하고 계속 동작)


def _turn_off_buzzer():
    """BUZZER_ON_DURATION_SEC 뒤 threading.Timer로 호출됨"""
    try:
        GPIO.output(BUZZER_PIN, GPIO.LOW)
    except Exception:
        pass  # 종료 시점에 GPIO가 이미 정리됐을 수 있음 — 무시해도 안전


def _blink_lcd_backlight(lcd):
    """짧게 백라이트를 껐다 켜서 "지금 막 갱신됐다"는 걸 시각적으로 알린다."""
    try:
        lcd.backlight_enabled = False

        def _turn_on():
            try:
                lcd.backlight_enabled = True
            except Exception:
                pass

        threading.Timer(LCD_BLINK_DELAY_SEC, _turn_on).start()
    except Exception:
        pass


def _trigger_buzzer(severity: str):
    """
    청각 알림. caution은 부저를 울리지 않는다 — z-score caution은 정상 구역에서도
    노이즈로 종종 뜨는 수준이라(server/judge/regression.py 참고) 부저까지 울리면
    너무 과민한 시스템으로 보인다. alarm급 이상만 울린다.
    delay()/sleep() 없이 threading.Timer로 자동 소등해 receiver/udp_listener.py의
    메인 수신 루프를 블로킹하지 않는다(다른 ESP32 노드의 논블로킹 설계와 동일한 원칙).
    """
    if severity == "caution":
        return
    if not _GPIO_AVAILABLE:
        print("[알림-부저] (GPIO 없는 개발환경 - 콘솔로 대체)")
        return
    _ensure_gpio_initialized()
    try:
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        threading.Timer(BUZZER_ON_DURATION_SEC, _turn_off_buzzer).start()
    except Exception as e:
        print(f"[WARN] 부저 트리거 실패: {e}")


def _update_lcd(device: str, zone: str, message: str, severity: str):
    """
    LCD에 "어느 설비/구역이 왜 경보인지"를 텍스트로 표시한다. 표시 시간이 지나면
    자동으로 평상시 화면으로 복귀한다(threading.Timer, 메인 루프 블로킹 없음).
    """
    lcd = _get_lcd()
    if lcd is None:
        print(f"[알림-LCD] (LCD 없는 개발환경 - 콘솔로 대체) {device}/{zone}: {message}")
        return
    device_label = DEVICE_LABELS.get(device, device)
    try:
        lcd.clear()
        lcd.write_string(f"{device_label} {severity.upper()}"[:LCD_COLS])
        lcd.crlf()
        lcd.write_string(zone[:LCD_COLS])
        _blink_lcd_backlight(lcd)
        threading.Timer(LCD_DISPLAY_DURATION_SEC, _show_idle_message).start()
    except Exception as e:
        print(f"[WARN] LCD 갱신 실패: {e}")


def trigger_alert(device: str, zone: str, message: str, severity: str = "alarm"):
    """
    모든 설비 판정 모듈이 경보 상태 전환 시점에 호출하는 단일 진입점.

    device: 'fire_alarm' / 'water_pump' / 'gas' / 'extinguisher' / 'evac_light'
    zone: 사람이 읽을 구역/노드 이름 (예: "광전식구역", "1층 복도", "pump_main_01")
    message: 알림 본문 — 무슨 일이 있었는지 한국어로 짧게 (콘솔 로그·LCD 대체 표시에 쓰임)
    severity: 'caution'(부저 없이 LCD만) 또는 'alarm'(부저+LCD 전부)
    """
    key = (device, zone)
    now = time.time()
    if now - _last_alert_time.get(key, 0) < ALERT_COOLDOWN_SEC:
        return  # 쿨다운 중 — 같은 원인으로 반복 알림 억제
    _last_alert_time[key] = now

    print(f"[ALERT:{severity}] {device}/{zone}: {message}")
    _trigger_buzzer(severity)
    _update_lcd(device, zone, message, severity)
