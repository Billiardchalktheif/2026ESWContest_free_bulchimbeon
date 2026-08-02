# 2026ESWContest_free_불침번
> **점검을 대체하지 않고, 점검 사이를 지킵니다.**
> 소방설비 법정 점검(연 1~2회) 사이의 상태 열화를 상시 센서로 감시해, 점검 사각지대를 메우는 AIoT 기반 소방설비 상태감시·점검보조 플랫폼입니다.

## docs/ — 설계·기획 자료
- `불침번_하드웨어_조감도_핀배치_v3.pdf` — 전체 시스템 구조도 + 모듈별 핀 배치 (가장 먼저 볼 자료)
- `AI_학습_데이터_수집_가이드.md` — AI 학습 시 데이터 수집 방법 초기 구상안
- `소프트웨어_구상안_초안.md` — 서버/AI/판정로직 아키텍처 초안
- `소프트웨어_구성안_v3.md` — 서버/AI/판정로직 아키텍처 최신안 (실제 코드는 이 기준)
- `개발_장애요인_해결방안_정리.md` — 개발 중 발생한 기술적 문제와 해결 과정 (CT클램프→INA219 교체 등)
- `개발_체크리스트.md` — 하드웨어 조립 및 실험 시 체크할 내역
- `발전가능성_확장방안_정리.md` — 실물 규모 확장 시 설계 변경 방향
- `불침번_팀원용_이해자료_최종.md` — 프로젝트 취지·로직을 쉽게 풀어쓴 내부 참고자료
- `앱_통합_가이드.md` — (폐기) 텔레그램 알림봇 + PWA 연동 초기안 — 네이티브 앱으로 방향 전환됨
- `bulchimbeon_animatic.html` — 애니매틱으로 만든 시연영상 초기 구성안

**시연영상 초기 구성안**: [바로 재생하기](https://billiardchalktheif.github.io/2026ESWContest_free_bulchimbeon/docs/bulchimbeon_animatic.html)

## guide/ — 실험·조립 가이드
- `테스트베드_조립가이드.md` — 5개 모듈 조립 순서 총정리
- `자탐_실험가이드.md` — 자탐1(차동식)·자탐2(광전식) 실험 프로토콜
- `수계_실험가이드.md` — 압력센서를 활용한 펌프성능시험의 주기적인 측정 및 전류센서(INA219)를 통한 충압펌프 작동 빈도 확인
- `가스계_실험가이드.md` — 로드셀을 이용한 가스저장용기의 무게 변화(공병중량 분리 계산) 확인
- `소화기_실험가이드.md` — 자이로 센서 및 게이트웨이와의 통신 연결 상태를 통해 도난/분실 확인
- `유도등_실험가이드.md` — 유도등 구동전압 탐색 및 배터리 방전 실험

## server/ — 실제 동작 코드
라즈베리파이에서 상시 구동되는 서버(수신·판정·AI·대시보드)와, 각 모듈의 ESP32에 올라가는 펌웨어(.ino)입니다.

| 파일 | 담당 모듈 | 비고 |
|---|---|---|
| `fire_alarm_differential_node.ino` | 자탐1 (차동식구역) | ADS1115+TS0202, 온도상승률 계산 |
| `fire_alarm_photoelectric_node.ino` | 자탐2 (광전식구역) | ADS1115+MQ-2+DHT22, AI 비화재보 판별 대상 |
| `pump_node_INA219.ino` | 수계 | INA219(I2C) 기반 — CT클램프는 DC 측정 불가로 폐기, 이 파일이 최신 수정본 |
| `gas_node.ino` | 가스계 (CO2) | HX711, NVS 기반 캘리브레이션 저장 |
| `extinguisher_leafnode.ino` | 소화기 리프노드 ×4 | ESP32-C3, MPU6500+딥슬립, ESP-NOW 송신 |
| `extinguisher_gateway.ino` | 소화기 게이트웨이 | ESP-NOW 수신 → WiFi/UDP 중계, 상시전원 |
| `evac_light_node.ino` | 유도등 | CD74HC4067 멀티플렉서, 데모모드(20분→45초 압축) 포함 |

*(라즈베리파이 서버 쪽 파이썬 코드는 추가되는 대로 이 표에 이어서 정리 예정)*

## simulation/ — 하드웨어 시뮬레이션 (Wokwi)
실물 부품 도착 전, 회로·로직을 미리 검증한 시뮬레이션입니다.

- **가스계(CO2) 모듈**: [Wokwi에서 열기](https://wokwi.com/projects/471159083371335681)
  - 소스: `simulation/가스계_wokwi/`
  - ⚠️ 시뮬레이션 한계: HX711 캘리브레이션 계수는 임시값 — 실물 로드셀 수령 후 실측 조정 예정

- **소화기 모듈**: [Wokwi에서 열기](https://wokwi.com/projects/471162307837300737)
  - 소스: `simulation/소화기_wokwi/`
  - ⚠️ 시뮬레이션 한계: 실물은 리프노드-게이트웨이 간 **ESP-NOW 무선통신**으로 연결상태를 판단하지만, Wokwi에서 무선 통신하는 보드 2개를 구현하기 어려워 **푸시버튼으로 연결끊김 상태를 대신 흉내냄**. 판정 로직(가속도 이벤트 → 연결상태 확인 → 이탈 확정)의 흐름 자체는 실물과 동일함

---

## 폴더 구조

- `docs/`
  - AI_학습_데이터_수집_가이드.md
  - bulchimbeon_animatic.html
  - 개발_장애요인_해결방안_정리.md
  - 개발_체크리스트.md
  - 발전가능성_확장방안_정리.md
  - 불침번_팀원용_이해자료_최종.md
  - 불침번_하드웨어_조감도_핀배치_v3.pdf
  - 소프트웨어_구상안_초안.md
  - 소프트웨어_구성안_v3.md
  - 앱_통합_가이드.md (폐기)

- `guide/`
  - 테스트베드_조립가이드.md
  - 자탐_실험가이드.md
  - 수계_실험가이드.md
  - 가스계_실험가이드.md
  - 소화기_실험가이드.md
  - 유도등_실험가이드.md

- `server/`
  - evac_light_node.ino
  - extinguisher_gateway.ino
  - extinguisher_leafnode.ino
  - fire_alarm_differential_node.ino
  - fire_alarm_photoelectric_node.ino
  - gas_node.ino
  - pump_node_INA219.ino
  - *(라즈베리파이 서버 파이썬 코드 추가 예정)*

- `simulation/`
  - 가스계_wokwi/
    - sketch.ino
    - diagram.json
    - libraries.txt
    - wokwi-project.txt
      
  - 소화기_wokwi/
    - sketch.ino
    - diagram.json
    - libraries.txt
    - wokwi-project.txt
