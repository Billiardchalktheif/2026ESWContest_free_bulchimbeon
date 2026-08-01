# 2026ESWContest_free_불침번

## docs/ — 설계·기획 자료
- `불침번_하드웨어_조감도_핀배치_v3.pdf` — 전체 시스템 구조도 + 모듈별 핀 배치 (가장 먼저 볼 자료)
- `소프트웨어_구상안.md` — 서버/AI/판정로직 아키텍처
- `개발_장애요인_해결방안_정리.md` — 개발 중 발생한 기술적 문제와 해결 과정
- `발전가능성_확장방안_정리.md` — 실물 규모 확장 시 설계 변경 방향
- `불침번_팀원용_이해자료_최종.md` — 프로젝트 취지·로직을 쉽게 풀어쓴 내부 참고자료
- `앱_통합_가이드.md` — 텔레그램 알림봇 + PWA 연동 방법
- `bulchimbeon_animatic.html` - 애니매틱으로 만든 초기 구성안
- **시연영상 기획안**: [바로 재생하기](https://billiardchalktheif.github.io/2026ESWContest_free_bulchimbeon/docs/bulchimbeon_animatic.html) 
## guide/ — 실험·조립 가이드
- `테스트베드_조립가이드.md` — 5개 모듈 조립 순서 총정리
- `자탐_실험가이드.md` — 자탐1(차동식)·자탐2(광전식) 실험 프로토콜
- `유도등_실험가이드.md` — 유도등 구동전압 탐색 및 배터리 방전 실험

## server/ - 실제 동작 코드
- 라즈베리파이에서 상시 구동되는 서버, AI 모델 학습 코드, 대시보드
- 각 esp32-C3에서 해당 모듈을 작동하는 코드

## simulation/ - 하드웨어 시뮬레이션 (Wokwi)
- 실물 부품 도착 전, 회로·로직을 미리 검증한 시뮬레이션
- 
- **가스계(CO2) 모듈**: [https://wokwi.com/projects/471159083371335681]
  - 소스: `simulation/가스계_wokwi/`
  - 시뮬레이션 한계: HX711 캘리브레이션 계수는 임시값 — 실물 로드셀 수령 후 실측 조정 예정
    
- **소화기 모듈**: [https://wokwi.com/projects/471162307837300737]
  - 소스: `simulation/소화기_wokwi/`
  - 시뮬레이션 한계:
  - 실물은 리프노드-게이트웨이 간 **ESP-NOW 무선통신**으로 연결상태를 판단하지만, Wokwi에서 무선 통신하는 보드     2개를 구현하기 어려워 **푸시버튼으로 연결끊김 상태를 대신 흉내냄**.
  - 판정 로직(가속도 이벤트 → 연결상태 확인 → 이탈 확정)의 흐름 자체는 실물과 동일함


## 폴더 구조
- docs/
  - 개발_장애요인_해결방안_정리.md
  - 발전가능성_확장방안_정리.md
  - 불침번_팀원용_이해자료_최종.md
  - 불침번_하드웨어_조감도_핀배치_v3.pdf
  - 소프트웨어_구상안.md
  - 앱_통합_가이드.md
  - bulchimbeon_animatic.html

- guide/
  - 테스트베드_조립가이드.md
  - 자탐_실험가이드.md
  - 유도등_실험가이드.md

- server/
  - (소스코드)
 
- simulation/
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
