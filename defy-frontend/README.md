# defy-frontend

15c 실시간 차량 시연 웹 프론트엔드입니다. Vite + TypeScript(프레임워크 없음)로 만들었습니다.

- 기본은 **샘플 데이터** 모드입니다. 화면 위쪽에 "샘플 데이터 — 실제 15c 출력 아님"이 표시됩니다.
- `?source=server`를 붙이면 서버 모드가 되고, 목업 서버나 실제 서버와 WebSocket으로 통신합니다.

## 준비

- **Node.js 24 이상.** 목업 서버(`mock/*.ts`)를 Node가 TypeScript 그대로 실행하기 때문입니다. nvm을 쓴다면 `nvm use`로 `.nvmrc`의 버전을 고를 수 있습니다.

```bash
git clone https://github.com/cksdyd3786/defy-frontend.git
cd defy-frontend
npm install
```

## 실행

### 샘플 모드 (서버 없이)

```bash
npm run dev
```

http://localhost:5173 을 엽니다. 처음에는 4단계 안내가 나오고, Enter 또는 아무 키로 넘깁니다.

- **샘플 재생:** 오른쪽 위 "테스트" 목록에서 고릅니다. 정적 확인 프레임도 같은 목록에 있습니다.
- **운전:** 물리 키보드로 합니다. W/A/S/D 운전, Q/E 깜빡이, 1~5 시나리오, 0 해제, R 초기화.

### 서버 모드 (목업 서버)

터미널 두 개가 필요합니다.

```bash
npm run mock   # 목업 서버 (기본 포트 8787, 환경변수 MOCK_PORT로 변경)
npm run dev    # Vite 개발 서버. /api 를 목업 서버로 프록시
```

http://localhost:5173/?source=server 를 엽니다.

- **개발용 도구:** 주소 끝에 `#debug`를 붙이면 메시지 로그 창과 [오류 일으키기] 시험 메뉴가 보입니다.
- **실제 서버에 연결:** `DEFY_API_TARGET=http://서버주소:포트 npm run dev`로 프록시 대상을 바꿉니다.
- **목업 서버는 실제 15c가 아닙니다.** 계약(`src/contracts.ts`) 형식만 흉내 냅니다.

## 스크립트

| 명령 | 내용 |
|---|---|
| `npm run dev` | 개발 서버 (http://localhost:5173) |
| `npm run mock` | 목업 서버 (http://localhost:8787) |
| `npm run typecheck` | 타입 검사 |
| `npm run check:contracts` | `src/contracts.ts`가 원본 `../demo_v15c/docs/Defy_Tech_Spec_Contracts/web-demo-contracts.ts`와 같은지 확인합니다. 원본 폴더가 없으면 건너뜁니다 |
| `npm run build` | 계약 확인 → 타입 검사 → `dist/` 빌드 |
| `npm run preview` | 빌드 결과 미리보기 (http://localhost:4173) |

## 폴더

| 경로 | 내용 |
|---|---|
| `src/contracts.ts` | 계약 타입. 원본 사본이므로 **직접 고치지 않습니다** |
| `src/contract-access.ts` | 계약 필드에 접근하는 유일한 곳 |
| `src/source/` | 샘플 재생, WebSocket 클라이언트, 오류 처리 규칙 |
| `src/input/` | 키보드 입력과 서버로 보내는 메시지 |
| `src/view/` | 도로·그래프·게이지·패널 그리기 |
| `src/ui/` | 안내, 정지, 오류, 연결 화면과 화면 문구(`text-ko.ts`) |
| `mock/` | 목업 서버 |
