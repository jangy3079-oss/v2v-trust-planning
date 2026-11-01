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
