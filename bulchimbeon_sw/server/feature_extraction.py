"""
수계 주펌프 CT 파형에서 classical ML용 feature를 추출하는 모듈.

실제 배포시엔 이 계산이 ESP32에서 이뤄지고(원시 파형 대신 이 4개 숫자만 전송),
지금은 서버 시뮬레이션 단계라 파이썬으로 먼저 구현해 검증한다.
나중에 ESP32(C++)로 옮길 때 로직은 동일하게 유지할 것.
"""
import numpy as np


def compute_rms(waveform: np.ndarray) -> float:
    """전류 파형의 실효값(RMS)"""
    return float(np.sqrt(np.mean(np.square(waveform))))


def compute_peak(waveform: np.ndarray) -> float:
    """피크 전류"""
    return float(np.max(np.abs(waveform)))


def compute_duty_cycle(waveform: np.ndarray, threshold: float) -> float:
    """
    전류가 threshold 이상인 구간의 비율.
    펌프 기동 지속시간 비율(공회전은 짧고 불안정, 과부하는 길게 유지되는 경향)
    """
    active = np.abs(waveform) > threshold
    return float(np.mean(active))


def compute_cycle_interval(start_timestamps: list) -> float:
    """
    연속된 기동 시작 시각들의 평균 간격(초).
    충압펌프 미세누수 감지 로직에서 사용 — 간격이 짧아지면 누수 의심.
    """
    if len(start_timestamps) < 2:
        return float("nan")
    diffs = np.diff(sorted(start_timestamps))
    return float(np.mean(diffs))


def extract_features(waveform: np.ndarray, threshold: float = None) -> dict:
    """파형 1개 사이클에서 4개 feature를 한번에 추출"""
    if threshold is None:
        threshold = 0.3 * np.max(np.abs(waveform))
    return {
        "rms": compute_rms(waveform),
        "peak": compute_peak(waveform),
        "duty_cycle": compute_duty_cycle(waveform, threshold),
    }
