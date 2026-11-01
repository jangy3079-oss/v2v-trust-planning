/*
 * 데이터 근거 지표 계산 — 화면 값이 서버에서 실시간으로 왔다는 근거를 모은다.
 * 입력만 받는 구조: onSnapshot(받은 Snapshot) · onFrame(렌더 프레임) · onSent(보낸 메시지) · onPong(RTT) · setSource.
 * 원칙: 출처가 'server' 가 아니면 서버에서만 알 수 있는 값은 만들지 않고 disconnected 로 둔다.
 *       로컬에서 정직하게 잴 수 있는 값(수신 간격·렌더 fps·경과·수신 순서)은 측정하되 출처를 함께 표시한다.
 * 필드 읽기는 contract-access.ts (readProvenance / readSentInput) 를 통해서만 한다.
 */
import type { ClientMessage, DriveKey, Snapshot } from '../contracts';
import { readProvenance, readSentInput, type ProvenanceView } from '../contract-access';

export type DataSource = 'sample' | 'static' | 'server';

// 서버 전용 값: 서버 연결이 아니면 'disconnected', 연결됐지만 아직 값이 없으면 'none'
export type Reading<T> = { state: 'ok'; value: T } | { state: 'disconnected' } | { state: 'none' };

export interface ProvenanceMetrics {
  source: DataSource;
  // 로컬 측정 (source 가 server 가 아니면 "로컬 샘플 기준")
  recvHz: number;
  sinceLastMs: number | null;
  renderFps: number;
  seq: { lastTick: number | null; lastFrame: number | null; frameDup: number; frameBack: number; tickSkip: number; tickBack: number; received: number };
  sentHeld: DriveKey[] | null;
  sentSeq: number | null;
  // 서버 전용
  clockOffsetMs: Reading<number>;     // server_time − 내 시계 (수신 시점)
  stepMs: Reading<number | null>;
  lagMs: Reading<number>;
  rttMs: Reading<number>;
  inputLatencyMs: Reading<number>;    // 보낸 client_seq 가 last_applied_input_seq 로 돌아오기까지
  lastAppliedSeq: Reading<number | null>;
  heldApplied: Reading<DriveKey[]>;
  sessionId: Reading<string>;
  generation: Reading<number>;
  protocolVersion: Reading<string>;
  inputConnectionId: Reading<string | null>;
  localGeneration: number | null;     // 샘플 재생기가 매긴 세대 (서버 값 아님)
  hasSnapshot: boolean;
  link: { text: string; bad: boolean } | null; // 서버 모드 연결 상태
  reconnects: number;
  recovered: { samples: number; events: number; truncated: boolean } | null; // 마지막 복구 결과
}

const WINDOW_MS = 1000;
const SENT_KEEP = 256;

export function createProvenance() {
  let source: DataSource = 'sample';
  let recvTimes: number[] = [];
  let frameTimes: number[] = [];
  let last: { p: ProvenanceView; recvMs: number; clockOffsetMs: number } | null = null;
  let raw: Snapshot | null = null;
  let seq = emptySeq();
  const sent = new Map<number, number>(); // client_seq → 보낸 시각 (같은 connection 안에서만 의미)
  let sentConn: string | null = null;
  let sentHeld: DriveKey[] | null = null;
  let sentSeq: number | null = null;
  let inputLatencyMs: number | null = null;
  let rttMs: number | null = null;
  let link: { text: string; bad: boolean } | null = null;
  let reconnects = 0;
  let recovered: ProvenanceMetrics['recovered'] = null;

  function emptySeq() {
    return { lastTick: null as number | null, lastFrame: null as number | null, frameDup: 0, frameBack: 0, tickSkip: 0, tickBack: 0, received: 0 };
  }

  const prune = (arr: number[], now: number) => { while (arr.length && arr[0]! < now - WINDOW_MS) arr.shift(); };
  const server = <T>(v: T | undefined, has: boolean): Reading<T> =>
    source !== 'server' ? { state: 'disconnected' } : has && v !== undefined ? { state: 'ok', value: v } : { state: 'none' };

  return {
    setSource(s: DataSource): void { source = s; },

    // 서버 모드 연결 상태 (끊김·재연결·복구)
    setLink(l: { text: string; bad: boolean } | null, reconnecting: boolean): void {
      if (reconnecting && !(link?.bad ?? false)) reconnects++;
      link = l;
    },
    onRecovered(r: { samples: number; events: number; truncated: boolean }): void { recovered = r; },

    // 세션·모드가 바뀌면 처음부터 다시 잰다
    reset(): void {
      recvTimes = []; last = null; raw = null; seq = emptySeq();
      sent.clear(); sentConn = null; sentHeld = null; sentSeq = null; inputLatencyMs = null; rttMs = null;
    },

    // 받은 모든 Snapshot (렌더 버퍼가 거르기 전)
    onSnapshot(s: Snapshot, recvMs: number): void {
      const p = readProvenance(s);
      recvTimes.push(recvMs);
      prune(recvTimes, recvMs);
      // 수신 순서: generation 이 바뀌면 다시 센다. frame_seq 는 단조 증가, sample_tick 은 같거나 +1
      if (last && p.generation !== last.p.generation) seq = emptySeq();
      if (seq.lastFrame != null) {
        if (p.frameSeq === seq.lastFrame) seq.frameDup++;
        else if (p.frameSeq < seq.lastFrame) seq.frameBack++;
      }
      if (seq.lastTick != null) {
        if (p.sampleTick > seq.lastTick + 1) seq.tickSkip += p.sampleTick - seq.lastTick - 1;
        else if (p.sampleTick < seq.lastTick) seq.tickBack++;
      }
      seq.lastFrame = p.frameSeq; seq.lastTick = p.sampleTick; seq.received++;
      // 입력→반영: 내가 보낸 seq 가 같은 연결로 반영되었을 때만
      if (p.lastAppliedInputSeq != null && p.inputConnectionId != null && p.inputConnectionId === sentConn) {
        const t = sent.get(p.lastAppliedInputSeq);
        if (t != null) inputLatencyMs = recvMs - t;
      }
      last = { p, recvMs, clockOffsetMs: Date.parse(p.serverTime) - Date.now() };
      raw = s;
    },

    onFrame(nowMs: number): void {
      frameTimes.push(nowMs);
      prune(frameTimes, nowMs);
    },

    onSent(m: ClientMessage, sentMs: number): void {
      const inp = readSentInput(m);
      if (!inp) return;
      if (sentConn !== inp.connectionId) { sent.clear(); sentConn = inp.connectionId; }
      sent.set(inp.clientSeq, sentMs);
      if (sent.size > SENT_KEEP) sent.delete(sent.keys().next().value!);
      sentHeld = inp.held; sentSeq = inp.clientSeq;
    },

    // 서버 연결 단계: pong 의 client_time_ms 를 넘긴다
    onPong(clientTimeMs: number, nowMs: number): void { rttMs = nowMs - clientTimeMs; },

    snapshotJson(): string | null { return raw ? JSON.stringify(raw, null, 2) : null; },

    metrics(nowMs: number): ProvenanceMetrics {
      prune(recvTimes, nowMs);
      prune(frameTimes, nowMs);
      const p = last?.p;
      const has = !!p;
      return {
        source,
        recvHz: recvTimes.length * (1000 / WINDOW_MS),
        sinceLastMs: last ? nowMs - last.recvMs : null,
        renderFps: frameTimes.length * (1000 / WINDOW_MS),
        seq: { ...seq },
        sentHeld, sentSeq,
        clockOffsetMs: server(last?.clockOffsetMs, has),
        stepMs: server(p?.stepMs, has),
        lagMs: server(p?.lagMs, has),
        rttMs: server(rttMs ?? undefined, rttMs != null),
        inputLatencyMs: server(inputLatencyMs ?? undefined, inputLatencyMs != null),
        lastAppliedSeq: server(p?.lastAppliedInputSeq, has),
        heldApplied: server(p?.heldApplied, has),
        sessionId: server(p?.sessionId, has),
        generation: server(p?.generation, has),
        protocolVersion: server(p?.protocolVersion, has),
        inputConnectionId: server(p?.inputConnectionId, has),
        localGeneration: source !== 'server' && p ? p.generation : null,
        hasSnapshot: has,
        link, reconnects, recovered,
      };
    },
  };
}
