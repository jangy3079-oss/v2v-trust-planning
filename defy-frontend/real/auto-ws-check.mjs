// 서버 단독 확인 (프론트 없이 WS 로). 실제 15c 서버를 자식 프로세스로 띄워 계약 동작을 본다.
//   node real/auto-ws-check.mjs manual [--real <real 폴더>] [--port 8799]   수동 세션 회귀 (TTL·멱등키·pause/resume·이벤트 라벨·명령 빈도)
//   node real/auto-ws-check.mjs auto   [--port 8799]                       자동 모드 (정지·수동 복귀·리셋·라벨)
// DEFY_PYTHON 은 그대로 넘긴다. 결과는 JSON 한 줄씩 출력하고, 마지막 줄이 요약이다.
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const mode = argv[0] ?? 'manual';
const opt = (n, d) => { const i = argv.indexOf(`--${n}`); return i >= 0 ? argv[i + 1] : d; };
const realDir = resolve(opt('real', here));
const PORT = Number(opt('port', 8799));
const BASE = `http://localhost:${PORT}/api/v1`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail = {}) => { results.push({ name, ok }); console.log(JSON.stringify({ name, ok, ...detail })); };

// ---- 서버
function startServer() {
  const child = spawn(process.execPath, [join(realDir, 'server.ts')], {
    cwd: resolve(realDir, '..'), env: { ...process.env, MOCK_PORT: String(PORT) }, stdio: ['ignore', 'pipe', 'inherit'],
  });
  return new Promise((ok, fail) => {
    child.stdout.on('data', d => { if (String(d).includes('15c 서버')) ok(child); if (process.env.WS_CHECK_VERBOSE) process.stdout.write(d); });
    child.on('exit', c => fail(new Error(`server exited ${c}`)));
  });
}
async function post(body, key = randomUUID()) {
  const r = await fetch(`${BASE}/sessions`, { method: 'POST', headers: { 'content-type': 'application/json', 'idempotency-key': key }, body: JSON.stringify(body) });
  return { status: r.status, json: await r.json() };
}

// ---- WS 클라이언트
class Client {
  constructor(created) {
    this.c = created; this.seq = 0; this.snap = created.snapshot; this.events = []; this.errors = []; this.acks = [];
    this.pinger = null; this.inputTimer = null; this.held = [];
  }
  open() {
    return new Promise((ok, fail) => {
      this.ws = new WebSocket(`ws://localhost:${PORT}${this.c.ws_path}`);
      this.ws.on('open', () => this.ws.send(JSON.stringify({ protocol_version: 'v1', type: 'hello', session_token: this.c.session_token })));
      this.ws.on('message', d => {
        const m = JSON.parse(String(d));
        if (m.type === 'ready') { this.conn = m.connection_id; this.snap = m.snapshot; this.startPing(); ok(this); }
        if (m.snapshot) this.snap = m.snapshot;
        if (m.type === 'telemetry') this.events.push(...m.events);
        if (m.type === 'error') this.errors.push(m);
        if (m.type === 'ack') this.acks.push(m);
      });
      this.ws.on('error', fail);
    });
  }
  base(type) { return { protocol_version: 'v1', type, connection_id: this.conn, client_seq: ++this.seq }; }
  startPing() { this.pinger = setInterval(() => this.ws.readyState === 1 && this.ws.send(JSON.stringify({ ...this.base('ping'), client_time_ms: Date.now() })), 1000); }
  input(held = [], tapped = []) { this.ws.send(JSON.stringify({ ...this.base('input.state'), generation: this.snap.generation, held, tapped })); }
  keepInput(held = []) { this.held = held; if (!this.inputTimer) this.inputTimer = setInterval(() => this.input(this.held), 250); }
  stopInput() { clearInterval(this.inputTimer); this.inputTimer = null; }
  cmd(action, payload = {}) {
    const request_id = randomUUID();
    this.ws.send(JSON.stringify({ ...this.base('command'), request_id, generation: this.snap.generation, action, payload }));
    return request_id;
  }
  async waitFor(pred, ms = 5000) {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (pred(this.snap)) return Date.now() - t0; await sleep(20); }
    return null;
  }
  async finishTutorial() {
    for (let st = 0; st < 4; st++) { this.cmd('tutorial.advance', { expected_step: st }); await this.waitFor(s => s.tutorial.step !== st, 2000); }
    return this.waitFor(s => s.phase === 'running', 2000);
  }
  close() { clearInterval(this.pinger); this.stopInput(); this.ws.terminate(); }
}
const evKinds = (c, from = 0) => c.events.slice(from).map(e => e.kind);

// ---- 수동 세션
async function manual() {
  const h = await (await fetch(`${BASE}/health`)).json();
  console.log(JSON.stringify({ name: 'health', auto_drive_available: h.auto_drive_available }));

  // 멱등키: 기존 형식(자동 필드 없음)
  const key = randomUUID();
  const a = await post({ solver: 'rule' }, key), b = await post({ solver: 'rule' }, key), d = await post({}, key), x = await post({ solver: 'nmpc' }, key);
  check('idem.same_body_same_session', a.status === 201 && b.status === 201 && a.json.session_id === b.json.session_id, { s1: a.json.session_id, s2: b.json.session_id });
  check('idem.omitted_solver_same_session', d.status === 201 && d.json.session_id === a.json.session_id, { status: d.status });
  check('idem.diff_body_conflict', x.status === 409 && x.json.error?.code === 'IDEMPOTENCY_CONFLICT', { status: x.status, code: x.json.error?.code });

  const created = (await post({ solver: 'rule' })).json;
  check('snapshot.no_auto', created.snapshot.auto === undefined || created.snapshot.auto.enabled === false, { auto: created.snapshot.auto ?? null });
  const c = await new Client(created).open();
  await c.finishTutorial();
  check('tutorial.running', c.snap.phase === 'running');

  // 입력 TTL 750ms
  c.keepInput(['W']); await sleep(1500);
  const okRun = c.snap.phase === 'running';
  c.stopInput(); const t0 = Date.now();
  const dt = await c.waitFor(s => s.phase === 'paused', 3000);
  check('ttl.input_timeout', okRun && dt != null && c.snap.runtime.pause_reason === 'input_timeout', { after_ms: dt != null ? Date.now() - t0 : null, reason: c.snap.runtime.pause_reason });
  check('ttl.no_auto_field', c.snap.auto === undefined);

  // pause / resume
  c.cmd('session.resume'); await c.waitFor(s => s.phase === 'running', 2000); c.keepInput([]);
  const rOk = c.snap.phase === 'running';
  c.cmd('session.pause', { reason: 'user' }); await c.waitFor(s => s.phase === 'paused', 2000);
  const pOk = c.snap.phase === 'paused' && c.snap.runtime.pause_reason === 'user';
  await sleep(1200); const stillPaused = c.snap.phase === 'paused';
  c.cmd('session.resume'); await c.waitFor(s => s.phase === 'running', 2000);
  check('pause_resume', rOk && pOk && stillPaused && c.snap.phase === 'running');

  // 이벤트 라벨: 사람이 해제 → stopped, 스스로 끝남 → finished
  let from = c.events.length;
  c.cmd('scenario.start', { kind: 'drift' }); await sleep(1000); c.cmd('scenario.stop'); await sleep(600);
  const k1 = evKinds(c, from).filter(k => k.startsWith('scenario.'));
  check('label.human_stop_stopped', JSON.stringify(k1) === JSON.stringify(['scenario.started', 'scenario.stopped']), { kinds: k1 });
  from = c.events.length;
  c.cmd('scenario.start', { kind: 'brake' }); await sleep(3000);
  const k2 = evKinds(c, from).filter(k => k.startsWith('scenario.'));
  check('label.self_end_finished', JSON.stringify(k2) === JSON.stringify(['scenario.started', 'scenario.finished']), { kinds: k2 });

  // 명령 빈도: 1초에 30개 → 오류 코드 집계 (기준선과 비교)
  const e0 = c.errors.length, a0 = c.acks.length;
  for (let i = 0; i < 30; i++) { c.cmd('signal.toggle', { side: 'left' }); await sleep(33); }
  await sleep(500);
  const codes = c.errors.slice(e0).map(e => e.code);
  console.log(JSON.stringify({ name: 'rate.30_per_s', acks: c.acks.length - a0, errors: codes.length, rate_limited: codes.filter(x => x === 'RATE_LIMITED').length }));
  c.close();
}

// ---- 자동 모드
async function auto() {
  const h = await (await fetch(`${BASE}/health`)).json();
  check('health.auto_available', h.auto_drive_available === true);
  const key = randomUUID();
  const a = await post({ auto_drive: true, auto_level: 'high' }, key), b = await post({ auto_drive: true, auto_level: 'high' }, key);
  const x = await post({ auto_drive: true, auto_level: 'high', auto_seed: a.json.snapshot.auto?.seed }, key);
  check('auto.server_seed', Number.isSafeInteger(a.json.snapshot.auto?.seed) && a.json.session_id === b.json.session_id, { seed: a.json.snapshot.auto?.seed });
  check('auto.idem_as_sent', x.status === 409, { status: x.status });

  const c = await new Client((await post({ auto_drive: true, auto_seed: 7, auto_level: 'high' })).json).open();
  check('auto.tutorial_first', c.snap.phase === 'tutorial' && c.snap.auto.state === 'cruise');
  await c.finishTutorial();
  c.keepInput([]);                               // 빈 입력은 개입이 아니다
  await sleep(1500);
  check('auto.empty_input_not_takeover', c.snap.auto.state !== 'manual' && c.snap.inputs.held_received.length === 0, { state: c.snap.auto.state });
  c.stopInput(); await sleep(1500);              // TTL 면제
  check('auto.ttl_exempt', c.snap.phase === 'running', { phase: c.snap.phase });

  // 이벤트 하나가 시작될 때까지 (cruise 10초 + 대기 4~10초 + 접근)
  await c.waitFor(s => s.auto.state === 'event' && s.scenario != null, 60000);
  const tick0 = c.snap.sample_tick;
  c.cmd('session.pause', { reason: 'user' }); await c.waitFor(s => s.phase === 'paused', 2000);
  const pTick = c.snap.sample_tick, pHeld = c.snap.inputs.held_applied.length; await sleep(1500);
  check('auto.pause_stops', c.snap.phase === 'paused' && c.snap.sample_tick === pTick && pHeld === 0, { tick0, pTick, now: c.snap.sample_tick });
  const left = c.snap.auto.next_event_in_s;
  c.cmd('session.resume'); await c.waitFor(s => s.phase === 'running', 2000);
  check('auto.resume_continues', c.snap.auto.state !== 'manual' && c.snap.auto.state !== 'cruise', { state: c.snap.auto.state, left });

  // 끊김 → 재접속: 자동 재개 없음
  const tok = { ...c.c }; c.close(); await sleep(500);
  const c2 = await new Client(tok).open(); c2.seq = 0;
  const dTick = c2.snap.sample_tick; await sleep(1500);
  check('auto.disconnect_no_autoresume', c2.snap.phase === 'paused' && c2.snap.runtime.pause_reason === 'disconnected' && c2.snap.sample_tick === dTick);
  c2.cmd('session.resume'); await c2.waitFor(s => s.phase === 'running', 2000);

  // 운전자가 넣은 0 은 stopped: 횡방향 시나리오가 끝날 때까지 본다
  const from = c2.events.length;
  const lat = await c2.waitFor(s => s.scenario && ['drift', 'stuck', 'weave'].includes(s.scenario.kind), 120000);
  let label = null;
  if (lat != null) {
    await c2.waitFor(s => !s.scenario, 20000); await sleep(300);
    const ks = evKinds(c2, from).filter(k => k.startsWith('scenario.'));
    label = ks[ks.indexOf('scenario.started') + 1] ?? null;
  }
  check('auto.driver_zero_is_stopped', label === 'scenario.stopped', { label });

  // 사람 입력 → 수동 복귀, 입력이 섞이지 않음, TTL 재적용
  c2.keepInput(['W']); await c2.waitFor(s => s.auto.state === 'manual', 2000); await sleep(300);
  const s = c2.snap;
  check('auto.human_input_manual', s.auto.state === 'manual' && s.auto.stopped_reason === 'human_input' && JSON.stringify(s.inputs.held_applied) === '["W"]',
    { state: s.auto.state, reason: s.auto.stopped_reason, held_applied: s.inputs.held_applied });
  c2.stopInput(); await c2.waitFor(t => t.phase === 'paused', 3000);
  check('auto.manual_ttl_back', c2.snap.runtime.pause_reason === 'input_timeout', { reason: c2.snap.runtime.pause_reason });

  // 리셋 → 안내 4단계 → 같은 시드로 cruise 부터
  c2.cmd('session.reset'); await c2.waitFor(t => t.phase === 'tutorial', 2000);
  const rs = c2.snap;
  check('auto.reset_halts', rs.phase === 'tutorial' && rs.auto.state === 'cruise' && rs.auto.seed === 7, { state: rs.auto.state, seed: rs.auto.seed });
  await c2.finishTutorial(); await sleep(500);
  check('auto.reset_restarts_cruise', c2.snap.phase === 'running' && c2.snap.auto.state === 'cruise' && c2.snap.auto.seed === 7);

  // 사람 명령 → human_command
  c2.cmd('signal.toggle', { side: 'right' }); await c2.waitFor(t => t.auto.state === 'manual', 2000);
  check('auto.human_command_manual', c2.snap.auto.stopped_reason === 'human_command', { reason: c2.snap.auto.stopped_reason });
  c2.close();
}

const server = await startServer();
try {
  await (mode === 'auto' ? auto() : manual());
} catch (err) {
  console.error(err); results.push({ name: 'crash', ok: false });
} finally {
  server.kill();
}
const failed = results.filter(r => !r.ok).map(r => r.name);
console.log(JSON.stringify({ summary: mode, real: realDir, passed: results.length - failed.length, failed }));
process.exit(failed.length ? 1 : 0);
