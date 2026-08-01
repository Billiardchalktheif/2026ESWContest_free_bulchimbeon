# 2026ESWContest_free_불침번

## 하드웨어 시뮬레이션 (Wokwi)
실물 부품 도착 전, 회로·로직을 미리 검증한 시뮬레이션입니다.

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
  - 불침번_하드웨어_조감도_핀배치_v3.pdf
  - 소프트웨어_구상안.md
  - 개발_장애요인_해결방안_정리.md
  - 발전가능성_확장방안_정리.md
  - 불침번_팀원용_이해자료_최종.md
  - 앱_통합_가이드.md

- guide/
  - 테스트베드_조립가이드.md
  - 자탐_실험가이드.md
  - 유도등_실험가이드.md

- media/
  - (링크)

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
