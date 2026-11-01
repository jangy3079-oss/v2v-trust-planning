/*
 * 실제 15c 세션. 물리·탐지·누적기·자차 회피는 모두 demo_v15c 의 Session (bridge.py 가 감싼다).
 * 여기서는 계약의 세션 상태만 관리한다: phase·안내·정지, 입력 커서, 이벤트, 그래프 이력, 시나리오 보고.
 * server.ts 가 쓰는 인터페이스는 mock/session.ts 와 같다.
 */
import type {
  ApiError, CommandBody, DemoEvent, DriveKey, ErrorCode, EventKind, GraphSample, PauseReason, Phase, ScenarioKind,
  ScenarioReport, Snapshot, Solver, TutorialStep,
} from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG as CFG } from './config.ts';
import { callBridge } from './bridge-sync.ts';

export const DT = 0.1;
const LAST_STEP = (CFG.tutorial.length - 1) as TutorialStep;
const EVENT_LIMIT = CFG.timing.event_limit;
const HISTORY_LIMIT = CFG.timing.history_samples;
const DRIVE_ORDER: DriveKey[] = ['W', 'A', 'S', 'D'];
const r4 = (n: number) => Math.round(n * 1e4) / 1e4;

export type ControlBody = Extract<CommandBody, { action: 'session.reset' | 'tutorial.advance' | 'session.pause' | 'session.resume' }>;
export type DrivingBody = Extract<CommandBody, { action: 'signal.toggle' | 'scenario.start' | 'scenario.stop' }>;
export type CommandResult = { ok: true } | { ok: false; code: ErrorCode; message: string };

export function isControl(b: CommandBody): b is ControlBody {
  return b.action === 'session.reset' || b.action === 'tutorial.advance' || b.action === 'session.pause' || b.action === 'session.resume';
}

// 브리지가 돌려주는 시뮬레이션 부분 (bridge.py parts())
type Parts = Pick<Snapshot, 'vehicles' | 'observation' | 'model' | 'ego_control' | 'plant' | 'scenario' | 'next_weave'> & {
  report: ScenarioReport | null; a_user_mps2: number; r_cmd_radps: number;
};

export function createSession(id: string, token: string, solver: Solver) {
  let generation = 0, frameSeq = 0, tick = -1;
  let phase: Phase = 'tutorial';
  let tutorialStep: TutorialStep | null = 0;
  let pauseReason: PauseReason | null = null;
  let resumePhase: 'tutorial' | 'running' | null = null;
  let connected = false;
  let stepMs: number | null = null, lagMs = 0;
  let runtimeError: ApiError | null = null;

  let heldReceived: DriveKey[] = [];
  let tapped = new Set<DriveKey>();
  let heldApplied: DriveKey[] = [];
  let lastRecvSeq: number | null = null, lastAppliedSeq: number | null = null, inputConn: string | null = null;
  let lastReport: ScenarioReport | null = null;

  let P: Parts;
  let prev = { det: false, eff: false, collisions: 0, scenStart: null as number | null, scenKind: null as ScenarioKind | null, signal: 'off' as string };
  let events: DemoEvent[] = [], eventSeq = 0, undrained: DemoEvent[] = [];
  let graph: GraphSample[] = [];
  let created = false;

  const simTime = () => (tick + 1) * DT;
  const sampleTime = () => (tick >= 0 ? tick * DT : null);
  const scenarioCfg = (k: ScenarioKind) => CFG.scenarios.find(x => x.kind === k)!;

  function pushEvent(kind: EventKind, message: string, requestId: string | null = null): void {
    const e: DemoEvent = {
      generation, event_seq: ++eventSeq, sample_tick: tick, sample_time_s: sampleTime() == null ? null : r4(sampleTime()!),
      sim_time_s: r4(simTime()), kind, message, request_id: requestId,
    };
    events.push(e);
    if (events.length > EVENT_LIMIT) events.shift();
    undrained.push(e);
  }
  function clearInputs(): void { heldReceived = []; tapped = new Set(); heldApplied = []; }

  function init(kind: 'session.created' | 'session.reset'): void {
    generation += 1;
    frameSeq = 0; tick = -1; phase = 'tutorial'; tutorialStep = 0; pauseReason = null; resumePhase = null;
    stepMs = null; lagMs = 0; runtimeError = null;
    clearInputs(); lastRecvSeq = null; lastAppliedSeq = null; inputConn = null; lastReport = null;
    P = created ? callBridge<Parts>({ cmd: 'reset' }) : callBridge<Parts>({ cmd: 'new', solver });
    created = true;
    prev = { det: false, eff: false, collisions: 0, scenStart: null, scenKind: null, signal: 'off' };
    events = []; eventSeq = 0; undrained = []; graph = [];
    pushEvent(kind, kind === 'session.created' ? '세션 생성 (실제 15c)' : '초기화 — 새 세대, 안내 0단계');
  }
  init('session.created');

  // ---- 한 스텝: 입력 적용 → 15c Session.step → 이벤트·그래프 ----
  function step(driving: DrivingBody[], measuredLagMs: number): void {
    const t0 = performance.now();
    tick += 1;
    lagMs = Math.max(0, Math.round(measuredLagMs));
    const applied = new Set<DriveKey>([...heldReceived, ...tapped]);
    heldApplied = DRIVE_ORDER.filter(k => applied.has(k));
    tapped = new Set();
    lastAppliedSeq = lastRecvSeq;

    const hadStop = driving.some(b => b.action === 'scenario.stop');
    const hadStart = driving.some(b => b.action === 'scenario.start');
    const before = P;
    try {
      P = callBridge<Parts>({ cmd: 'step', held: heldApplied, actions: driving });
    } catch (err) {
      console.error('[real] bridge error', err);
      setError({ code: 'INTERNAL_ERROR', message: '모델 실행 중 오류가 발생했습니다', retryable: true, details: {} });
      tick -= 1;
      return;
    }

    // 이벤트: 깜빡이, 시나리오, 경보, 충돌
    const sig = P.vehicles.neighbor.signal_side;
    if (sig !== prev.signal) {
      pushEvent('signal.changed', sig === 'off' ? '이웃차 깜빡이 끔' : `이웃차 ${sig === 'left' ? '왼쪽' : '오른쪽'} 깜빡이 켬`);
      prev.signal = sig;
    }
    const sc = P.scenario;
    const ended = prev.scenStart != null && (sc == null || sc.start_tick !== prev.scenStart);
    if (ended) {
      const replaced = sc != null; // 새 시나리오로 교체
      const rep = replaced ? before.report : P.report;
      if (rep && prev.scenKind === rep.kind) lastReport = rep;
      const how = !replaced && !hadStop ? 'scenario.finished' : 'scenario.stopped';
      pushEvent(how, `${scenarioCfg(prev.scenKind!).label} ${how === 'scenario.finished' ? '자동 종료' : '해제'}`);
    }
    if (sc && sc.start_tick !== prev.scenStart) pushEvent('scenario.started', `${scenarioCfg(sc.kind).label} 시작`);
    prev.scenStart = sc ? sc.start_tick : null;
    prev.scenKind = sc ? sc.kind : null;
    void hadStart;

    const al = P.model?.alarms;
    if (al) {
      if (al.alarm_det !== prev.det) pushEvent(al.alarm_det ? 'alarm.det.started' : 'alarm.det.ended', al.alarm_det ? '탐지 경보 시작' : '탐지 경보 해제');
      if (al.alarm_eff !== prev.eff) pushEvent(al.alarm_eff ? 'alarm.eff.started' : 'alarm.eff.ended', al.alarm_eff ? '반응 경보 시작' : '반응 경보 해제');
      prev.det = al.alarm_det; prev.eff = al.alarm_eff;
    }
    const nColl = P.ego_control.collision_count;
    if (nColl > prev.collisions) pushEvent('collision', `충돌 #${nColl}`);
    prev.collisions = nColl;

    const tr = P.model!.trust;
    graph.push({
      sample_tick: tick, sample_time_s: r4(tick * DT), tau_det: r4(tr.tau_det), tau_eff: r4(tr.tau_eff), tau_a2: r4(tr.tau_a2),
      alarm_det: P.model!.alarms.alarm_det, alarm_eff: P.model!.alarms.alarm_eff, active: P.model!.context.active,
    });
    if (graph.length > HISTORY_LIMIT) graph.shift();
    stepMs = r4(performance.now() - t0);
    frameSeq += 1;
  }

  function systemPause(reason: PauseReason): boolean {
    if (phase !== 'running' && phase !== 'tutorial') return false;
    resumePhase = phase; phase = 'paused'; pauseReason = reason;
    clearInputs();
    pushEvent('session.paused', `정지 (${reason})`);
    frameSeq += 1;
    return true;
  }
  function setError(err: ApiError): void {
    phase = 'error'; runtimeError = err; resumePhase = null; pauseReason = null;
    clearInputs();
    pushEvent('runtime.error', `서버 오류 (${err.code})`);
    frameSeq += 1;
  }

  function control(b: ControlBody): CommandResult {
    switch (b.action) {
      case 'session.reset':
        if (!['tutorial', 'running', 'paused', 'error'].includes(phase)) return { ok: false, code: 'INVALID_STATE', message: `reset 불가: phase=${phase}` };
        try { init('session.reset'); } catch (err) {
          console.error('[real] reset failed', err);
          return { ok: false, code: 'INTERNAL_ERROR', message: '초기화에 실패했습니다' };
        }
        return { ok: true };
      case 'tutorial.advance':
        if (phase !== 'tutorial' || tutorialStep == null) return { ok: false, code: 'INVALID_STATE', message: `tutorial.advance 는 tutorial 에서만 (phase=${phase})` };
        if (b.payload.expected_step !== tutorialStep) return { ok: false, code: 'TUTORIAL_STEP_MISMATCH', message: `현재 단계 ${tutorialStep}, 요청 ${b.payload.expected_step}` };
        if (tutorialStep >= LAST_STEP) { tutorialStep = null; phase = 'running'; pushEvent('tutorial.completed', '안내 완료 · 실시간 시작'); }
        else { tutorialStep = (tutorialStep + 1) as TutorialStep; pushEvent('tutorial.advanced', `안내 ${tutorialStep + 1}/${CFG.tutorial.length}`); }
        frameSeq += 1;
        return { ok: true };
      case 'session.pause':
        if (phase !== 'running' && phase !== 'tutorial') return { ok: false, code: 'INVALID_STATE', message: `pause 불가: phase=${phase}` };
        resumePhase = phase; phase = 'paused'; pauseReason = b.payload.reason;
        clearInputs(); pushEvent('session.paused', `정지 (${b.payload.reason})`); frameSeq += 1;
        return { ok: true };
      case 'session.resume':
        if (phase !== 'paused' || !resumePhase) return { ok: false, code: 'INVALID_STATE', message: `resume 은 paused 에서만 (phase=${phase})` };
        phase = resumePhase; resumePhase = null; pauseReason = null;
        clearInputs(); pushEvent('session.resumed', '재개'); frameSeq += 1;
        return { ok: true };
    }
  }

  function snapshot(): Snapshot {
    const sampled = tick >= 0;
    const m = P.model;
    const scen = P.scenario;
    return {
      protocol_version: 'v1', session_id: id, generation, frame_seq: frameSeq, sample_tick: tick,
      sample_time_s: sampled ? r4(tick * DT) : null, sim_time_s: r4(simTime()), server_time: new Date().toISOString(), phase,
      initial_config: { ...CFG.defaults, solver },
      model_status: !sampled ? 'not_started' : m?.context.active ? 'active' : 'warming_up',
      warmup_remaining_samples: Math.max(0, CFG.timing.detector_active_tick - tick),
      vehicles: P.vehicles, observation: sampled ? P.observation : null, model: sampled ? m : null,
      ego_control: P.ego_control, plant: P.plant,
      scenario: scen && { ...scen, steering_blocked: scenarioCfg(scen.kind).steering_blocked, auto_end_s: scenarioCfg(scen.kind).auto_end_s },
      last_scenario_report: lastReport, next_weave: P.next_weave,
      inputs: {
        held_received: [...heldReceived], held_applied: [...heldApplied],
        last_received_input_seq: lastRecvSeq, last_applied_input_seq: lastAppliedSeq, input_connection_id: inputConn,
        a_user_mps2: r4(P.a_user_mps2), r_cmd_radps: r4(P.r_cmd_radps), steering_blocked: !!scen && scenarioCfg(scen.kind).steering_blocked,
      },
      tutorial: phase === 'tutorial' || (phase === 'paused' && resumePhase === 'tutorial')
        ? { step: tutorialStep, completed: false } : { step: null, completed: tutorialStep == null },
      runtime: { connected, step_ms: stepMs, lag_ms: lagMs, pause_reason: pauseReason, resume_phase: resumePhase, error: runtimeError },
    };
  }

  return {
    id, token, solver,
    get generation() { return generation; },
    get phase() { return phase; },
    snapshot, step, control, systemPause, setError,
    history(afterTick: number) {
      const from = graph[0]?.sample_tick ?? null;
      return {
        available_from_tick: from, latest_tick: graph[graph.length - 1]?.sample_tick ?? null,
        truncated: from != null && afterTick + 1 < from, samples: graph.filter(g => g.sample_tick > afterTick),
      };
    },
    events(afterSeq: number) {
      const from = events[0]?.event_seq ?? null;
      return {
        available_from_event_seq: from, latest_event_seq: events[events.length - 1]?.event_seq ?? null,
        truncated: from != null && afterSeq + 1 < from, events: events.filter(e => e.event_seq > afterSeq),
      };
    },
    drainEvents(): DemoEvent[] { const e = undrained; undrained = []; return e; },
    setInput(held: DriveKey[], tap: DriveKey[], clientSeq: number, connectionId: string): void {
      heldReceived = [...held];
      for (const k of tap) tapped.add(k);
      lastRecvSeq = clientSeq; inputConn = connectionId;
    },
    setConnected(v: boolean): void { connected = v; },
  };
}

export type MockSession = ReturnType<typeof createSession>;
