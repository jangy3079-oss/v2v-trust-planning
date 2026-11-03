/*
 * 실제 15c 세션. 물리·탐지·누적기·자차 회피는 모두 demo_v15c 의 Session (bridge.py 가 감싼다).
 * 여기서는 계약의 세션 상태만 관리한다: phase·안내·정지, 입력 커서, 이벤트, 그래프 이력, 시나리오 보고.
 * 도로: 세대마다 새 굽은 도로 (shared/road.ts, 명세 5.2). 화면 표시용이라 브리지에는 넘기지 않는다 (15c 는 직선 그대로).
 * server.ts 가 쓰는 인터페이스는 mock/session.ts 와 같다.
 */
import type {
  ApiError, AutoDriveLevel, AutoDriveStatus, AutoStopReason, CommandBody, DemoEvent, DriveKey, ErrorCode, EventKind, GraphSample,
  PauseReason, Phase, RoadKind, RoadResponse, ScenarioKind, ScenarioReport, Snapshot, Solver, TutorialStep,
} from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG as CFG, REAL_ROAD } from './config.ts';
import { callBridge } from './bridge-sync.ts';
import { createRoadGenerator, roadModeFor, roadSeedFor } from '../shared/road.ts';

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
  auto?: AutoDriveStatus; auto_held?: DriveKey[]; auto_stop?: boolean; auto_idle_s?: number; // 자동 세션만 (bridge.py auto_step·parts)
};

// 자동 모드 (명세 1.3). null = 수동 세션: 아래 자동 분기는 모두 건너뛴다
export interface AutoOptions { seed: number; level: AutoDriveLevel }

export function createSession(id: string, token: string, solver: Solver, auto: AutoOptions | null = null, roadKind?: RoadKind) {
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
  let autoOn = false;                               // 자동 세션에서 운전자가 조종 중 (manual 이 아님)
  let takeover: AutoStopReason | null = null;       // 다음 스텝에 브리지로 넘길 사람 개입
  let idleTicks = 0, idleLimit = 0;                 // manual 에서 사람 입력 없이 지난 running 스텝 수 · 한도 (AUTO.manual_idle_s ÷ DT)
  let road = createRoadGenerator(null);             // 세대마다 새로 (자동 복귀는 세대가 그대로라 도로도 그대로)

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
    P = created ? callBridge<Parts>({ cmd: 'reset' })
      : callBridge<Parts>(auto ? { cmd: 'new', solver, auto: { ...auto, session_id: id } } : { cmd: 'new', solver });
    created = true;
    const mode = roadModeFor(roadKind, REAL_ROAD);
    const roadSeed = mode === 'straight' ? null : mode === 'circular' ? 0 : roadSeedFor(/^\d+$/.test(REAL_ROAD) ? REAL_ROAD : 'random');
    road = createRoadGenerator(roadSeed, mode === 'circular');
    console.log(`[real] road ${id} generation=${generation} mode=${mode} seed=${roadSeed ?? 'straight'}`); // 재현용 (자동 seed 와 별개)
    if (auto) { autoOn = true; takeover = null; idleTicks = 0; idleLimit = Math.round(P.auto_idle_s! / DT); } // R: 같은 설정·같은 시드로 처음부터
    prev = { det: false, eff: false, collisions: 0, scenStart: null, scenKind: null, signal: 'off' };
    events = []; eventSeq = 0; undrained = []; graph = [];
    pushEvent(kind, kind === 'session.created' ? '세션 생성 (실제 15c)' : '초기화 — 새 세대, 안내 0단계');
  }
  init('session.created');

  // 자동 세션: 사람 개입 → 수동 복귀 (pause/reset/advance 는 개입이 아니다)
  function handBack(reason: AutoStopReason): void {
    if (!auto || !autoOn) return;
    autoOn = false;
    takeover ??= reason;
    idleTicks = 0;
  }
  // 자동 세션: manual 에서 사람 입력 없이 idleLimit 스텝 → 현재 장면에서 운전자가 이어받는다 (R 과 달리 세대·안내·기록은 그대로)
  function autoResume(): void {
    const st = callBridge<AutoDriveStatus>({ cmd: 'auto_resume' });
    P = { ...P, auto: st };
    autoOn = true; idleTicks = 0;
  }
  // 자동 세션: phase 가 running 밖으로 나가면 운전자 정지를 브리지에 알린다 (기록용. 스텝이 멈추므로 키도 멈춘다)
  function autoHalt(reason: string): void {
    if (!auto || !autoOn) return;
    try { callBridge({ cmd: 'auto_halt', reason }); } catch (err) { console.error('[real] auto_halt failed', err); }
  }

  // ---- 한 스텝: 입력 적용 → 15c Session.step → 이벤트·그래프 ----
  function step(driving: DrivingBody[], measuredLagMs: number): void {
    const t0 = performance.now();
    tick += 1;
    lagMs = Math.max(0, Math.round(measuredLagMs));
    if (auto && autoOn && driving.length > 0) handBack('human_command');
    const applied = new Set<DriveKey>([...heldReceived, ...tapped]);
    heldApplied = DRIVE_ORDER.filter(k => applied.has(k));
    tapped = new Set();
    lastAppliedSeq = lastRecvSeq;

    const hadStart = driving.some(b => b.action === 'scenario.start');
    const before = P;
    try {
      P = callBridge<Parts>(auto ? { cmd: 'step', held: heldApplied, actions: driving, takeover }
        : { cmd: 'step', held: heldApplied, actions: driving });
    } catch (err) {
      console.error('[real] bridge error', err);
      setError({ code: 'INTERNAL_ERROR', message: '모델 실행 중 오류가 발생했습니다', retryable: true, details: {} });
      tick -= 1;
      return;
    }
    if (auto) {
      takeover = null;
      if (P.auto_held) heldApplied = DRIVE_ORDER.filter(k => P.auto_held!.includes(k)); // 이번 스텝에 실제로 쓴 키 (운전자 또는 사람)
    }
    // 운전자가 넣은 0 도 사람의 해제와 같이 stopped (finished 는 원본이 스스로 끝낸 경우만)
    const hadStop = driving.some(b => b.action === 'scenario.stop') || !!P.auto_stop;

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
    // 입력 없음 카운트: running 스텝에서만 센다 (정지·끊김·오류 중에는 스텝이 없다). 사람 키·명령이 있으면 처음부터
    if (auto && !autoOn) {
      idleTicks = driving.length > 0 || heldApplied.length > 0 ? 0 : idleTicks + 1;
      if (idleTicks >= idleLimit) {
        try { autoResume(); } catch (err) {
          console.error('[real] auto_resume failed', err);
          setError({ code: 'INTERNAL_ERROR', message: '모델 실행 중 오류가 발생했습니다', retryable: true, details: {} });
        }
      }
    }
    stepMs = r4(performance.now() - t0);
    frameSeq += 1;
  }

  function systemPause(reason: PauseReason): boolean {
    if (phase !== 'running' && phase !== 'tutorial') return false;
    if (phase === 'running') autoHalt(reason);
    resumePhase = phase; phase = 'paused'; pauseReason = reason;
    clearInputs();
    pushEvent('session.paused', `정지 (${reason})`);
    frameSeq += 1;
    return true;
  }
  function setError(err: ApiError): void {
    if (phase === 'running') autoHalt('error');
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
        if (phase === 'running') autoHalt(b.payload.reason);
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
      ...(auto && P.auto ? { auto: { ...P.auto, auto_resume_in_s: P.auto.state === 'manual' && !autoOn ? r4((idleLimit - idleTicks) * DT) : null } } : {}),
    };
  }

  return {
    id, token, solver,
    get generation() { return generation; },
    get phase() { return phase; },
    get autoDriving() { return !!auto && autoOn; }, // 운전자 조종 중: 입력 TTL 면제 (명세 1.3)
    snapshot, step, control, systemPause, setError,
    // 가장 앞 차량 = 마지막 브리지 결과(P)의 자차·이웃 x 중 큰 값. 그 앞 3000m 까지 이어 붙여 보낸다
    road(): RoadResponse { return road.response(generation, Math.max(P.vehicles.ego.x_m, P.vehicles.neighbor.x_m)); },
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
      if (auto && autoOn) {
        // 운전자 조종 중: 빈 입력은 받기만 하고 쓰지 않는다 (held_received 는 []). 비어 있지 않으면 수동 복귀
        lastRecvSeq = clientSeq; inputConn = connectionId;
        if (held.length === 0 && tap.length === 0) return;
        handBack('human_input');
      }
      if (auto && (held.length > 0 || tap.length > 0)) idleTicks = 0; // manual: 사람 키가 오면 입력 없음 카운트를 처음부터
      heldReceived = [...held];
      for (const k of tap) tapped.add(k);
      lastRecvSeq = clientSeq; inputConn = connectionId;
    },
    setConnected(v: boolean): void { connected = v; },
  };
}

export type MockSession = ReturnType<typeof createSession>;
