/*
 * #debug 시험 메뉴 — 목업 서버(/api/v1/_mock/*, 계약 외)로 오류·정지를 일부러 일으킨다. 실제 서버에는 없다.
 * 각 항목: 무엇을 일으키는지(label)와 목업에 보낼 요청.
 */
export interface FaultItem { id: string; label: string; path: string; body: object; reload?: boolean }

export const FAULTS: FaultItem[] = [
  { id: 'invalid', label: 'INVALID_ARGUMENT · 다음 input.state', path: 'faults', body: { ws_error: { code: 'INVALID_ARGUMENT', match: 'input.state' } } },
  { id: 'unauth', label: 'UNAUTHENTICATED · 다음 메시지 (연결 종료)', path: 'faults', body: { ws_error: { code: 'UNAUTHENTICATED', match: '*' } } },
  { id: 'forbidden', label: 'FORBIDDEN · 다음 메시지 (연결 종료)', path: 'faults', body: { ws_error: { code: 'FORBIDDEN', match: '*' } } },
  { id: 'expired', label: 'SESSION_EXPIRED · 세션 만료시키기', path: 'expire', body: {} },
  { id: 'conflict', label: 'CONTROL_CONFLICT · 연결 끊고 재접속 hello 거절', path: 'faults', body: { conflict_next_hello: true } },
  { id: 'stalegen', label: 'STALE_GENERATION · 다음 command', path: 'faults', body: { ws_error: { code: 'STALE_GENERATION', match: '*' } } },
  { id: 'invstate', label: 'INVALID_STATE · 다음 command (예: Q)', path: 'faults', body: { ws_error: { code: 'INVALID_STATE', match: 'signal.toggle' } } },
  { id: 'mismatch', label: 'TUTORIAL_STEP_MISMATCH · 다음 안내 진행', path: 'faults', body: { ws_error: { code: 'TUTORIAL_STEP_MISMATCH', match: 'tutorial.advance' } } },
  { id: 'staleconn', label: 'STALE_CONNECTION · 다음 메시지 (재접속)', path: 'faults', body: { ws_error: { code: 'STALE_CONNECTION', match: '*' } } },
  { id: 'staleseq', label: 'STALE_CLIENT_SEQ · 다음 메시지 (재접속)', path: 'faults', body: { ws_error: { code: 'STALE_CLIENT_SEQ', match: '*' } } },
  { id: 'ridconf', label: 'REQUEST_ID_CONFLICT · 다음 command (버그 기록)', path: 'faults', body: { ws_error: { code: 'REQUEST_ID_CONFLICT', match: 'signal.toggle' } } },
  { id: 'resetcanc', label: 'RESET_CANCELLED · 다음 command (버그 기록)', path: 'faults', body: { ws_error: { code: 'RESET_CANCELLED', match: 'scenario.start' } } },
  { id: 'rate', label: 'RATE_LIMITED · 다음 input.state', path: 'faults', body: { ws_error: { code: 'RATE_LIMITED', match: 'input.state' } } },
  { id: 'rate429', label: 'RATE_LIMITED · 다음 GET 상태 (REST 429, Retry-After 2)', path: 'faults', body: { http_error: { code: 'RATE_LIMITED', match: 'GET /api/v1/sessions/', retry_after_s: 2 } } },
  { id: 'internal', label: 'INTERNAL_ERROR · phase=error', path: 'faults', body: { internal_error: true } },
  { id: 'overload', label: '정지 overload · 누적 지연 800ms', path: 'faults', body: { overload_ms: 800 } },
  { id: 'solver', label: 'SOLVER_UNAVAILABLE · 새로고침 시 세션 생성', path: 'faults', body: { http_error: { code: 'SOLVER_UNAVAILABLE', match: 'POST /api/v1/sessions' } }, reload: true },
  { id: 'model', label: 'MODEL_UNAVAILABLE · 새로고침 시 세션 생성', path: 'faults', body: { http_error: { code: 'MODEL_UNAVAILABLE', match: 'POST /api/v1/sessions' } }, reload: true },
  { id: 'a13', label: 'A13 · history 3초 지연 + 재접속 + 도중 reset', path: 'faults', body: { history_delay_ms: 3000 } },
];
