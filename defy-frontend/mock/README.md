# mock/ — 개발용 목업 서버 (실제 15c 아님)

화면·연결을 검증하려고 만든 **개발용** 서버입니다. 계약(`src/contracts.ts`)의 HTTP·WebSocket 형식만 흉내 내며, 물리·신뢰도·경보는 단순 근사입니다. 실제 15c 결과로 쓰지 않습니다.

- 실행: `npm run mock` (포트 `MOCK_PORT`, 기본 8787) → `npm run dev` → http://localhost:5173/?source=server
- 자동 모드는 지원하지 않습니다(`auto_drive_available=false`).
- 시험용 경로 `/api/v1/_mock/*`(오류·끊김 일으키기)는 계약 밖이며, 개발 모드의 `#debug` 창에서 씁니다.
- 실제 15c 서버는 `real/`(`npm run real`)입니다.
