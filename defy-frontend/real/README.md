# real/ — 실제 15c(demo_v15c)를 프론트에 연결하는 서버

합성 데이터 · 직선 도로 · 시뮬레이션. mock/server.ts 와 같은 API(contract v1)를 내되, 시뮬레이션은 demo_v15c 의 Session 이 돌린다.

## 실행
```
# 터미널 1 (mock 서버는 같은 8787 포트라 꺼 둘 것. Node 24 이상)
npm run real   # python3 가 torch·casadi 있는 파이썬이 아니면 DEFY_PYTHON=/path/to/python 지정
# 터미널 2
npm run dev      # http://localhost:5173/?source=server
```
- Python 에 torch, numpy, casadi 필요 (nmpc 를 쓰려면 casadi).
- 환경변수: DEFY_PYTHON, DEFY_V15C(기본 ../demo_v15c), DEFY_BRIDGE_TIMEOUT_MS(60000), MOCK_PORT(8787)

## 구성
- bridge.py: demo_v15c app.Session 을 JSON 줄로 감싼 파이썬 자식 프로세스
- bridge-worker.mjs / bridge-sync.ts: Node 메인 스레드에서 동기 호출하기 위한 워커 연결
- session.ts: 세션 상태(phase, 튜토리얼, 이벤트, 그래프)를 관리하고 브리지 결과를 합침
- config.ts, server.ts: mock 과 같은 형태의 설정 / HTTP·WS 서버
- auto_driver.py: 자동 모드(가상 운전자, 명세 1.3). 조정값은 맨 위 `AUTO`. 동작 기록은 real/logs/auto_<세션>_<seed>.jsonl (저장소 제외)

## 자동 모드
`POST /sessions` 본문 `{"auto_drive": true, "auto_seed": 7, "auto_level": "high"}` (seed 생략 시 서버가 정함, level 기본 normal). 안내 4단계를 사람이 끝내면 cruise 부터 시작한다.

## 서버 단독 확인 (프론트 없이)
```
python -X utf8 real/auto_check.py auto-suite --minutes 5     # 시드 재현성 · 단계별 이벤트 횟수 · 횡 시나리오 중 A/D · 도로 이탈
python -X utf8 real/auto_check.py manual --real real --out a.jsonl   # 수동 고정 입력 기록 (다른 real 폴더와 compare)
node real/auto-ws-check.mjs manual                          # 수동: TTL·멱등키·pause/resume·이벤트 라벨
node real/auto-ws-check.mjs auto                            # 자동: 정지·끊김·수동 복귀·리셋·라벨 (실시간이라 수 분 걸림)
```
