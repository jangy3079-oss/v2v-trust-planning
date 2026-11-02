// 조립: DemoConfig → 뷰 생성 → 데이터 출처(샘플 재생 또는 서버 WebSocket) → 렌더 버퍼 → 60fps 루프, 입력 → 메시지
import '@fontsource/ibm-plex-sans-kr/400.css';
import '@fontsource/ibm-plex-sans-kr/500.css';
import '@fontsource/ibm-plex-sans-kr/600.css';
import '@fontsource/ibm-plex-sans-kr/700.css';
import '@fontsource/barlow-semi-condensed/500.css';
import '@fontsource/barlow-semi-condensed/600.css';
import './styles.css';
import type { ClientMessage, DemoConfig, DemoEvent, Snapshot } from './contracts';
import { createSessionBody, readConfig, readEvent, readSnapshot, snapshotOrder, type EventView, type SnapshotView } from './contract-access';
import { AUTO_START_LEVEL, DATA_SOURCE, DEV_TOOLS, FORCE_MANUAL, SERVER_BASE_URL } from './config';
import { SAMPLE_DEMO_CONFIG, STATIC_FRAMES } from './source/fixtures';
import { createSamplePlayer } from './source/sample-player';
import { createServerSource, fetchAutoDriveAvailable, fetchDemoConfig, type LinkState, type SnapshotKind } from './source/ws-client';
import { createRoadView } from './view/road';
import { createChartHistory, createChartView } from './view/chart';
import { createPanel } from './view/panel';
import { createKeysView } from './view/keys';
import { createAutoView } from './view/auto';
import { createRenderBuffer } from './view/interp';
import { esc } from './view/format';
import { createInputController, type SendKind } from './input/controller';
import { LOCAL_CONNECTION_ID } from './input/messages';
import { createPauseOverlay } from './ui/pause';
import { createProvenance } from './view/provenance';
import { createTutorialOverlay } from './ui/tutorial';
import { createConnectionOverlay, linkLabel, linkPhase } from './ui/connection';
import { createErrorUi, newSessionAction, type ErrorCardAction } from './ui/error';
import { errorPolicy } from './source/error-policy';
import { AUTO_TEXT, BUTTON, ERROR_TEXT, LOCK_TEXT, RATE_LIMIT_WAIT } from './ui/text-ko';
import { FAULTS } from './ui/debug-faults';
import type { ServerErrorView } from './source/ws-client';

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} 요소가 없습니다`);
  return e as T;
}

const STALE_MS = 250;    // 명세 4.2
const LOG_LINES = 80;
const PING_MS = 1000;    // 명세 6.2: ping 1초 주기

// 주소 해시 옵션: #debug (디버그 창 표시), frame=<id> (정적 확인 프레임). 예: #debug&frame=nmpc. 개발 모드에서만 (시연 빌드는 무시)
const hashOpts = new Set(DEV_TOOLS ? location.hash.replace(/^#/, '').split('&').filter(Boolean) : []);
const DEBUG = hashOpts.has('debug');
const hashFrame = [...hashOpts].find(o => o.startsWith('frame='))?.slice('frame='.length) ?? '';

const connUi = createConnectionOverlay();
const errorUi = createErrorUi();

// DemoConfig: 서버 모드는 서버에서, 샘플 모드는 샘플 DemoConfig. 서버를 못 찾으면 샘플로 바꾸지 않는다
async function loadDemoConfig(): Promise<DemoConfig | null> {
  if (DATA_SOURCE !== 'server') return SAMPLE_DEMO_CONFIG;
  connUi.show({ state: 'connecting', step: 'DemoConfig (GET /demo-config)' });
  try {
    return await fetchDemoConfig(SERVER_BASE_URL);
  } catch (e) {
    // 서버를 못 찾으면 샘플로 바꾸지 않고 오류 카드만
    console.warn('[server] DemoConfig 실패', e);
    connUi.hide();
    errorUi.show('CONNECTION_FAILED', [newSessionAction()]);
    return null;
  }
}

function boot(demoConfig: DemoConfig): void {
  const cfg = readConfig(demoConfig);
  const server = DATA_SOURCE === 'server';

  const road = createRoadView(el<HTMLCanvasElement>('road'), cfg);
  const chart = createChartView(el<HTMLCanvasElement>('chart'), cfg);
  const history = createChartHistory(cfg.historySamples);
  const buffer = createRenderBuffer(cfg.renderBufferMs);
  const panel = createPanel({
    status: el('statusBar'), trustBody: el('trustBody'), trustRefs: el('trustRefs'), trustLegend: el('trustLegend'), egoSummary: el('egoSummary'),
    acc: el('accBox'), score: el('scoreBox'), context: el('contextBox'), egoMore: el('egoMoreBox'),
    scenario: el('scenarioBox'), scenarioMore: el('scenarioMoreBox'), log: el('logBox'),
    sumScore: el('sumScore'), sumContext: el('sumContext'), sumScenario: el('sumScenario'),
    provSource: el('provSource'), provNote: el('provNote'), provEvidenceNote: el('provEvidenceNote'), provEvidence: el('provEvidence'),
    provConn1: el('provConn1'), provConn2: el('provConn2'),
    sumProvenance: el('sumProvenance'),
  }, cfg);
  // 데이터 근거 지표 (입력만 받는다: 수신·렌더·보낸 메시지·pong)
  const provenance = createProvenance();
  const PROVENANCE_RENDER_MS = 250;
  let provRenderedAt = 0;
  const keys = createKeysView(el('keyRows'), el('keyNote'), cfg);
  // 자동 모드 표시: Snapshot.auto 가 있을 때만 요소를 만든다 (수동 세션 화면은 그대로)
  const autoView = createAutoView(el('roadDock'), el('keys'), cfg);

  const state = {
    mode: (server ? 'server' : 'play') as 'play' | 'static' | 'server',
    latest: null as SnapshotView | null, // 마지막으로 받은 표본 (입력 메시지의 phase·generation 기준)
    events: [] as EventView[],
    eventGen: null as number | null,
    pressed: new Set<string>() as ReadonlySet<string>, // 물리 키 눌림 (표시용)
    shown: null as SnapshotView | null,                // 패널에 마지막으로 그린 표본
    shownStale: false,
    refreshCount: 0,
    showHud: false,
    link: null as LinkState | null,                     // 서버 모드 연결 상태
    fatal: null as string | null,                       // 세션을 더 쓸 수 없는 오류 코드 (운전 잠금)
    lock: null as string | null,                        // 지금 표시 중인 운전 잠금 이유
    autoRequested: false,                               // 자동 세션을 요청했음 (지원 안 함 토스트 1회)
  };

  // ---- 수신: 샘플 재생기·정적 프레임·서버(ready/telemetry/ack) 공통 ----
  function receive(snap: Snapshot, events: readonly DemoEvent[], kind: SnapshotKind = 'telemetry'): void {
    const now = performance.now();
    // 근거 지표는 수신 스트림(ready/telemetry) 기준. ack·GET 의 snapshot 은 같은 표본의 재전송일 수 있다
    if (kind === 'ready' || kind === 'telemetry') provenance.onSnapshot(snap, now);
    const o = snapshotOrder(snap);
    const cur = state.latest;
    const older = !!cur && (o.generation < cur.generation || (o.generation === cur.generation && o.frameSeq <= cur.frameSeq));
    if (!older) {
      const view = readSnapshot(snap);
      // 자동을 요청했는데 서버가 auto 없이 수동 세션을 만들었다 (명세 5.1): 첫 표본에서 한 번 알린다
      if (state.autoRequested) { state.autoRequested = false; if (!view.auto) errorUi.toast(AUTO_TEXT.unsupported, 6000); }
      if (buffer.push(view, now)) history.clear(); // 새 generation: 이전 그래프 비움
      state.latest = view;
      history.add(view);
      pause.render(view, linkPhase(state.link)); // 정지 오버레이는 최신 상태 기준으로 즉시
    }
    if (cur && o.generation < cur.generation) return; // 과거 세대 이벤트 무시
    mergeEvents(o.generation, events);
  }

  // 이벤트 합치기: generation 별, event_seq 로 중복 제거·정렬 (telemetry 와 GET /events 공통). 반환: 새로 합친 수
  function mergeEvents(generation: number, events: readonly DemoEvent[]): number {
    if (state.eventGen !== generation) {
      if (state.eventGen != null && generation < state.eventGen) return 0;
      state.events = []; state.eventGen = generation;
    }
    let added = 0;
    for (const e of events) {
      const ev = readEvent(e);
      if (ev.generation !== generation || state.events.some(x => x.eventSeq === ev.eventSeq)) continue;
      state.events.push(ev);
      added++;
    }
    if (added) state.events.sort((a, b) => a.eventSeq - b.eventSeq);
    if (state.events.length > 200) state.events.splice(0, state.events.length - 200);
    panel.renderLog(state.events);
    return added;
  }

  // ---- 보낸 메시지: 샘플 모드는 디버그 창(#debug)·콘솔만, 서버 모드는 WebSocket 으로 실제 전송 ----
  const msgLatest = el<HTMLPreElement>('msgLatest');
  const suppressInput = el<HTMLInputElement>('dbgNoInput');
  const msgLog = el<HTMLPreElement>('msgLog');
  const refreshCountEl = el('refreshCount');
  const stamp = () => new Date().toISOString().slice(11, 23);

  function logLine(html: string): void {
    const div = document.createElement('div');
    div.innerHTML = html;
    msgLog.prepend(div);
    while (msgLog.childElementCount > LOG_LINES) msgLog.lastElementChild?.remove();
  }

  const player = createSamplePlayer({ onSnapshot: (s, e) => receive(s, e) });
  const serverSource = server ? createServerSource(SERVER_BASE_URL, {
    onSnapshot: receive,
    // 재접속 복구: 현재 generation 의 history 만 그래프에 합친다 (빈 구간은 선으로 잇지 않음)
    onHistory: (gen, pts) => (state.latest?.generation === gen ? history.addPoints(gen, pts) : 0),
    onEvents: (gen, evs) => mergeEvents(gen, evs),
    onRecovered: r => {
      provenance.onRecovered(r);
      logLine(`<span class="note">${stamp()} 재접속 복구: 그래프 표본 +${r.samples}, 이벤트 +${r.events}${r.truncated ? ' (보존 범위 밖 truncated)' : ''}</span>`);
    },
    onPong: t => provenance.onPong(t, Date.now()),
    onRetry: (msg, n, why) => {
      console.log('[send:retry]', msg);
      logLine(`<span class="cmd">${stamp()} 재전송 ${n}회 (${esc(why)}) ${esc(JSON.stringify(msg))}</span>`);
    },
    onServerError: err => handleError(err),
    onRateLimited: s => errorUi.toast(RATE_LIMIT_WAIT(s), (s + 1) * 1000),
    onLink: link => {
      state.link = link;
      connUi.show(link);
      // 코드 없는 실패(서버에 닿지 못함)는 여기서, 코드 있는 실패는 handleError 에서 카드 표시
      if (link.state === 'failed' && !link.code && !state.fatal) lockFatal('CONNECTION_FAILED', [newSessionAction()]);
      provenance.setLink(linkLabel(link), link.state === 'reconnecting');
      // 끊김·복구 중: 입력 버퍼를 비운다. 아직 누른 키는 재접속 뒤 keyup → 새 keydown 부터 (A12)
      if (link.state !== 'open') input.clearInputs();
      pause.render(state.latest, linkPhase(link));
      state.shown = null; // 상태 줄 다시 그리기
    },
  }, { resend: m => input.resendCommand(m) }) : null;

  // ---- 오류 코드별 처리 (명세 9, source/error-policy.ts). details 는 콘솔에만, 화면에는 우리 문구와 코드만 ----
  function handleError(err: ServerErrorView): void {
    const policy = errorPolicy(err.code);
    console.warn(`[server:error] ${err.code} → ${policy}`, { message: err.message, details: err.details, requestId: err.requestId, source: err.source });
    logLine(`<span class="note">${stamp()} 서버 오류 ${esc(err.code)} → ${policy}${err.requestId ? ' (해당 명령 취소)' : ''}</span>`);
    switch (policy) {
      case 'cancel': return; // 해당 명령만 취소 (ws-client 가 응답 대기 목록에서 지움)
      case 'bug': console.error('[bug] 자동 재시도하지 않음', err.code, err.requestId); return;
      case 'resync': input.allowTutorialRetry(); serverSource?.resync(); return;
      case 'reconnect': serverSource?.forceReconnect(`${err.code} — 최신 연결·커서 확인 후 재접속`); return;
      case 'rateLimited': errorUi.toast(`${ERROR_TEXT.RATE_LIMITED!.title} — 입력 전송을 잠시 줄였습니다`); input.slowDown(10_000); return;
      case 'internal': errorUi.toast(ERROR_TEXT.INTERNAL_ERROR!.title, 6000); serverSource?.resync(); return;
      case 'ended': return lockFatal(err.code, [newSessionAction()]);
      case 'conflict': return lockFatal(err.code, [{ label: BUTTON.retry, run: () => { state.fatal = null; serverSource?.retryConnect(); } }]);
      case 'unavailable': return lockFatal(err.code, [err.retryable ? { label: BUTTON.retry, run: () => location.reload() } : newSessionAction()]);
    }
  }

  // 세션을 더 쓸 수 없음: 연결을 멈추고 입력을 비우고 카드 표시 (운전 잠금)
  function lockFatal(code: string, actions: ErrorCardAction[]): void {
    state.fatal = code;
    serverSource?.stop(code);
    input.clearInputs();
    errorUi.show(code, actions);
    state.shown = null;
  }

  // 운전 입력이 막힌 이유 (키 영역·도로에 작게)
  function lockReason(): string | null {
    if (state.fatal) return LOCK_TEXT.fatal;
    if (state.mode === 'server' && !linkOpen()) return LOCK_TEXT.link;
    const v = state.latest;
    if (!v || state.mode === 'static') return null;
    if (v.phase === 'error') return LOCK_TEXT.error;
    if (v.phase === 'tutorial') return LOCK_TEXT.tutorial;
    if (v.phase === 'paused') return LOCK_TEXT.paused;
    if (input.completionLocked()) return LOCK_TEXT.completion;
    if (input.slowed) return LOCK_TEXT.rateLimited;
    return null;
  }

  function onSend(msg: ClientMessage, kind: SendKind): void {
    // 시험: 입력 TTL 확인용으로 input.state 만 보내지 않음 (#debug 체크박스)
    if (serverSource && msg.type === 'input.state' && suppressInput.checked) return;
    if (serverSource && !serverSource.send(msg)) {
      logLine(`<span class="note">${stamp()} 전송 안 됨 (서버 연결 없음): ${esc(msg.type)}</span>`);
      return;
    }
    provenance.onSent(msg, performance.now());
    if (kind === 'ping') { console.debug('[send:ping]', msg); return; }
    const json = JSON.stringify(msg);
    if (msg.type === 'input.state') {
      msgLatest.textContent = json;
      if (kind === 'refresh') {
        // 250ms 주기 재전송은 로그를 덮지 않도록 횟수만 (콘솔은 verbose 레벨)
        state.refreshCount++;
        refreshCountEl.textContent = `· 주기 재전송 ${state.refreshCount}회`;
        console.debug('[send:refresh]', msg);
        return;
      }
    }
    console.log(`[send:${kind}]`, msg);
    logLine(`<span class="${msg.type === 'command' ? 'cmd' : ''}">${stamp()} ${esc(json)}</span>`);
  }

  const linkOpen = () => state.link?.state === 'open';
  const input = createInputController({
    cfg,
    getConnectionId: () => serverSource?.connectionId() ?? LOCAL_CONNECTION_ID,
    // 메시지를 만들 수 있는 상태: 샘플 재생 중, 또는 서버 연결이 열려 있을 때
    getSession: () => {
      const v = state.latest;
      if (!v || state.mode === 'static' || (state.mode === 'server' && !linkOpen())) return null;
      return { phase: v.phase, generation: v.generation, tutorialStep: v.tutorial.step };
    },
    send: onSend,
    note: text => { console.info('[input]', text); logLine(`<span class="note">${stamp()} ${esc(text)}</span>`); },
    onPhysical: pressed => {
      state.pressed = new Set(pressed);
      if (state.shown) keys.render(state.shown, state.pressed, state.lock);
    },
    // 샘플 모드에서만 가짜 서버(재생기)에 직접 반영. 서버 모드는 보낸 명령의 ack/telemetry 로 반영된다
    onPauseRequested: () => { if (state.mode === 'play') player.pause('focus_lost'); },
    onResumeRequested: () => { if (state.mode === 'play') player.resume(); },
    onResetRequested: () => { if (state.mode === 'play') player.reset(); },
    onTutorialAdvanceRequested: expected => {
      if (state.mode !== 'play') return;
      const r = player.tutorialAdvance(expected);
      if (r !== 'ok') logLine(`<span class="note">${stamp()} tutorial.advance 거절: ${r} (샘플 서버)</span>`);
    },
  });
  // ping 1초 주기: 인증된 연결이면 복구 중에도 (서버 생존 판정 3초)
  if (serverSource) setInterval(() => { if (serverSource.authenticated()) input.ping(Date.now()); }, PING_MS);

  // 안내 마스크: 최신 수신 상태(phase=tutorial, tutorial.step)를 따른다. 클릭 1회 = 1단계
  const tutorial = createTutorialOverlay(cfg, () => input.tutorialClick());
  const pause = createPauseOverlay(el('roadPane'), () => input.resume());

  // 도크(자차 요약 + 키)의 캔버스 좌표 영역: 도로 라벨·화면 밖 표식이 피해 간다
  const roadCanvas = el<HTMLCanvasElement>('road');
  const dock = el('roadDock');
  function dockRect() {
    const c = roadCanvas.getBoundingClientRect(), r = dock.getBoundingClientRect();
    return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
  }
  const dbgStatus = el('dbgStatus');
  const lockBadge = el('lockBadge');

  function playbackLabel(): string {
    if (state.mode === 'server') return `서버 (${state.link?.state ?? '—'})`;
    if (state.mode === 'static') return '정적 프레임';
    return player.phase === 'tutorial' ? '샘플 재생 (안내 대기)' : player.phase === 'paused' ? '샘플 재생 (정지)' : '샘플 재생 (10Hz)';
  }

  function frame(): void {
    const now = performance.now();
    provenance.onFrame(now);
    if (now - provRenderedAt >= PROVENANCE_RENDER_MS) {
      provRenderedAt = now;
      panel.renderProvenance(provenance.metrics(now), state.latest?.auto ?? null);
    }
    const d = buffer.sample(now);
    const lock = lockReason();
    if (lock !== state.lock) {
      state.lock = lock;
      lockBadge.hidden = !lock;
      lockBadge.textContent = lock ? `🔒 ${lock}` : '';
      if (state.shown) keys.render(state.shown, state.pressed, lock);
    }
    tutorial.update(state.mode !== 'static' && (state.mode !== 'server' || linkOpen()) ? state.latest : null);
    if (d) {
      road.render({ ego: d.ego, nb: d.nb, simTime: d.simTime, view: d.current, showHud: state.showHud, avoid: [dockRect()] });

      const last = buffer.lastReceivedMs();
      const stale = state.mode !== 'static' && d.current.phase === 'running' && last != null && now - last > STALE_MS;
      if (d.current !== state.shown || stale !== state.shownStale) {
        state.shown = d.current;
        state.shownStale = stale;
        panel.render(d.current, { stale, link: linkLabel(state.link) });
        keys.render(d.current, state.pressed, state.lock);
        autoView.render(d.current);
        // 세대·frame·재생 상태는 디버그 창에서만
        dbgStatus.textContent = `gen ${d.current.generation} · frame ${d.current.frameSeq} · tick ${d.current.sampleTick} · 그래프 표본 ${history.size} · ${playbackLabel()}`;
      }
      // 그래프 커서 = 표시 시각의 표본 (차량·깜빡이와 같은 시각)
      chart.render(history.upTo(d.current.generation, d.current.sampleTick), d.current.sampleTime);
    }
    requestAnimationFrame(frame);
  }

  // ---- 탭 (기본: 첫 번째) ----
  function initTabs(): void {
    const tabs = [...document.querySelectorAll<HTMLButtonElement>('#tabs .tab')];
    const panes = [...document.querySelectorAll<HTMLElement>('#tabs .tabpane')];
    const select = (name: string) => {
      for (const t of tabs) { const on = t.dataset.tab === name; t.classList.toggle('active', on); t.setAttribute('aria-selected', String(on)); }
      for (const p of panes) p.hidden = p.dataset.pane !== name;
    };
    for (const t of tabs) t.addEventListener('click', () => { select(t.dataset.tab ?? 'score'); t.blur(); });
    select(tabs[0]?.dataset.tab ?? 'score');
  }

  // 최신 Snapshot 원본 JSON: 열 때와 [갱신] 때만 채운다 (스크롤 위치 유지)
  function initProvenanceJson(): void {
    const box = el<HTMLDetailsElement>('provJson');
    const fill = () => {
      const source = provenance.metrics(performance.now()).source;
      el('provJsonNote').textContent = source === 'server'
        ? '서버 WebSocket 으로 받은 Snapshot 원본'
        : `${source === 'static' ? '샘플 정적 프레임' : '샘플 재생기'}가 로컬에서 만든 Snapshot — 서버 값 아님`;
      el('provJsonText').textContent = provenance.snapshotJson() ?? '(아직 받은 Snapshot 없음)';
    };
    box.addEventListener('toggle', () => { if (box.open) fill(); });
    el('provJsonRefresh').addEventListener('click', e => { fill(); (e.currentTarget as HTMLButtonElement).blur(); });
  }

  // ---- 시작 ----
  el('bannerTitle').textContent = server
    ? '서버 모드 — 연결된 서버의 값 (목업 서버는 실제 15c 아님)'
    : '샘플 데이터 — 실제 15c 출력 아님';
  el('bannerInfo').title = [
    cfg.banner, `원본 보정 기준과 다른 k_drain=${cfg.kDrainDefault}`,
    server ? `서버 모드: ${SERVER_BASE_URL} (개발 서버가 프록시)` : '서버 미연결 — 샘플 Snapshot 재생',
    '그림은 참 상태, 탐지기는 잡음 포함 관측 상태',
  ].join('\n');
  el('trustInfo').title = [
    '경보 배지는 서버 alarm_det / alarm_eff 값을 그대로 표시합니다 (tau 로 다시 계산하지 않음).',
    'tau 는 이상 점수를 변환한 표시·제어용 값이며 안전 확률이나 모델 정확도가 아닙니다.',
    `게이지 눈금: ${cfg.tauAlarmRef} 경보 기준 · ${cfg.tauRecoveryRef} 회복 참고.`,
    '준비 구간(약 10초)에는 경보가 없습니다.',
  ].join('\n');

  const hudCheck = el<HTMLInputElement>('hudCheck');
  hudCheck.addEventListener('change', () => { state.showHud = hudCheck.checked; hudCheck.blur(); });

  initTabs();
  initProvenanceJson();
  el('debug').hidden = !DEBUG;
  el('testBar').hidden = !DEV_TOOLS; // "테스트" 드롭다운은 개발 모드에서만
  // 서버 모드 시험 도구 (#debug): 연결 끊기·ack 유실·입력 멈춤
  el('dbgServer').hidden = !serverSource;
  if (serverSource) {
    const btn = (id: string, fn: () => void) => el<HTMLButtonElement>(id).addEventListener('click', e => { fn(); (e.currentTarget as HTMLButtonElement).blur(); });
    btn('dbgDropClient', () => serverSource.forceReconnect('시험: 클라이언트에서 연결 끊기'));
    // 오류·정지 일으키기 (목업 /_mock/*)
    const faultSel = el<HTMLSelectElement>('dbgFault');
    faultSel.innerHTML = FAULTS.map(f => `<option value="${esc(f.id)}">${esc(f.label)}</option>`).join('');
    btn('dbgFaultRun', () => {
      const f = FAULTS.find(x => x.id === faultSel.value);
      if (!f) return;
      void serverSource.mockFault(f.path, f.body).then(async () => {
        logLine(`<span class="note">${stamp()} 목업 시험: ${esc(f.label)}</span>`);
        if (f.id === 'conflict') serverSource.forceReconnect('시험: CONTROL_CONFLICT');
        if (f.id === 'a13') {
          // 재접속 → history 가 3초 지연되는 동안 다른 쪽에서 reset
          serverSource.forceReconnect('시험: A13');
          await new Promise(r => setTimeout(r, 1500));
          await serverSource.mockFault('reset-sessions', {});
          logLine(`<span class="note">${stamp()} 목업 시험: history 응답 대기 중 reset</span>`);
        }
        if (f.reload) location.reload();
      });
    });
    btn('dbgDropServer', () => void serverSource.mockFault('drop-connections', {}));
    btn('dbgDropAck', () => { void serverSource.mockFault('faults', { drop_next_ack: 'session.reset' }); logLine(`<span class="note">${stamp()} 목업: 다음 session.reset ack 를 버림</span>`); });
  }
  el<HTMLButtonElement>('btnStop').addEventListener('click', e => { input.clickStop(); (e.currentTarget as HTMLButtonElement).blur(); });

  const sel = el<HTMLSelectElement>('frameSelect');
  if (serverSource) {
    // 서버 모드: 정적 프레임·샘플 재생과 섞지 않는다
    sel.innerHTML = '<option>서버 모드 (?source=server)</option>';
    sel.disabled = true;
    provenance.setSource('server');
    // 자동 모드를 지원하는 서버면 바로 자동 세션 {auto_drive, auto_level}. 아니면(목업·health 실패) 또는 ?mode=manual 이면 기존처럼 {} 로 rule 세션
    void (async () => {
      if (FORCE_MANUAL || !(await fetchAutoDriveAvailable(SERVER_BASE_URL))) return serverSource.start();
      state.autoRequested = true;
      return serverSource.start(createSessionBody({ solver: 'rule', auto: { level: AUTO_START_LEVEL, seed: null } }));
    })();
  } else {
    // 샘플 모드: 샘플 재생(기본) 또는 정적 확인 프레임
    sel.innerHTML = '<option value="play">샘플 재생 (90초 타임라인 반복)</option>'
      + STATIC_FRAMES.map(f => `<option value="${esc(f.id)}">정적 확인: ${esc(f.label)}</option>`).join('');
    const show = (id: string) => {
      const f = STATIC_FRAMES.find(x => x.id === id);
      player.stop();
      buffer.clear();
      history.clear();
      provenance.reset();
      provenance.setSource(f ? 'static' : 'sample');
      state.latest = null;
      state.eventGen = null;
      state.shown = null;
      if (f) {
        state.mode = 'static';
        sel.value = f.id;
        receive(f.snapshot, f.events);
      } else {
        state.mode = 'play';
        sel.value = 'play';
        player.start(); // 안내 0단계 초기 화면. 재생은 안내 완료 뒤
      }
    };
    // 선택 후 포커스를 돌려 키 입력이 선택창으로 가지 않게 한다
    sel.addEventListener('change', () => { show(sel.value); sel.blur(); });
    show(hashFrame);
  }

  requestAnimationFrame(frame);
}

const demoConfig = await loadDemoConfig();
if (demoConfig) boot(demoConfig);
