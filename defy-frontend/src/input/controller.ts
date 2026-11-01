/*
 * 입력 → 메시지 규칙 (명세 2.2, 3.3, 6.2).
 * - 안내(tutorial): Enter·새 일반 키·안내 영역 클릭 1회 → tutorial.advance{expected_step} 1개.
 *   자동 반복은 진행을 만들지 않고, 진행 키는 운전·깜빡이·시나리오로 전달하지 않는다. R 은 진행 키가 아니라 초기화.
 *   마지막 단계 완료 시 눌려 있던 키는 keyup 전까지 운전 입력으로 받지 않는다.
 * - blur 는 tutorial 에서도 session.pause 만 만든다 (input.state 는 running 에서만 허용, 명세 6.2).
 * - input.state: running 에서만. 눌림 변경 즉시(최대 20Hz 로 합침), 변화가 없어도 input_refresh_ms(250ms)마다.
 *   tapped = 마지막 전송 이후 새로 눌린 WASD, 보내면 비운다. held 는 매번 전체 집합.
 * - command: Q/E → signal.toggle, 1~5 → scenario.start, 0 → scenario.stop (running 에서만),
 *            R → session.reset (어느 phase 에서나).
 * - blur/탭 숨김: 입력을 비우고, running 이면 빈 input.state 를 보낸 뒤 session.pause{focus_lost}.
 *   재개는 "재개" 버튼(session.resume)으로만. 재개 후 입력은 빈 집합에서 새 keydown 부터.
 * 메시지는 send 로 넘긴다: 샘플 모드는 디버그·콘솔에만, 서버 모드는 WebSocket 으로 실제 전송.
 */
import type { ClientMessage, CommandBody, CommandMessage, Phase, TutorialStep } from '../contracts';
import type { ConfigView } from '../contract-access';
import { createKeyboard } from './keyboard';
import { createMessageFactory } from './messages';
import type { DriveKey } from '../contracts';

const MIN_SEND_INTERVAL_MS = 50;     // 20Hz
const SLOW_SEND_INTERVAL_MS = 200;   // RATE_LIMITED 뒤 잠시 5Hz 로 줄임

export type SendKind = 'change' | 'refresh' | 'command' | 'ping';

export interface InputControllerDeps {
  cfg: ConfigView;
  getConnectionId(): string; // 서버 모드: ready 의 connection_id, 샘플 모드: 자리표시자
  // 최신 수신 Snapshot 기준 세션 상태. null 이면 메시지를 만들지 않는다 (정적 프레임 등)
  getSession(): { phase: Phase; generation: number; tutorialStep: TutorialStep | null } | null;
  send(msg: ClientMessage, kind: SendKind): void;
  note(text: string): void;
  onPhysical(pressed: ReadonlySet<string>): void;
  onPauseRequested(): void; // 샘플 재생 정지 (서버라면 session.pause 처리)
  onResumeRequested(): void;
  onResetRequested(): void;                          // 샘플 재생기 초기화 (서버라면 session.reset 처리)
  onTutorialAdvanceRequested(expected: TutorialStep): void;
}

export function createInputController(d: InputControllerDeps) {
  const factory = createMessageFactory(d.getConnectionId);
  const tapped = new Set<DriveKey>();
  let lastSendMs = -Infinity;
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let sentAdvance: { generation: number; step: TutorialStep } | null = null; // 같은 단계에 진행을 두 번 보내지 않음
  let slowUntil = 0;
  const minInterval = () => (performance.now() < slowUntil ? SLOW_SEND_INTERVAL_MS : MIN_SEND_INTERVAL_MS);
  const lastStep = (d.cfg.tutorial.length - 1) as TutorialStep;

  const running = () => {
    const s = d.getSession();
    return s && s.phase === 'running' ? s : null;
  };

  function flush(kind: SendKind): void {
    if (flushTimer != null) { clearTimeout(flushTimer); flushTimer = null; }
    const s = running();
    if (!s) { tapped.clear(); return; }
    const msg = factory.inputState(s.generation, kb.held(), tapped);
    tapped.clear();
    lastSendMs = performance.now();
    d.send(msg, kind);
  }

  function scheduleFlush(): void {
    const wait = lastSendMs + minInterval() - performance.now();
    if (wait <= 0) flush('change');
    else if (flushTimer == null) flushTimer = setTimeout(() => flush('change'), wait);
  }

  function edgeBody(code: string): CommandBody | null {
    if (code === 'KeyQ') return { action: 'signal.toggle', payload: { side: 'left' } };
    if (code === 'KeyE') return { action: 'signal.toggle', payload: { side: 'right' } };
    if (code === 'Digit0') return { action: 'scenario.stop', payload: {} };
    if (code === 'KeyR') return { action: 'session.reset', payload: {} };
    const sc = d.cfg.scenarios.find(s => `Digit${s.key}` === code);
    return sc ? { action: 'scenario.start', payload: { kind: sc.kind } } : null;
  }

  function command(body: CommandBody): void {
    const s = d.getSession();
    if (!s) return;
    d.send(factory.command(s.generation, body), 'command');
  }

  // 안내 한 단계 진행 (키·클릭 공통)
  function advanceTutorial(): void {
    const s = d.getSession();
    if (!s || s.phase !== 'tutorial' || s.tutorialStep == null) return;
    if (sentAdvance && sentAdvance.generation === s.generation && sentAdvance.step === s.tutorialStep) return; // 응답 대기
    const step = s.tutorialStep;
    sentAdvance = { generation: s.generation, step };
    if (step === lastStep) kb.lockHeldKeys(); // 완료: 지금 눌린 키는 keyup 전까지 운전 입력 아님
    command({ action: 'tutorial.advance', payload: { expected_step: step } });
    d.onTutorialAdvanceRequested(step);
  }

  function onEdgeKey(code: string): void {
    const body = edgeBody(code);
    const s = d.getSession();
    if (!body || !s) return;
    if (body.action === 'session.reset') {
      // 어느 phase 에서나 초기화 우선. 입력 버퍼를 비우고, 아직 눌린 키는 keyup 전 재무장하지 않는다 (명세 8.1)
      kb.clear();
      tapped.clear();
      sentAdvance = null;
      command(body);
      d.onResetRequested();
      return;
    }
    // 명세 6.2 허용 상태: reset 은 tutorial/running/paused/error, 나머지 주행 명령은 running
    if (s.phase !== 'running') {
      d.note(`${code} 무시 — phase=${s.phase} 에서는 ${body.action} 을 보내지 않음`);
      return;
    }
    command(body);
  }

  const kb = createKeyboard({
    // 안내 중: R 을 뺀 모든 새 일반 키는 진행으로 소비 (운전·깜빡이·시나리오로 전달 안 함)
    intercept(code) {
      const s = d.getSession();
      if (!s || s.phase !== 'tutorial' || code === 'KeyR') return false;
      advanceTutorial();
      return true;
    },
    onDrive(_held, newlyPressed) {
      if (!running()) return;
      if (newlyPressed) tapped.add(newlyPressed);
      scheduleFlush();
    },
    onEdgeKey,
    onPhysical: d.onPhysical,
    onFocusLost() {
      const s = d.getSession();
      if (!s || s.phase === 'paused' || s.phase === 'error') return;
      tapped.clear();
      // 빈 입력은 running 에서만 (input.state 허용 상태). tutorial 에서는 session.pause 만
      if (s.phase === 'running') flush('change');
      command({ action: 'session.pause', payload: { reason: 'focus_lost' } });
      d.onPauseRequested();
    },
  });

  // 변화가 없어도 running 이면 250ms 마다
  setInterval(() => {
    if (running() && performance.now() - lastSendMs >= d.cfg.inputRefreshMs) flush('refresh');
  }, 25);

  return {
    // "재개" 버튼
    resume(): void {
      const s = d.getSession();
      if (!s || s.phase !== 'paused') return;
      kb.clear();
      tapped.clear();
      command({ action: 'session.resume', payload: {} });
      d.onResumeRequested();
      lastSendMs = -Infinity; // 재개 직후 빈 입력을 바로 한 번 보낸다
    },
    // 연결 끊김·복구: 눌림·tapped·안내 진행 기록을 비운다. 아직 누른 키는 keyup 후 새 keydown 부터 (A12)
    clearInputs(): void {
      kb.clear();
      tapped.clear();
      sentAdvance = null;
    },
    // 안내 완료 직후 누르고 있던 키가 아직 안 올라옴 (운전 입력 잠김)
    completionLocked(): boolean { return kb.isLocked(); },
    // RATE_LIMITED: 잠시 입력 전송량을 줄인다
    slowDown(ms: number): void { slowUntil = performance.now() + ms; },
    get slowed(): boolean { return performance.now() < slowUntil; },
    // TUTORIAL_STEP_MISMATCH 등으로 진행이 거절되면 같은 단계 진행을 다시 허용 (사용자가 다시 시도)
    allowTutorialRetry(): void { sentAdvance = null; },
    // 명령 재시도 (같은 request_id·본문·generation, 새 client_seq)
    resendCommand(m: CommandMessage): CommandMessage { return factory.resend(m); },
    // 1초 주기 ping (서버 모드). client_seq 는 다른 메시지와 같은 연결 순번을 쓴다
    ping(clientTimeMs: number): void { d.send(factory.ping(clientTimeMs), 'ping'); },
    // 화면의 [시나리오 해제 (0)] 버튼
    clickStop(): void { onEdgeKey('Digit0'); },
    // 안내 영역 클릭 (클릭 1회당 한 단계)
    tutorialClick(): void { advanceTutorial(); },
  };
}
