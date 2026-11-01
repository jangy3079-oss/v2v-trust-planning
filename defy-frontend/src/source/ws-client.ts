/*
 * 서버 연결 (서버 모드, ?source=server). sample-player 와 같은 sink(onSnapshot)로 Snapshot 을 넘긴다.
 * 첫 연결: GET /demo-config(main) → POST /sessions (Idempotency-Key) → WebSocket → hello → ready → telemetry.
 * 재접속 (명세 8.2): 끊기면 GET /sessions/{id} 로 세션 생존 확인 → 새 WebSocket → hello → ready
 *   → 현재 generation 으로 history·events 요청 (그동안 telemetry 는 버퍼) → 합친 뒤 버퍼 적용.
 *   요청 도중 리셋되면(409 STALE_GENERATION) 응답을 버리고 새 generation 으로 다시 받는다. 재개는 사용자 버튼으로만.
 * 생존 확인: 3초 동안 서버 메시지가 없으면 끊긴 것으로 보고 재접속한다 (ping 1초 주기는 main 이 보냄).
 * 명령 재시도 (명세 9): ack 가 2초 안에 없으면 GET /sessions/{id} 로 상태 확인 후 같은 request_id·본문으로 재전송, 최대 2회.
 *   그래도 없으면 연결 복구 흐름으로 넘긴다. 새 request_id 로 다시 보내지 않는다.
 * REST 429 RATE_LIMITED: Retry-After 초만큼 기다렸다 다시 요청 (최대 3번).
 * 오류는 코드·retryable·details 와 함께 sink 로 넘긴다 (처리 방식은 source/error-policy.ts, 화면은 main·ui/error.ts).
 * 세션 토큰은 이 모듈 메모리에만 둔다 (URL·로그·localStorage 에 넣지 않음). 응답 필드는 contract-access.ts 로만 읽는다.
 */
import type {
  ClientMessage, CommandMessage, CreateSessionRequest, CreateSessionResponse, DemoConfig, DemoEvent, ErrorResponse, EventsResponse,
  HealthResponse, HistoryResponse, ServerMessage, Snapshot,
} from '../contracts';
import {
  readApiError, readCreateSession, readEventsResponse, readHealth, readHistory, readSentCommand, readServerMessage, snapshotOrder,
  type GraphPointView,
} from '../contract-access';
import { helloMessage } from '../input/messages';

export type SnapshotKind = 'ready' | 'telemetry' | 'ack' | 'rest';

export type LinkState =
  | { state: 'connecting'; step: string }                          // 첫 연결 중
  | { state: 'open'; connectionId: string; recovered: boolean }    // recovered: 재접속 후 복구 완료
  | { state: 'reconnecting'; attempt: number; reason: string }     // 끊김 → 다시 연결 중
  | { state: 'recovering'; step: string }                          // 재접속됨, history/events 복구 중
  | { state: 'failed'; step: string; reason: string; code: string | null; retryable: boolean }; // 더 진행할 수 없음

export interface RecoveryResult { samples: number; events: number; truncated: boolean }

export interface ServerErrorView {
  code: string;
  message: string;               // 콘솔·디버그용 (화면 표시는 하지 않음)
  retryable: boolean;
  details: Record<string, unknown>;
  requestId: string | null;
  source: 'ws' | 'http';
}

export interface ServerSink {
  onSnapshot(snapshot: Snapshot, events: DemoEvent[], kind: SnapshotKind): void;
  onHistory(generation: number, points: GraphPointView[]): number; // 반환: 새로 합친 표본 수
  onEvents(generation: number, events: DemoEvent[]): number;       // 반환: 새로 합친 이벤트 수
  onRecovered(r: RecoveryResult): void;
  onPong(clientTimeMs: number): void;
  onServerError(err: ServerErrorView): void;
  onRateLimited(waitS: number): void;
  onLink(link: LinkState): void;
  onRetry(message: CommandMessage, attempt: number, why: string): void; // 같은 request_id 재전송 (로그용)
}

export interface ServerSourceOptions {
  resend(m: CommandMessage): CommandMessage; // 같은 request_id 로 새 client_seq (input/messages.ts)
}

const HELLO_READY_TIMEOUT_MS = 5000;
const SILENCE_MS = 3000;          // 서버 메시지 없음 → 끊김으로 판단
const ACK_TIMEOUT_MS = 2000;      // 명령 응답 대기
const MAX_RETRIES = 2;            // 같은 request_id 재전송 횟수
const RECOVERY_TRIES = 3;         // STALE_GENERATION 으로 다시 받는 횟수
const BACKOFF_MS = [500, 1000, 2000, 4000, 5000];
const RATE_LIMIT_TRIES = 3;
const ENDED = new Set(['SESSION_EXPIRED', 'SESSION_NOT_FOUND', 'UNAUTHENTICATED', 'FORBIDDEN']);

class HttpError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;
  constructor(status: number, code: string | null, message: string, retryable: boolean, details: Record<string, unknown>) {
    super(message); this.status = status; this.code = code; this.retryable = retryable; this.details = details;
  }
}

async function httpFail(res: Response): Promise<HttpError> {
  try {
    const e = readApiError((await res.json()) as ErrorResponse);
    return new HttpError(res.status, e.code, `${res.status} ${e.code}`, e.retryable, e.details);
  } catch {
    return new HttpError(res.status, null, `HTTP ${res.status}`, res.status >= 500, {});
  }
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

// 429 이면 Retry-After 초 기다렸다 다시 (명세 9). onWait 로 화면에 알린다
async function fetchWithRetryAfter(url: string, init: RequestInit, onWait?: (s: number) => void): Promise<Response> {
  for (let i = 0; ; i++) {
    const res = await fetch(url, init);
    if (res.status !== 429 || i >= RATE_LIMIT_TRIES - 1) return res;
    const s = Math.max(1, Number(res.headers.get('Retry-After')) || 1);
    onWait?.(s);
    await sleep(s * 1000);
  }
}

// DemoConfig 는 서버 상수의 원본 (명세 5.1: 프론트가 숫자 상수를 복제하지 않음)
export async function fetchDemoConfig(base: string): Promise<DemoConfig> {
  const res = await fetch(`${base}/demo-config`, { cache: 'no-store' });
  if (!res.ok) throw await httpFail(res);
  return (await res.json()) as DemoConfig;
}

// GET /health: 자동 모드 지원 여부 (명세 5, v1.4). 실패하면 지원하지 않는 것으로 본다 (시작 카드 없이 기존 흐름)
export async function fetchAutoDriveAvailable(base: string): Promise<boolean> {
  try {
    const res = await fetch(`${base}/health`, { cache: 'no-store' });
    if (!res.ok) return false;
    return readHealth((await res.json()) as HealthResponse).autoDriveAvailable === true;
  } catch {
    return false;
  }
}

interface PendingCommand { message: CommandMessage; generation: number; sentAt: number; retries: number; checking: boolean }

export function createServerSource(base: string, sink: ServerSink, opts: ServerSourceOptions) {
  let sessionId: string | null = null;
  let token: string | null = null; // 메모리에만
  let wsPath: string | null = null;
  let ws: WebSocket | null = null;
  let connectionId: string | null = null;
  let everOpened = false;
  let failed = false;
  let lastServerMsgAt = 0;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let recovering = false;
  let held: ServerMessage[] = [];  // 복구 중 들어온 telemetry
  let currentGeneration: number | null = null;
  const pending = new Map<string, PendingCommand>();

  const auth = () => ({ Authorization: `Bearer ${token}` });
  const link = (l: LinkState) => { if (l.state === 'failed') failed = true; sink.onLink(l); };
  const failWith = (step: string, err: unknown) => {
    const h = err instanceof HttpError ? err : null;
    link({ state: 'failed', step, reason: err instanceof Error ? err.message : String(err), code: h?.code ?? null, retryable: h?.retryable ?? true });
    if (h?.code) sink.onServerError({ code: h.code, message: h.message, retryable: h.retryable, details: h.details, requestId: null, source: 'http' });
  };

  function wsUrl(path: string): string {
    const abs = new URL(path, new URL(base, location.href));
    abs.protocol = abs.protocol === 'https:' ? 'wss:' : 'ws:';
    return abs.toString();
  }

  async function getJson<T>(path: string): Promise<T> {
    const res = await fetchWithRetryAfter(`${base}${path}`, { headers: auth(), cache: 'no-store' }, sink.onRateLimited);
    if (!res.ok) throw await httpFail(res);
    return (await res.json()) as T;
  }

  // GET /sessions/{id}: 상태 확인 (명령 재시도·복구 중 리셋 확인)
  async function fetchSnapshot(): Promise<Snapshot> {
    const s = await getJson<Snapshot>(`/sessions/${sessionId}`);
    currentGeneration = snapshotOrder(s).generation;
    sink.onSnapshot(s, [], 'rest');
    return s;
  }

  function dispatch(m: ServerMessage): void {
    const v = readServerMessage(m);
    switch (v.type) {
      case 'ready': return; // openSocket 에서 처리
      case 'telemetry':
        currentGeneration = snapshotOrder(v.snapshot).generation;
        sink.onSnapshot(v.snapshot, v.events, 'telemetry');
        return;
      case 'ack':
        pending.delete(v.requestId);
        currentGeneration = v.generation;
        sink.onSnapshot(v.snapshot, [], 'ack');
        return;
      case 'pong': sink.onPong(v.clientTimeMs); return;
      case 'error':
        if (v.requestId) pending.delete(v.requestId); // 실패한 명령만 취소 (자동 재시도 안 함)
        sink.onServerError({ code: v.code, message: v.message, retryable: v.retryable, details: v.details, requestId: v.requestId, source: 'ws' });
        return;
    }
  }

  // ---- 재접속 후 복구: history·events 를 현재 generation 으로, 리셋되면 다시 ----
  async function recover(generation: number): Promise<void> {
    recovering = true;
    link({ state: 'recovering', step: 'history · events 복구' });
    let gen = generation;
    let result: RecoveryResult = { samples: 0, events: 0, truncated: false };
    for (let i = 0; i < RECOVERY_TRIES; i++) {
      try {
        const [h, e] = await Promise.all([
          getJson<HistoryResponse>(`/sessions/${sessionId}/history?generation=${gen}&after_tick=-1`),
          getJson<EventsResponse>(`/sessions/${sessionId}/events?generation=${gen}&after_event_seq=0`),
        ]);
        const hv = readHistory(h), ev = readEventsResponse(e);
        result = {
          samples: sink.onHistory(hv.generation, hv.samples),
          events: sink.onEvents(ev.generation, ev.events),
          truncated: hv.truncated || ev.truncated,
        };
        break;
      } catch (err) {
        if (err instanceof HttpError && err.code === 'STALE_GENERATION') {
          // 요청 도중 리셋: 이 응답은 버리고 현재 generation 으로 다시
          gen = snapshotOrder(await fetchSnapshot()).generation;
          continue;
        }
        recovering = false; held = [];
        if (err instanceof HttpError && err.code && ENDED.has(err.code)) return failWith('복구', err);
        return reconnectLater(`복구 실패: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    // 복구 중 쌓인 telemetry 적용 (렌더 쪽이 generation·frame_seq·event_seq 로 중복 제거)
    const queued = held; held = [];
    recovering = false;
    for (const m of queued) dispatch(m);
    sink.onRecovered(result);
    attempt = 0;
    link({ state: 'open', connectionId: connectionId!, recovered: true });
    // 응답을 못 받은 명령: 같은 generation 이고 재시도 여유가 있을 때만 같은 request_id 로 재전송
    for (const [rid, p] of pending) {
      if (p.generation !== currentGeneration || p.retries >= MAX_RETRIES) { pending.delete(rid); continue; }
      p.retries++; p.sentAt = performance.now();
      const again = opts.resend(p.message);
      if (rawSend(again)) sink.onRetry(again, p.retries, '재접속 후');
    }
  }

  function openSocket(isReconnect: boolean): void {
    const sock = new WebSocket(wsUrl(wsPath!));
    ws = sock;
    lastServerMsgAt = performance.now();
    const readyTimer = setTimeout(() => {
      if (ws === sock && !connectionId) onSocketDown(sock, `${HELLO_READY_TIMEOUT_MS / 1000}초 안에 ready 가 없음`);
    }, HELLO_READY_TIMEOUT_MS);
    sock.addEventListener('open', () => { if (token) sock.send(JSON.stringify(helloMessage(token))); });
    sock.addEventListener('message', ev => {
      if (ws !== sock || typeof ev.data !== 'string') return;
      lastServerMsgAt = performance.now();
      let m: ServerMessage;
      try { m = JSON.parse(ev.data) as ServerMessage; } catch { return; }
      const v = readServerMessage(m);
      if (v.type === 'ready') {
        clearTimeout(readyTimer);
        connectionId = v.connectionId;
        everOpened = true;
        currentGeneration = snapshotOrder(v.snapshot).generation;
        sink.onSnapshot(v.snapshot, [], 'ready');
        if (isReconnect) void recover(currentGeneration);
        else { attempt = 0; link({ state: 'open', connectionId: v.connectionId, recovered: false }); }
        return;
      }
      if (!connectionId && v.type === 'error') {
        // 인증 전 오류 (hello 거절·CONTROL_CONFLICT 등): 재접속을 반복하지 않고 실패로
        clearTimeout(readyTimer);
        ws = null;
        link({ state: 'failed', step: 'hello', reason: v.code, code: v.code, retryable: v.retryable });
        sink.onServerError({ code: v.code, message: v.message, retryable: v.retryable, details: v.details, requestId: null, source: 'ws' });
        sock.close();
        return;
      }
      if (recovering && v.type === 'telemetry') { held.push(m); return; }
      dispatch(m);
    });
    sock.addEventListener('close', ev => {
      clearTimeout(readyTimer);
      onSocketDown(sock, `종료 코드 ${ev.code}${ev.reason ? ` · ${ev.reason}` : ''}`);
    });
  }

  // 소켓이 죽었다: 현재 소켓일 때만 처리 (이전 소켓의 늦은 이벤트 무시)
  function onSocketDown(sock: WebSocket, reason: string): void {
    if (ws !== sock) return;
    ws = null;
    connectionId = null;
    recovering = false; held = [];
    try { sock.close(); } catch { /* 이미 닫힘 */ }
    if (failed) return;
    if (!everOpened) { link({ state: 'failed', step: 'WebSocket 연결 · hello', reason, code: null, retryable: true }); return; }
    reconnectLater(reason);
  }

  function reconnectLater(reason: string): void {
    if (failed || reconnectTimer) return;
    attempt++;
    link({ state: 'reconnecting', attempt, reason });
    const delay = BACKOFF_MS[Math.min(attempt - 1, BACKOFF_MS.length - 1)]!;
    reconnectTimer = setTimeout(async () => {
      reconnectTimer = null;
      try {
        await fetchSnapshot(); // 세션이 살아 있는지 (만료·없음이면 재접속하지 않음)
      } catch (err) {
        if (err instanceof HttpError && err.code && ENDED.has(err.code)) return failWith('재접속', err);
        return reconnectLater(err instanceof Error ? err.message : String(err)); // 서버 응답 없음: 계속 시도
      }
      openSocket(true);
    }, delay);
  }

  function rawSend(m: ClientMessage): boolean {
    if (!ws || ws.readyState !== WebSocket.OPEN || !connectionId) return false;
    ws.send(JSON.stringify(m));
    return true;
  }

  // 생존 확인 + 명령 응답 대기
  setInterval(() => {
    const now = performance.now();
    if (ws && connectionId && now - lastServerMsgAt > SILENCE_MS) onSocketDown(ws, `${SILENCE_MS / 1000}초 동안 서버 메시지 없음`);
    if (!connectionId || recovering) return;
    for (const [rid, p] of pending) {
      if (p.checking || now - p.sentAt < ACK_TIMEOUT_MS) continue;
      if (p.retries >= MAX_RETRIES) {
        pending.delete(rid);
        if (ws) onSocketDown(ws, `명령 응답 없음 (${p.message.action}, 재시도 ${MAX_RETRIES}회)`); // 연결 복구 흐름으로
        return;
      }
      p.checking = true;
      fetchSnapshot()
        .then(() => {
          p.checking = false;
          if (!pending.has(rid)) return; // 그 사이 ack 도착
          if (p.generation !== currentGeneration && p.message.action !== 'session.reset') { pending.delete(rid); return; }
          p.retries++; p.sentAt = performance.now();
          const again = opts.resend(p.message);
          if (rawSend(again)) sink.onRetry(again, p.retries, `ack ${ACK_TIMEOUT_MS / 1000}초 없음 · GET 상태 확인 후`);
        })
        .catch(() => { p.checking = false; p.sentAt = performance.now(); });
    }
  }, 250);

  return {
    // body: 생성 요청 본문 (contract-access.createSessionBody). 직접 조종 + rule 은 {} (solver 생략 → rule)
    async start(body: CreateSessionRequest = {}): Promise<void> {
      link({ state: 'connecting', step: '세션 생성 (POST /sessions)' });
      try {
        // 같은 Idempotency-Key 로 재요청해야 중복 생성되지 않는다 (429 대기 후 재요청 포함)
        const res = await fetchWithRetryAfter(`${base}/sessions`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'Idempotency-Key': globalThis.crypto.randomUUID() },
          body: JSON.stringify(body),
        }, sink.onRateLimited);
        if (res.status !== 201) throw await httpFail(res);
        const created = readCreateSession((await res.json()) as CreateSessionResponse);
        sessionId = created.sessionId; token = created.token; wsPath = created.wsPath;
      } catch (e) {
        failWith('세션 생성', e);
        return;
      }
      link({ state: 'connecting', step: 'WebSocket 연결 · hello' });
      openSocket(false);
    },

    // 인증된 연결이 열려 있을 때만 보낸다. command 는 응답 대기 목록에 올린다. 반환: 보냈는지
    send(msg: ClientMessage): boolean {
      // 복구 중에도 ping 은 보낸다 (서버의 3초 생존 판정). 다른 메시지는 복구가 끝난 뒤
      if ((recovering && msg.type !== 'ping') || !rawSend(msg)) return false;
      const c = readSentCommand(msg);
      if (c) pending.set(c.requestId, { message: c.message, generation: c.generation, sentAt: performance.now(), retries: 0, checking: false });
      return true;
    },

    connectionId(): string | null { return connectionId; },

    // 인증된 연결이 있음 (복구 중 포함) — ping 대상
    authenticated(): boolean { return connectionId != null && ws?.readyState === WebSocket.OPEN; },

    // 조용한 재접속 (STALE_CONNECTION·STALE_CLIENT_SEQ, 시험용 끊기)
    forceReconnect(reason: string): void { if (ws) onSocketDown(ws, reason); },

    // 조용한 동기화 (STALE_GENERATION·INVALID_STATE·TUTORIAL_STEP_MISMATCH·INTERNAL_ERROR): 최신 Snapshot 받기
    resync(): void { if (sessionId && token) fetchSnapshot().catch(() => { /* 다음 telemetry 로 맞춰짐 */ }); },

    // 세션을 더 쓸 수 없음: 연결을 닫고 재접속하지 않는다 (상태 줄에도 반영)
    stop(code: string): void {
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      const s = ws; ws = null; connectionId = null;
      s?.close(1000, 'client stop');
      if (!failed) link({ state: 'failed', step: '세션 종료', reason: code, code, retryable: false });
      failed = true;
    },

    // [다시 시도] (CONTROL_CONFLICT 등): 같은 세션으로 다시 붙는다
    retryConnect(): void {
      if (!sessionId) return;
      failed = false; attempt = 0;
      reconnectLater('다시 시도');
    },

    // 시험용 목업 경로 (계약 외)
    async mockFault(path: string, body: object): Promise<void> {
      await fetch(`${base}/_mock/${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    },
  };
}
