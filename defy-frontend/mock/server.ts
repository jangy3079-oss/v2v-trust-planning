/*
 * 목업 서버 — 화면·연결 검증용. 실제 15c 시뮬레이션·모델·보정이 아니다 (물리·신뢰도·경보는 단순 근사, session.ts 의 // MOCK).
 * 명세 5장 HTTP 와 6장 WebSocket 계약을 src/contracts.ts 타입 그대로 구현한다. 프론트 코드·상수는 공유하지 않는다.
 * 포함: history/events, 입력 TTL 750ms(paused/input_timeout), 3초 무응답 시 연결 종료(paused/disconnected),
 *       끊긴 뒤 120초 만료(410 SESSION_EXPIRED), 재접속은 다시 hello · 자동 재개 없음 (명세 6.2, 8.2).
 *       누적 지연 500ms 초과 → paused/overload. 모델 실패 → phase=error (R 로 복귀).
 * 미포함: Origin 검사·실제 속도 제한(RATE_LIMITED 는 시험 장치로만 발생).
 * 시험용(계약 외, 목업 전용):
 *   POST /api/v1/_mock/faults  { drop_next_ack, ws_error:{code,match}, http_error:{code,match:'METHOD /path',retry_after_s},
 *                                history_delay_ms, conflict_next_hello, overload_ms, internal_error }
 *   POST /api/v1/_mock/drop-connections · /_mock/expire · /_mock/reset-sessions
 * 실행: npm run mock   (포트: 환경변수 MOCK_PORT, 기본 8787)
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomUUID } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';
import type {
  AckMessage, ApiError, CommandAction, CommandBody, CreateSessionResponse, DriveKey, ErrorCode, ErrorMessage, ErrorResponse,
  EventsResponse, HealthResponse, HistoryResponse, PongMessage, ReadyMessage, Solver, TelemetryMessage,
} from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG } from './config.ts';
import { createSession, DT, isControl, type DrivingBody, type MockSession } from './session.ts';

const PORT = Number(process.env['MOCK_PORT'] ?? 8787);
const BASE = '/api/v1';
const HELLO_TIMEOUT_MS = 5000;
const REQUEST_CACHE = 128;
const IDEMPOTENCY_TTL_MS = 120_000;
const STEP_MS = DT * 1000;
const MAX_CATCH_UP = 5;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DRIVE_KEYS: readonly DriveKey[] = ['W', 'A', 'S', 'D'];
const INPUT_LEASE_MS = MOCK_DEMO_CONFIG.timing.input_lease_ms;                         // 750
const LIVENESS_MS = 3000;                                                               // 3초 유효 메시지 없음 → 연결 종료
// 120초. 시험할 때만 MOCK_RETENTION_S 로 줄일 수 있다
const RETENTION_MS = Number(process.env['MOCK_RETENTION_S'] ?? MOCK_DEMO_CONFIG.timing.disconnected_retention_s) * 1000;

const HTTP_STATUS: Partial<Record<ErrorCode, number>> = {
  INVALID_ARGUMENT: 400, UNAUTHENTICATED: 401, FORBIDDEN: 403, SESSION_NOT_FOUND: 404, SESSION_EXPIRED: 410,
  CONTROL_CONFLICT: 409, STALE_GENERATION: 409, INVALID_STATE: 409, TUTORIAL_STEP_MISMATCH: 409, STALE_CONNECTION: 409,
  STALE_CLIENT_SEQ: 409, REQUEST_ID_CONFLICT: 409, IDEMPOTENCY_CONFLICT: 409, RESET_CANCELLED: 409, RATE_LIMITED: 429,
  SOLVER_UNAVAILABLE: 503, MODEL_UNAVAILABLE: 503, INTERNAL_ERROR: 500,
};
const RETRYABLE = new Set<ErrorCode>(['RATE_LIMITED', 'INTERNAL_ERROR', 'MODEL_UNAVAILABLE', 'CONTROL_CONFLICT']);
const OVERLOAD_MS = 500; // 명세 4.2: 누적 지연 500ms 초과 → paused/overload

const apiError = (code: ErrorCode, message: string, details: ApiError['details'] = {}): ApiError =>
  ({ code, message, retryable: RETRYABLE.has(code), details });

// ---- 세션·연결 상태 ----
interface CachedResult { bodyKey: string; ack: Omit<AckMessage, 'client_seq' | 'server_time'> | null } // null = 아직 적용 대기
interface Conn { ws: WebSocket; id: string; lastSeq: number; lastMsgAt: number }
interface Pending { requestId: string; clientSeq: number; body: DrivingBody; conn: Conn }
interface Entry {
  s: MockSession;
  conn: Conn | null;
  requests: Map<string, CachedResult>; // request_id → 결과 (리셋을 넘어 유지, 최근 128)
  pending: Pending[];                  // 다음 스텝 경계에서 적용할 운전 명령
  due: number;
  lastInputAt: number;                 // 마지막 유효 input.state (TTL)
  disconnectedAt: number | null;       // 연결 없음 시작 시각 (생성 직후 포함)
  expired: boolean;                    // 만료 후에도 410 을 주기 위해 남겨 둔다
}
const sessions = new Map<string, Entry>();
const idempotency = new Map<string, { bodyKey: string; at: number; status: number; payload: unknown }>();
let connCounter = 0;
// 시험용 오류 주입 (계약 외). 각 항목은 한 번 쓰이면 꺼진다
const faults = {
  dropNextAck: null as CommandAction | null,
  wsError: null as { code: ErrorCode; match: string } | null,             // match: '*', 'input.state', 'ping', 또는 command action
  httpError: null as { code: ErrorCode; match: string; retryAfterS: number } | null, // match: 경로에 포함된 문자열
  historyDelayMs: 0,
  conflictNextHello: false,
};
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

// ---- HTTP ----
function send(res: ServerResponse, status: number, payload: unknown, headers: Record<string, string> = {}): void {
  if (status === 204) { res.writeHead(204).end(); return; }
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', ...headers });
  res.end(JSON.stringify(payload));
}
function sendError(res: ServerResponse, e: ApiError, retryAfterS?: number): void {
  const body: ErrorResponse = { error: e };
  send(res, HTTP_STATUS[e.code] ?? 500, body, retryAfterS != null ? { 'Retry-After': String(retryAfterS) } : {});
}
async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const text = Buffer.concat(chunks).toString('utf8').trim();
  return text ? JSON.parse(text) : {};
}
function authorize(req: IncomingMessage, res: ServerResponse, id: string): Entry | null {
  const e = sessions.get(id);
  if (!e) { sendError(res, apiError('SESSION_NOT_FOUND', '세션이 없습니다')); return null; }
  if (e.expired) { sendError(res, apiError('SESSION_EXPIRED', '세션이 만료되었습니다 (연결 없음 120초)')); return null; }
  const auth = req.headers['authorization'];
  if (!auth?.startsWith('Bearer ')) { sendError(res, apiError('UNAUTHENTICATED', 'Authorization: Bearer 토큰이 필요합니다')); return null; }
  if (auth.slice(7) !== e.s.token) { sendError(res, apiError('FORBIDDEN', '토큰이 일치하지 않습니다')); return null; }
  return e;
}

async function handleHttp(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? '/', 'http://mock');
  const path = url.pathname;
  const method = req.method ?? 'GET';
  const now = new Date().toISOString();

  // 시험: 다음 HTTP 요청 하나를 지정한 오류로 (시험 경로 자체는 제외)
  const he = faults.httpError;
  if (he && !path.includes('/_mock/') && `${method} ${path}`.includes(he.match)) {
    faults.httpError = null;
    console.log(`[mock] fault: http ${he.code} for ${method} ${path}`);
    return sendError(res, apiError(he.code, `시험용 오류 (${he.code})`, { injected: true, stack: 'Error: 시험용 스택 — 화면에 보이면 안 됨\n    at mock' }),
      he.code === 'RATE_LIMITED' ? he.retryAfterS : undefined);
  }

  if (method === 'GET' && path === `${BASE}/health`) {
    const body: HealthResponse = { protocol_version: 'v1', ready: true, model_version: 'demo_v15c', available_solvers: MOCK_DEMO_CONFIG.available_solvers, auto_drive_available: false, server_time: now };
    return send(res, 200, body);
  }
  if (method === 'GET' && path === `${BASE}/demo-config`) return send(res, 200, MOCK_DEMO_CONFIG);

  if (method === 'POST' && path === `${BASE}/sessions`) {
    const key = req.headers['idempotency-key'];
    if (typeof key !== 'string' || !UUID_RE.test(key)) return sendError(res, apiError('INVALID_ARGUMENT', 'Idempotency-Key 헤더(UUID)가 필요합니다', { field: 'Idempotency-Key' }));
    let body: unknown;
    try { body = await readJson(req); } catch { return sendError(res, apiError('INVALID_ARGUMENT', 'JSON 본문이 아닙니다')); }
    if (typeof body !== 'object' || body === null || Array.isArray(body)) return sendError(res, apiError('INVALID_ARGUMENT', '본문은 객체여야 합니다'));
    const extra = Object.keys(body).filter(k => k !== 'solver');
    if (extra.length) return sendError(res, apiError('INVALID_ARGUMENT', `정의되지 않은 필드: ${extra.join(', ')}`, { field: extra[0]! }));
    const solverRaw = (body as { solver?: unknown }).solver ?? 'rule';
    if (solverRaw !== 'rule' && solverRaw !== 'nmpc') return sendError(res, apiError('INVALID_ARGUMENT', 'solver 는 rule 또는 nmpc', { field: 'solver' }));
    const solver: Solver = solverRaw;
    const bodyKey = JSON.stringify({ solver });
    const cached = idempotency.get(key);
    if (cached && Date.now() - cached.at < IDEMPOTENCY_TTL_MS) {
      if (cached.bodyKey !== bodyKey) return sendError(res, apiError('IDEMPOTENCY_CONFLICT', '같은 Idempotency-Key 에 다른 본문'));
      return send(res, cached.status, cached.payload);
    }
    if (!MOCK_DEMO_CONFIG.available_solvers.includes(solver)) return sendError(res, apiError('SOLVER_UNAVAILABLE', `${solver} 는 목업 서버에서 지원하지 않습니다`, { solver }));
    const id = `mock_${randomUUID().slice(0, 8)}`;
    const s = createSession(id, randomUUID(), solver);
    sessions.set(id, { s, conn: null, requests: new Map(), pending: [], due: 0, lastInputAt: 0, disconnectedAt: performance.now(), expired: false });
    const payload: CreateSessionResponse = {
      protocol_version: 'v1', session_id: id, session_token: s.token, ws_path: `${BASE}/sessions/${id}/stream`,
      expires_after_disconnect_s: 120, snapshot: s.snapshot(),
    };
    idempotency.set(key, { bodyKey, at: Date.now(), status: 201, payload });
    console.log(`[mock] session created ${id} (${solver})`);
    return send(res, 201, payload);
  }

  const m = path.match(new RegExp(`^${BASE}/sessions/([^/]+)(/history|/events)?$`));
  if (m) {
    const e = authorize(req, res, m[1]!);
    if (!e) return;
    if (method === 'DELETE' && !m[2]) {
      e.conn?.ws.close(1000, 'session deleted');
      sessions.delete(m[1]!);
      return send(res, 204, null);
    }
    if (method === 'GET' && !m[2]) return send(res, 200, e.s.snapshot());
    if (method === 'GET' && m[2]) {
      if (m[2] === '/history' && faults.historyDelayMs > 0) { // 시험: 응답 지연 (그 사이 reset 재현)
        const d = faults.historyDelayMs; faults.historyDelayMs = 0;
        console.log(`[mock] fault: history delayed ${d}ms`);
        await sleep(d);
      }
      const g = Number(url.searchParams.get('generation'));
      if (!url.searchParams.has('generation') || !Number.isSafeInteger(g) || g < 1) return sendError(res, apiError('INVALID_ARGUMENT', 'generation 쿼리가 필요합니다', { field: 'generation' }));
      if (g !== e.s.generation) {
        console.log(`[mock] ${m[2]} generation=${g} → 409 STALE_GENERATION (현재 ${e.s.generation})`);
        return sendError(res, apiError('STALE_GENERATION', '현재 generation 과 다릅니다', { current_generation: e.s.generation }));
      }
      const isHistory = m[2] === '/history';
      console.log(`[mock] ${m[2]} generation=${g} → 200`);
      const cursorName = isHistory ? 'after_tick' : 'after_event_seq';
      const raw = url.searchParams.get(cursorName);
      const cursor = raw == null ? (isHistory ? -1 : 0) : Number(raw);
      if (!Number.isSafeInteger(cursor) || cursor < (isHistory ? -1 : 0)) return sendError(res, apiError('INVALID_ARGUMENT', `${cursorName} 는 정수`, { field: cursorName }));
      if (isHistory) {
        const body: HistoryResponse = { session_id: e.s.id, generation: g, ...e.s.history(cursor) };
        return send(res, 200, body);
      }
      const body: EventsResponse = { session_id: e.s.id, generation: g, ...e.s.events(cursor) };
      return send(res, 200, body);
    }
  }
  // ---- 시험용 경로 (계약 외, 목업 전용) ----
  if (method === 'POST' && path === `${BASE}/_mock/faults`) {
    const body = (await readJson(req)) as {
      drop_next_ack?: CommandAction; ws_error?: { code: ErrorCode; match?: string };
      http_error?: { code: ErrorCode; match?: string; retry_after_s?: number };
      history_delay_ms?: number; conflict_next_hello?: boolean; overload_ms?: number; internal_error?: boolean;
    };
    if (body.drop_next_ack !== undefined) faults.dropNextAck = body.drop_next_ack;
    if (body.ws_error) faults.wsError = { code: body.ws_error.code, match: body.ws_error.match ?? '*' };
    if (body.http_error) faults.httpError = { code: body.http_error.code, match: body.http_error.match ?? '/', retryAfterS: body.http_error.retry_after_s ?? 2 };
    if (body.history_delay_ms !== undefined) faults.historyDelayMs = body.history_delay_ms;
    if (body.conflict_next_hello) faults.conflictNextHello = true;
    for (const e of sessions.values()) {
      if (e.expired) continue;
      // 누적 지연을 인위로 만든다 → 다음 루프에서 overload 판단
      if (body.overload_ms && e.s.phase === 'running') e.due -= body.overload_ms;
      if (body.internal_error) {
        e.s.setError(apiError('INTERNAL_ERROR', '시험용 모델 실패', { stack: 'Traceback (most recent call last): … 화면에 보이면 안 됨' }));
        if (e.conn) wsError(e.conn.ws, e, apiError('INTERNAL_ERROR', '시험용 모델 실패', { stack: 'Traceback … 화면에 보이면 안 됨' }), null, null);
        sendTelemetry(e);
      }
    }
    console.log('[mock] faults', JSON.stringify(body));
    return send(res, 200, { ok: true });
  }
  if (method === 'POST' && path === `${BASE}/_mock/expire`) {
    for (const e of sessions.values()) { dropConn(e, 'mock expire'); e.expired = true; }
    return send(res, 200, { ok: true });
  }
  if (method === 'POST' && path === `${BASE}/_mock/reset-sessions`) {
    // 다른 쪽에서 reset 이 일어난 상황 재현 (history 요청 도중 리셋 시험)
    for (const e of sessions.values()) {
      if (e.expired) continue;
      e.s.control({ action: 'session.reset', payload: {} });
      e.pending = [];
      sendTelemetry(e);
    }
    return send(res, 200, { ok: true });
  }
  if (method === 'POST' && path === `${BASE}/_mock/drop-connections`) {
    let n = 0;
    for (const e of sessions.values()) if (e.conn) { dropConn(e, 'mock drop-connections'); n++; }
    return send(res, 200, { dropped: n });
  }
  sendError(res, apiError('INVALID_ARGUMENT', `경로 없음: ${method} ${path}`));
}

// 연결 종료 처리: 정지(disconnected) + 만료 카운트 시작. 재접속은 다시 hello, 자동 재개 없음
function dropConn(e: Entry, why: string): void {
  const c = e.conn;
  if (!c) return;
  e.conn = null;
  e.s.setConnected(false);
  e.disconnectedAt = performance.now();
  e.s.systemPause('disconnected');
  e.s.drainEvents();
  if (c.ws.readyState === WebSocket.OPEN || c.ws.readyState === WebSocket.CONNECTING) c.ws.terminate();
  console.log(`[mock] ${e.s.id} disconnected ${c.id} (${why})`);
}

// ---- WebSocket ----
const envelope = (e: Entry) => ({ protocol_version: 'v1' as const, session_id: e.s.id, server_time: new Date().toISOString() });

function wsSend(ws: WebSocket, msg: object): void {
  if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}
function wsError(ws: WebSocket, e: Entry | null, err: ApiError, requestId: string | null, clientSeq: number | null): void {
  const msg: ErrorMessage = {
    protocol_version: 'v1', session_id: e?.s.id ?? '', server_time: new Date().toISOString(), type: 'error',
    ...err, request_id: requestId, client_seq: clientSeq, current_generation: e ? e.s.generation : null,
  };
  wsSend(ws, msg);
}
function sendTelemetry(e: Entry): void {
  if (!e.conn) { e.s.drainEvents(); return; }
  const msg: TelemetryMessage = { ...envelope(e), type: 'telemetry', snapshot: e.s.snapshot(), events: e.s.drainEvents() };
  wsSend(e.conn.ws, msg);
}
function remember(e: Entry, requestId: string, r: CachedResult): void {
  e.requests.set(requestId, r);
  while (e.requests.size > REQUEST_CACHE) e.requests.delete(e.requests.keys().next().value!);
}
function ack(e: Entry, conn: Conn, requestId: string, clientSeq: number, action: CommandAction, appliedTick: number | null): void {
  const base = { ...envelope(e), type: 'ack' as const, request_id: requestId, action, generation: e.s.generation, applied_tick: appliedTick, snapshot: e.s.snapshot() };
  remember(e, requestId, { bodyKey: e.requests.get(requestId)?.bodyKey ?? '', ack: base });
  if (faults.dropNextAck === action) { // 시험: 결과는 기억하되 ack 를 잃어버린 것처럼
    faults.dropNextAck = null;
    console.log(`[mock] fault: dropped ack ${action} ${requestId}`);
    return;
  }
  const msg: AckMessage = { ...base, client_seq: clientSeq, server_time: new Date().toISOString() };
  wsSend(conn.ws, msg);
}

// 명령 본문 검사 (정의되지 않은 필드·값은 INVALID_ARGUMENT)
function parseCommand(m: Record<string, unknown>): CommandBody | string {
  const p = m['payload'];
  if (typeof p !== 'object' || p === null || Array.isArray(p)) return 'payload 는 객체여야 합니다';
  const keys = Object.keys(p);
  const only = (...allowed: string[]) => keys.every(k => allowed.includes(k)) && allowed.every(k => keys.includes(k));
  const q = p as Record<string, unknown>;
  switch (m['action']) {
    case 'signal.toggle': return only('side') && (q['side'] === 'left' || q['side'] === 'right') ? { action: 'signal.toggle', payload: { side: q['side'] } } : 'signal.toggle payload';
    case 'scenario.start': {
      const k = q['kind'];
      return only('kind') && (k === 'drift' || k === 'stuck' || k === 'weave' || k === 'brake' || k === 'accel') ? { action: 'scenario.start', payload: { kind: k } } : 'scenario.start payload';
    }
    case 'scenario.stop': return only() ? { action: 'scenario.stop', payload: {} } : 'scenario.stop payload';
    case 'session.reset': return only() ? { action: 'session.reset', payload: {} } : 'session.reset payload';
    case 'session.resume': return only() ? { action: 'session.resume', payload: {} } : 'session.resume payload';
    case 'session.pause': return only('reason') && (q['reason'] === 'focus_lost' || q['reason'] === 'user') ? { action: 'session.pause', payload: { reason: q['reason'] } } : 'session.pause payload';
    case 'tutorial.advance': {
      const st = q['expected_step'];
      return only('expected_step') && (st === 0 || st === 1 || st === 2 || st === 3) ? { action: 'tutorial.advance', payload: { expected_step: st } } : 'tutorial.advance payload';
    }
    default: return `알 수 없는 action: ${String(m['action'])}`;
  }
}

function onMessage(e: Entry, conn: Conn, raw: string): void {
  let m: Record<string, unknown>;
  try { m = JSON.parse(raw) as Record<string, unknown>; } catch { return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'JSON 이 아닙니다'), null, null); }
  const seq = typeof m['client_seq'] === 'number' ? m['client_seq'] : null;
  const rid = typeof m['request_id'] === 'string' ? m['request_id'] : null;
  if (m['protocol_version'] !== 'v1') return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'protocol_version 은 v1'), rid, seq);
  if (m['connection_id'] !== conn.id) return wsError(conn.ws, e, apiError('STALE_CONNECTION', '현재 연결의 connection_id 가 아닙니다'), rid, seq);
  if (seq == null || !Number.isSafeInteger(seq) || seq < 1) return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'client_seq 는 1 이상의 정수'), rid, seq);
  if (seq <= conn.lastSeq) return wsError(conn.ws, e, apiError('STALE_CLIENT_SEQ', `client_seq 는 단조 증가해야 합니다 (마지막 ${conn.lastSeq})`), rid, seq);
  conn.lastSeq = seq;
  conn.lastMsgAt = performance.now(); // 유효 메시지 (연결 생존). ping 도 포함되지만 입력 TTL 은 연장하지 않음

  // 시험: 다음 메시지 하나를 지정한 오류로
  const we = faults.wsError;
  const kind = m['type'] === 'command' ? String(m['action']) : String(m['type']);
  if (we && (we.match === '*' || we.match === kind)) {
    faults.wsError = null;
    console.log(`[mock] fault: ws ${we.code} for ${kind}`);
    wsError(conn.ws, e, apiError(we.code, `시험용 오류 (${we.code})`, { injected: true, stack: 'Error: 시험용 스택 — 화면에 보이면 안 됨' }), rid, seq);
    if (rid) e.requests.delete(rid);
    if (we.code === 'UNAUTHENTICATED' || we.code === 'FORBIDDEN') conn.ws.close(1008, 'auth');
    return;
  }

  switch (m['type']) {
    case 'ping': {
      const t = m['client_time_ms'];
      if (typeof t !== 'number' || Object.keys(m).length !== 5) return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'ping 형식'), null, seq);
      const pong: PongMessage = { ...envelope(e), type: 'pong', client_time_ms: t };
      return wsSend(conn.ws, pong);
    }
    case 'input.state': {
      const allowed = ['protocol_version', 'type', 'connection_id', 'client_seq', 'generation', 'held', 'tapped'];
      if (Object.keys(m).some(k => !allowed.includes(k))) return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', '정의되지 않은 필드'), null, seq);
      const valid = (a: unknown): a is DriveKey[] => Array.isArray(a) && a.every(k => DRIVE_KEYS.includes(k as DriveKey)) && new Set(a).size === a.length;
      if (!valid(m['held']) || !valid(m['tapped'])) return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'held/tapped 는 중복 없는 W/A/S/D 배열'), null, seq);
      if (m['generation'] !== e.s.generation) return wsError(conn.ws, e, apiError('STALE_GENERATION', 'generation 이 다릅니다'), null, seq);
      if (e.s.phase !== 'running') return wsError(conn.ws, e, apiError('INVALID_STATE', `input.state 는 running 에서만 (phase=${e.s.phase})`), null, seq);
      e.s.setInput(m['held'], m['tapped'], seq, conn.id);
      e.lastInputAt = performance.now(); // 입력 TTL 연장은 유효한 input.state 만
      return;
    }
    case 'command': {
      if (rid == null || !UUID_RE.test(rid)) return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', 'request_id 는 UUID'), rid, seq);
      const body = parseCommand(m);
      if (typeof body === 'string') return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', body), rid, seq);
      const bodyKey = JSON.stringify({ action: body.action, payload: body.payload, generation: m['generation'] });
      // 중복 request_id 확인은 generation 검사보다 먼저 (명세 6.1-5)
      const cached = e.requests.get(rid);
      if (cached) {
        if (cached.bodyKey !== bodyKey) return wsError(conn.ws, e, apiError('REQUEST_ID_CONFLICT', '같은 request_id 에 다른 본문'), rid, seq);
        if (cached.ack) wsSend(conn.ws, { ...cached.ack, client_seq: seq, server_time: new Date().toISOString() });
        return; // 적용 대기 중이면 적용 후 한 번만 ack
      }
      if (m['generation'] !== e.s.generation) return wsError(conn.ws, e, apiError('STALE_GENERATION', 'generation 이 다릅니다', { current_generation: e.s.generation }), rid, seq);

      if (isControl(body)) {
        const r = e.s.control(body);
        if (!r.ok) return wsError(conn.ws, e, apiError(r.code, r.message), rid, seq);
        if (body.action === 'session.reset') {
          // 아직 적용되지 않은 이전 generation 명령은 폐기 (RESET_CANCELLED)
          for (const p of e.pending) wsError(p.conn.ws, e, apiError('RESET_CANCELLED', '초기화로 폐기된 명령'), p.requestId, p.clientSeq);
          e.pending = [];
        }
        if (body.action === 'session.pause') {
          for (const p of e.pending) wsError(p.conn.ws, e, apiError('INVALID_STATE', '정지로 적용되지 않은 명령'), p.requestId, p.clientSeq);
          e.pending = [];
        }
        if (e.s.phase === 'running') { // 실행 시작·재개: 밀린 시간 없이 다음 스텝부터, 입력 TTL 새로 시작
          e.due = performance.now() + STEP_MS;
          e.lastInputAt = performance.now();
        }
        remember(e, rid, { bodyKey, ack: null });
        ack(e, conn, rid, seq, body.action, null);
        sendTelemetry(e); // 상태 변경 직후 telemetry 1회 (새 물리 표본 없음)
        return;
      }
      // 운전 명령: running 에서만, 다음 스텝 경계에서 적용한 뒤 ack
      if (e.s.phase !== 'running') return wsError(conn.ws, e, apiError('INVALID_STATE', `${body.action} 은 running 에서만 (phase=${e.s.phase})`), rid, seq);
      remember(e, rid, { bodyKey, ack: null });
      e.pending.push({ requestId: rid, clientSeq: seq, body, conn });
      return;
    }
    case 'hello':
      return wsError(conn.ws, e, apiError('INVALID_STATE', '이미 인증된 연결입니다'), null, seq);
    default:
      return wsError(conn.ws, e, apiError('INVALID_ARGUMENT', `알 수 없는 type: ${String(m['type'])}`), null, seq);
  }
}

function attach(ws: WebSocket, e: Entry): void {
  let conn: Conn | null = null;
  const helloTimer = setTimeout(() => {
    wsError(ws, e, apiError('UNAUTHENTICATED', '5초 안에 hello 가 없습니다'), null, null);
    ws.close(1008, 'hello timeout');
  }, HELLO_TIMEOUT_MS);

  ws.on('message', data => {
    const raw = data.toString();
    if (conn) return onMessage(e, conn, raw);
    // 인증 전: 첫 메시지는 hello 여야 한다. 인증 전에는 세션 상태를 보내지 않는다
    let m: Record<string, unknown> | null = null;
    try { m = JSON.parse(raw) as Record<string, unknown>; } catch { /* 아래에서 처리 */ }
    if (!m || m['type'] !== 'hello' || m['protocol_version'] !== 'v1' || m['session_token'] !== e.s.token) {
      wsError(ws, null, apiError('UNAUTHENTICATED', 'hello 인증 실패'), null, null);
      ws.close(1008, 'unauthenticated');
      return;
    }
    clearTimeout(helloTimer);
    if (faults.conflictNextHello || (e.conn && e.conn.ws.readyState === WebSocket.OPEN)) {
      faults.conflictNextHello = false;
      wsError(ws, e, apiError('CONTROL_CONFLICT', '이미 활성 제어 연결이 있습니다'), null, null);
      ws.close(1008, 'control conflict');
      return;
    }
    conn = { ws, id: `conn_${++connCounter}`, lastSeq: 0, lastMsgAt: performance.now() };
    e.conn = conn;
    e.disconnectedAt = null;
    e.s.setConnected(true);
    const ready: ReadyMessage = {
      ...envelope(e), type: 'ready', connection_id: conn.id, snapshot: e.s.snapshot(),
      retention: { history_samples: 600, event_limit: 200, disconnected_seconds: 120 },
    };
    e.s.drainEvents();
    wsSend(ws, ready);
    console.log(`[mock] ${e.s.id} ready ${conn.id}`);
  });
  ws.on('close', () => {
    clearTimeout(helloTimer);
    if (conn && e.conn === conn) dropConn(e, 'socket closed');
  });
}

// ---- 고정 스텝 루프 (세션마다 100ms, running 일 때만) + 연결 생존·입력 TTL·만료 ----
setInterval(() => {
  const now = performance.now();
  for (const e of sessions.values()) {
    if (e.expired) continue;
    if (e.conn && now - e.conn.lastMsgAt > LIVENESS_MS) dropConn(e, `${LIVENESS_MS / 1000}초 동안 유효 메시지 없음`);
    if (!e.conn && e.disconnectedAt != null && now - e.disconnectedAt > RETENTION_MS) {
      e.expired = true; e.pending = [];
      console.log(`[mock] ${e.s.id} expired`);
      continue;
    }
    if (e.s.phase === 'running' && now - e.lastInputAt > INPUT_LEASE_MS) {
      // 입력 TTL: held/tapped 비우고 paused/input_timeout (tutorial·paused 에는 적용하지 않음)
      e.s.systemPause('input_timeout');
      for (const p of e.pending) wsError(p.conn.ws, e, apiError('INVALID_STATE', '입력 시간 초과로 정지되어 적용되지 않은 명령'), p.requestId, p.clientSeq);
      e.pending = [];
      sendTelemetry(e);
      continue;
    }
    if (e.s.phase !== 'running') continue;
    if (now - e.due > OVERLOAD_MS) {
      // 누적 지연 500ms 초과: dt 를 늘리거나 추론을 생략하지 않고 정지. 재개하면 밀린 wall time 은 버린다
      e.s.systemPause('overload');
      e.due = now + STEP_MS;
      for (const p of e.pending) wsError(p.conn.ws, e, apiError('INVALID_STATE', '과부하로 정지되어 적용되지 않은 명령'), p.requestId, p.clientSeq);
      e.pending = [];
      sendTelemetry(e);
      continue;
    }
    let n = 0;
    while (now >= e.due && n < MAX_CATCH_UP && e.s.phase === 'running') {
      const batch = e.pending; e.pending = [];
      e.s.step(batch.map(p => p.body), now - e.due);
      const tick = e.s.snapshot().sample_tick;
      for (const p of batch) ack(e, p.conn, p.requestId, p.clientSeq, p.body.action, tick);
      sendTelemetry(e);
      e.due += STEP_MS;
      n++;
    }
    if (now - e.due > STEP_MS * MAX_CATCH_UP) e.due = now + STEP_MS; // 밀린 시간은 폐기 (dt 를 늘리지 않음)
  }
}, 5);

// ---- 서버 ----
const server = createServer((req, res) => {
  handleHttp(req, res).catch(err => {
    console.error('[mock] http error', err);
    sendError(res, apiError('INTERNAL_ERROR', '목업 서버 오류'));
  });
});
const wss = new WebSocketServer({ noServer: true });
server.on('upgrade', (req, socket, head) => {
  const m = new URL(req.url ?? '/', 'http://mock').pathname.match(new RegExp(`^${BASE}/sessions/([^/]+)/stream$`));
  const e = m ? sessions.get(m[1]!) : undefined;
  if (!e) { socket.write('HTTP/1.1 404 Not Found\r\n\r\n'); socket.destroy(); return; }
  if (e.expired) { socket.write('HTTP/1.1 410 Gone\r\n\r\n'); socket.destroy(); return; }
  wss.handleUpgrade(req, socket, head, ws => attach(ws, e));
});
server.listen(PORT, () => {
  console.log(`[mock] 목업 서버 (실제 15c 아님) http://localhost:${PORT}${BASE}  · WS ${BASE}/sessions/{id}/stream`);
});
