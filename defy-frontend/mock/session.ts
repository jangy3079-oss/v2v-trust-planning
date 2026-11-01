/*
 * 목업 세션 — 화면·연결 검증용 단순 근사 시뮬레이션. 실제 15c 물리·탐지·회피 정책이 아니다.
 *  - 이웃차: W/S 목표 가속 2초 램프, A/D 요레이트 명령(±0.05rad/s, 0.05rad/s² 증가, 0.3초 감쇠), 시나리오 5종 근사
 *  - 자차: 차선 유지 + 앞차 따라가기 + 이웃 진로 침범 시 감속·옆 차로 이동 (MOCK)
 *  - 신뢰도·경보: 이웃의 횡 편차·횡속도·가감속으로 만든 단순 누적 함수 (MOCK)
 *  - 입력 커서(held_applied, last_*_input_seq), runtime.step_ms·lag_ms, server_time 은 실제로 잰 값
 * 계약 형식은 src/contracts.ts 타입을 그대로 따른다 (타입만 import).
 */
import type {
  ActiveScenario, ApiError, CommandBody, DemoEvent, DriveKey, ErrorCode, EventKind, GraphSample, PauseReason, Phase,
  ScenarioKind, ScenarioParameters, ScenarioReport, SignalSide, Snapshot, Solver, TutorialStep, VehicleState, Zone,
} from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG as CFG } from './config.ts';

export const DT = 0.1;
const SUBSTEPS = 10;
const H = DT / SUBSTEPS;
const LEN = CFG.road.vehicle_length_m, WID = CFG.road.vehicle_width_m;
const LANES = CFG.road.lanes.map(l => l.center_y_m);
const LAST_STEP = (CFG.tutorial.length - 1) as TutorialStep;
const EVENT_LIMIT = CFG.timing.event_limit;
const HISTORY_LIMIT = CFG.timing.history_samples;
const WHEELBASE = 2.7;
const DRIVE_ORDER: DriveKey[] = ['W', 'A', 'S', 'D'];

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const smooth = (u: number) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const nearestLane = (y: number) => LANES.reduce((b, c) => (Math.abs(c - y) < Math.abs(b - y) ? c : b), 0);
const r4 = (n: number) => Math.round(n * 1e4) / 1e4;

interface Car { x: number; y: number; psi: number; v: number; a: number; steer: number; r: number }

interface RunningScenario {
  kind: ScenarioKind;
  params: ScenarioParameters;
  startTick: number;
  dir: -1 | 1;
  laneCenter: number;
  detTick: number | null;
  effTick: number | null;
  a2Tick: number | null;
  crossTick: number | null;
}

export type ControlBody = Extract<CommandBody, { action: 'session.reset' | 'tutorial.advance' | 'session.pause' | 'session.resume' }>;
export type DrivingBody = Extract<CommandBody, { action: 'signal.toggle' | 'scenario.start' | 'scenario.stop' }>;
export type CommandResult = { ok: true } | { ok: false; code: ErrorCode; message: string };

export function isControl(b: CommandBody): b is ControlBody {
  return b.action === 'session.reset' || b.action === 'tutorial.advance' || b.action === 'session.pause' || b.action === 'session.resume';
}

export function createSession(id: string, token: string, solver: Solver) {
  // ---- 세션 전체 상태 (reset 으로 다시 만든다) ----
  let generation = 0;
  let frameSeq = 0;
  let tick = -1;
  let phase: Phase = 'tutorial';
  let tutorialStep: TutorialStep | null = 0;
  let pauseReason: PauseReason | null = null;
  let resumePhase: 'tutorial' | 'running' | null = null;
  let connected = false;
  let stepMs: number | null = null;
  let lagMs = 0;
  let runtimeError: ApiError | null = null;

  let ego: Car, nb: Car, egoVy = 0;
  let signal: SignalSide = 'off';
  let signalOffAt = -Infinity;
  let aUser = 0, rampFrom = 0, rampTarget = 0, rampT = 2, rCmd = 0;
  let heldReceived: DriveKey[] = [];
  let tapped = new Set<DriveKey>();
  let heldApplied: DriveKey[] = [];
  let lastRecvSeq: number | null = null, lastAppliedSeq: number | null = null, inputConn: string | null = null;
  let scen: RunningScenario | null = null;
  let lastReport: ScenarioReport | null = null;
  let weaveIdx = 0;

  // 자차 (MOCK)
  let egoState: 'NORMAL' | 'AVOIDING' | 'RETURNING' = 'NORMAL';
  let targetY = 0, vRef = 25, stage: 0 | 1 | 2 | 3 = 0, reacted = false, danger = false;
  let cmdNext = { a: 0, ay: 0 }, cmdApplied = { a: 0, ay: 0 };
  let collisionCount = 0, prevHit = false, hit = false, gapMin: number | null = null;

  // 모델 (MOCK)
  let S = 0, Sp = 0, zone: Zone = 'front';
  let m = { tauDet: 1, tauEff: 1, tauA2: 1, tauRule: 1, alarmDet: false, alarmEff: false, alarmA2: false, active: false,
    eLong: 0, eLat: 0, rA: 0, R1: 0, R2: null as number | null, intent: false, g: true, clipped: false, holdOn: false };
  let holdUntil = -Infinity;

  let events: DemoEvent[] = [];
  let eventSeq = 0;
  let undrained: DemoEvent[] = [];
  let graph: GraphSample[] = []; // 완료된 step 마다 1표본, 최근 600개 (명세 5)

  const simTime = () => (tick + 1) * DT;
  const sampleTime = () => (tick >= 0 ? tick * DT : null);

  function pushEvent(kind: EventKind, message: string, requestId: string | null = null): void {
    const e: DemoEvent = {
      generation, event_seq: ++eventSeq, sample_tick: tick, sample_time_s: sampleTime() == null ? null : r4(sampleTime()!),
      sim_time_s: r4(simTime()), kind, message, request_id: requestId,
    };
    events.push(e);
    if (events.length > EVENT_LIMIT) events.shift();
    undrained.push(e);
  }

  function clearInputs(): void {
    heldReceived = []; tapped = new Set(); heldApplied = [];
  }

  // 최초 설정으로 완전 초기화 (명세 8.1 의 목업 근사)
  function init(kind: 'session.created' | 'session.reset'): void {
    generation += 1;
    frameSeq = 0; tick = -1; phase = 'tutorial'; tutorialStep = 0; pauseReason = null; resumePhase = null;
    stepMs = null; lagMs = 0; runtimeError = null;
    ego = { x: 0, y: 0, psi: 0, v: CFG.defaults.ego_speed_mps, a: 0, steer: 0, r: 0 };
    nb = { x: CFG.defaults.gap_m, y: LANES[CFG.defaults.neighbor_lane_id] ?? 3.5, psi: 0, v: CFG.defaults.neighbor_speed_mps, a: 0, steer: 0, r: 0 };
    egoVy = 0; signal = 'off'; signalOffAt = -Infinity;
    aUser = 0; rampFrom = 0; rampTarget = 0; rampT = 2; rCmd = 0;
    clearInputs(); lastRecvSeq = null; lastAppliedSeq = null; inputConn = null;
    scen = null; lastReport = null; weaveIdx = 0;
    egoState = 'NORMAL'; targetY = 0; vRef = 25; stage = 0; reacted = false; danger = false;
    cmdNext = { a: 0, ay: 0 }; cmdApplied = { a: 0, ay: 0 };
    collisionCount = 0; prevHit = false; hit = false; gapMin = null;
    S = 0; Sp = 0; zone = 'front'; holdUntil = -Infinity;
    m = { tauDet: 1, tauEff: 1, tauA2: 1, tauRule: 1, alarmDet: false, alarmEff: false, alarmA2: false, active: false,
      eLong: 0, eLat: 0, rA: 0, R1: 0, R2: null, intent: false, g: true, clipped: false, holdOn: false };
    events = []; eventSeq = 0; undrained = []; graph = [];
    pushEvent(kind, kind === 'session.created' ? '세션 생성 (목업)' : '초기화 — 새 세대, 안내 0단계 (목업)');
  }
  init('session.created');

  // ---- 시나리오 (MOCK 근사) ----
  function scenarioParams(kind: ScenarioKind): ScenarioParameters {
    if (kind === 'weave') {
      const w = CFG.weave_cycle[weaveIdx % CFG.weave_cycle.length]!;
      weaveIdx++;
      return { kind: 'weave', amplitude_m: w.amplitude_m, period_s: w.period_s, ramp_s: 7.5 };
    }
    const s = CFG.scenarios.find(x => x.kind === kind)!;
    return s.first_parameters;
  }
  const scenarioCfg = (k: ScenarioKind) => CFG.scenarios.find(x => x.kind === k)!;

  function endScenario(how: 'scenario.stopped' | 'scenario.finished'): void {
    if (!scen) return;
    const d = (t: number | null) => (t == null ? null : r4((t - scen!.startTick) * DT));
    lastReport = {
      kind: scen.kind, parameters: scen.params, start_sample_time_s: r4(scen.startTick * DT),
      boundary_cross_delay_s: d(scen.crossTick), detection_delay_s: d(scen.detTick), reaction_delay_s: d(scen.effTick), a2_delay_s: d(scen.a2Tick),
    };
    pushEvent(how, `${scenarioCfg(scen.kind).label} ${how === 'scenario.finished' ? '자동 종료' : '해제'} (목업)`);
    scen = null;
  }

  function applyDriving(b: DrivingBody): void {
    if (b.action === 'signal.toggle') {
      const prev = signal;
      signal = signal === b.payload.side ? 'off' : b.payload.side; // 같은 쪽 다시 → 끔, 반대쪽 → 그쪽만 켬
      if (prev !== 'off' && signal === 'off') signalOffAt = simTime();
      pushEvent('signal.changed', signal === 'off' ? '이웃차 깜빡이 끔' : `이웃차 ${signal === 'left' ? '왼쪽' : '오른쪽'} 깜빡이 켬`);
    } else if (b.action === 'scenario.stop') {
      endScenario('scenario.stopped');
    } else {
      endScenario('scenario.stopped'); // 교체: stopped → started
      const lc = nearestLane(nb.y);
      scen = { kind: b.payload.kind, params: scenarioParams(b.payload.kind), startTick: tick, dir: lc > 0 ? -1 : 1, laneCenter: lc,
        detTick: null, effTick: null, a2Tick: null, crossTick: null };
      pushEvent('scenario.started', `${scenarioCfg(b.payload.kind).label} 시작 (목업)`);
    }
  }

  // ---- 한 스텝 (0.1초): 입력 적용 → 이웃 → 자차 → 모델 → 다음 제어 ----
  function step(driving: DrivingBody[], measuredLagMs: number): void {
    const t0 = performance.now();
    tick += 1;
    lagMs = Math.max(0, Math.round(measuredLagMs));
    for (const b of driving) applyDriving(b);

    // 이번 스텝에 적용하는 입력 = 최신 held ∪ 지난 스텝 이후 tapped
    const applied = new Set<DriveKey>([...heldReceived, ...tapped]);
    heldApplied = DRIVE_ORDER.filter(k => applied.has(k));
    tapped = new Set();
    lastAppliedSeq = lastRecvSeq;

    const blocked = !!scen && scenarioCfg(scen.kind).steering_blocked;
    const target = applied.has('S') ? CFG.controls.brake_target_mps2 : applied.has('W') ? CFG.controls.accel_target_mps2 : 0; // S 우선
    if (target !== rampTarget) { rampFrom = aUser; rampTarget = target; rampT = 0; }
    const left = applied.has('A') && !blocked, right = applied.has('D') && !blocked;

    let crossed = false;
    const egoCmd = cmdNext; // 지난 스텝에 정한 명령을 이번 스텝에 적용 → 결정 후 command_applied 가 된다
    for (let i = 0; i < SUBSTEPS; i++) {
      rampT += H;
      aUser = rampFrom + (rampTarget - rampFrom) * Math.min(1, rampT / CFG.controls.pedal_ramp_s);
      const lim = CFG.controls.yaw_command_limit_radps, ramp = CFG.controls.yaw_command_ramp_radps2;
      if (left && !right) rCmd = Math.min(lim, rCmd + ramp * H);
      else if (right && !left) rCmd = Math.max(-lim, rCmd - ramp * H);
      else rCmd *= Math.exp(-H / CFG.controls.yaw_release_time_s);

      // 이웃 시나리오 (MOCK)
      let aAdd = 0, rScen: number | null = null;
      if (scen) {
        const st = (tick - scen.startTick) * DT + i * H;
        const p = scen.params;
        if (p.kind === 'brake') aAdd = st < p.duration_s ? p.added_accel_mps2 : 0;
        else if (p.kind === 'accel') aAdd = st < p.ramp_s + p.hold_s ? p.added_accel_mps2 * Math.min(1, st / p.ramp_s) : 0;
        else if (p.kind === 'drift') rScen = (Math.atan2(scen.dir * p.lateral_speed_mps * smooth(st / p.ramp_s), Math.max(nb.v, 1)) - nb.psi) / 0.5;
        else if (p.kind === 'stuck') rScen = scen.dir * p.r0_radps;
        else {
          const w = (2 * Math.PI) / p.period_s;
          const dy = scen.dir * p.amplitude_m * (w * Math.cos(w * st) * smooth(st / p.ramp_s));
          rScen = (Math.atan2(dy, Math.max(nb.v, 1)) - nb.psi) / 0.5;
        }
      }
      const r = rScen ?? (rCmd + (left || right ? 0 : -0.5 * nb.psi)); // MOCK: 조향 입력이 없으면 방향각 정렬
      const a = clamp(aUser + aAdd, CFG.controls.plant_accel_limits_mps2[0], CFG.controls.plant_accel_limits_mps2[1]);
      const prevSide = Math.sign(nb.y - nearestLane(nb.y));
      nb.v = Math.max(0, nb.v + a * H);
      nb.psi += r * H;
      nb.x += nb.v * Math.cos(nb.psi) * H;
      nb.y += nb.v * Math.sin(nb.psi) * H;
      nb.a = a; nb.r = r; nb.steer = Math.atan((WHEELBASE * r) / Math.max(nb.v, 1));
      if (Math.abs(Math.abs(nb.y - nearestLane(nb.y)) - 1.75) < 0.05 && prevSide !== 0) crossed = true;

      // 자차 (MOCK): 지난 스텝에 정한 명령을 적용
      ego.v = Math.max(0, ego.v + egoCmd.a * H);
      egoVy += egoCmd.ay * H;
      ego.y += egoVy * H;
      ego.x += ego.v * H;
    }
    const prevPsi = ego.psi;
    ego.psi = Math.atan2(egoVy, Math.max(ego.v, 1));
    ego.r = (ego.psi - prevPsi) / DT;
    ego.a = egoCmd.a;
    ego.steer = Math.atan((WHEELBASE * ego.r) / Math.max(ego.v, 1));

    // 시나리오 자동 종료
    if (scen) {
      const st = (tick - scen.startTick + 1) * DT;
      const auto = scenarioCfg(scen.kind).auto_end_s;
      if (crossed && scen.crossTick == null) scen.crossTick = tick;
      if (auto != null && st >= auto - 1e-9) endScenario('scenario.finished');
    }

    updateModel();
    decideEgo();
    measureGaps();

    graph.push({
      sample_tick: tick, sample_time_s: r4(tick * DT), tau_det: r4(m.tauDet), tau_eff: r4(m.tauEff), tau_a2: r4(m.tauA2),
      alarm_det: m.alarmDet, alarm_eff: m.alarmEff, active: m.active,
    });
    if (graph.length > HISTORY_LIMIT) graph.shift();

    stepMs = r4(performance.now() - t0);
    frameSeq += 1;
  }

  // 서버 사정으로 정지 (input_timeout · disconnected). running/tutorial 에서만. 시간·시나리오 상태는 그대로 보존
  function systemPause(reason: PauseReason): boolean {
    if (phase !== 'running' && phase !== 'tutorial') return false;
    resumePhase = phase; phase = 'paused'; pauseReason = reason;
    clearInputs();
    pushEvent('session.paused', `정지 (${reason})`);
    frameSeq += 1;
    return true;
  }

  // ---- 모델 (MOCK): 이웃의 횡 편차·횡속도·가감속 → 누적 → tau, 경보 ----
  function updateModel(): void {
    const h = CFG.calibration.h, cap = CFG.calibration.cap_theta_multiple * h;
    const dx = nb.x - ego.x;
    // 구역 (명세 7.1 의 ±8m, ±2m 히스테리시스)
    if (zone === 'front' && dx < 6) zone = 'alongside';
    else if (zone === 'rear' && dx > -6) zone = 'alongside';
    else if (zone === 'alongside') zone = dx > 10 ? 'front' : dx < -10 ? 'rear' : 'alongside';

    m.active = tick >= CFG.timing.detector_active_tick;
    m.intent = signal !== 'off' || simTime() - signalOffAt < 3;
    const dev = nb.y - nearestLane(nb.y);
    const vlat = nb.v * Math.sin(nb.psi);
    m.eLat = Math.max(0, Math.abs(vlat) - 0.15) * 3 + Math.max(0, Math.abs(dev) - 0.5) * 1.2; // MOCK
    m.eLong = Math.max(0, Math.abs(nb.a) - 2) * 0.5;                                          // MOCK
    m.rA = nb.a - ego.a;
    m.g = zone === 'front' ? m.rA < 0 : zone === 'rear' ? m.rA > 0 : true;
    m.R1 = m.eLat * 0.5;
    const toward = Math.sign(vlat) === Math.sign(dev) && Math.abs(vlat) > 0.05;
    const ttb = toward ? (1.75 - Math.abs(dev)) / Math.abs(vlat) : Infinity;
    m.R2 = ttb <= 2 ? r4(Math.max(0, ttb)) : null;

    if (!m.active) {
      Object.assign(m, { tauDet: 1, tauEff: 1, tauA2: 1, tauRule: 1, alarmDet: false, alarmEff: false, alarmA2: false, clipped: false, holdOn: false });
      return;
    }
    const e = m.eLat + m.eLong;
    if (!m.intent) { // 의도 구간에는 누적 갱신 동결 (이미 쌓인 값은 유지)
      S = clamp(S + (e - 0.4) * DT * 2, 0, cap);
      Sp = clamp(Sp + ((m.g ? e : 0) - 0.4) * DT * 2, 0, cap);
    }
    const Seff = Math.min(Sp, S);
    m.clipped = S >= cap - 1e-9;
    m.tauDet = Math.pow(0.7, S / h);
    m.tauEff = Math.pow(0.7, Seff / h);
    m.tauRule = 1 - Math.min(0.5, m.eLong * 0.15);
    m.tauA2 = clamp(1 - e * 0.3, 0, 1);
    const prevDet = m.alarmDet, prevEff = m.alarmEff;
    m.alarmDet = m.tauDet < CFG.thresholds.tau_alarm_reference;
    m.alarmEff = m.tauEff < CFG.thresholds.tau_alarm_reference;
    m.alarmA2 = m.tauA2 < 0.6;
    if (prevDet && !m.alarmDet) holdUntil = simTime() + CFG.calibration.hold_s;
    m.holdOn = simTime() < holdUntil;
    if (m.alarmDet !== prevDet) pushEvent(m.alarmDet ? 'alarm.det.started' : 'alarm.det.ended', m.alarmDet ? '탐지 경보 시작' : '탐지 경보 해제');
    if (m.alarmEff !== prevEff) pushEvent(m.alarmEff ? 'alarm.eff.started' : 'alarm.eff.ended', m.alarmEff ? '반응 경보 시작' : '반응 경보 해제');
    if (scen) {
      if (m.alarmDet && scen.detTick == null) scen.detTick = tick;
      if (m.alarmEff && scen.effTick == null) scen.effTick = tick;
      if (m.alarmA2 && scen.a2Tick == null) scen.a2Tick = tick;
    }
  }

  // ---- 자차 결정 (MOCK): 다음 스텝 명령 (command_next) ----
  function decideEgo(): void {
    const dx = nb.x - ego.x, dy = nb.y - ego.y;
    const closing = Math.max(0, ego.v - nb.v);
    const sameLaneAhead = dx > 0 && dx <= 100 && Math.abs(dy) < WID;
    const gapLong = Math.max(0, Math.abs(dx) - LEN);
    const intrusion = dx > -2 && dx < 60 && Math.abs(nb.y - ego.y) < 2.2;
    danger = intrusion && (m.tauEff <= CFG.thresholds.tau_alarm_reference || (closing > 0.3 && dx / closing < 4));
    if (egoState !== 'AVOIDING' && danger) {
      const free = LANES.filter(l => Math.abs(l - targetY) > 1 && Math.abs(l - targetY) < 4 && Math.abs(nb.y - l) > 2.65)
        .sort((a, b) => Math.abs(a - ego.y) - Math.abs(b - ego.y));
      if (free[0] !== undefined) targetY = free[0];
      egoState = 'AVOIDING';
      reacted = true;
    } else if (egoState === 'AVOIDING' && !danger && (dx < -8 || Math.abs(nb.y) > 2.65)) {
      egoState = 'RETURNING'; targetY = 0;
    } else if (egoState === 'RETURNING' && Math.abs(ego.y) < 0.1 && Math.abs(egoVy) < 0.05) {
      egoState = 'NORMAL';
    }
    stage = danger ? (closing > 0.3 && dx / closing < 2 ? 3 : 2) : egoState === 'NORMAL' ? 0 : 1;
    const vFollow = sameLaneAhead ? nb.v + (gapLong - (5 + 1.5 * ego.v)) / 2 : Infinity;
    vRef = Math.min(CFG.defaults.ego_speed_mps, vFollow);
    if (danger && Math.abs(dy) < WID) vRef = Math.min(vRef, nb.v - 2);
    vRef = Math.max(0, vRef);
    const a = clamp(0.6 * (vRef - ego.v), danger ? -5 : -3.5, 1);
    const ay = clamp(0.5 * (targetY - ego.y) - 1.3 * egoVy, -2, 2);
    cmdApplied = cmdNext; // 이번 스텝에 Vehicle 에 전달한 명령
    cmdNext = { a, ay };  // 다음 스텝용
  }

  function measureGaps(): void {
    const dx = nb.x - ego.x, dy = nb.y - ego.y;
    const gl = Math.max(0, Math.abs(dx) - LEN), gt = Math.max(0, Math.abs(dy) - WID);
    const gap = Math.hypot(gl, gt);
    gapMin = gapMin == null ? gap : Math.min(gapMin, gap);
    hit = gl === 0 && gt === 0;
    if (hit && !prevHit) { collisionCount++; pushEvent('collision', '충돌 (목업 축 정렬 근사)'); }
    prevHit = hit;
  }

  // ---- 제어 명령 (리셋·안내·정지·재개): 즉시 처리 ----
  function control(b: ControlBody): CommandResult {
    switch (b.action) {
      case 'session.reset':
        if (!['tutorial', 'running', 'paused', 'error'].includes(phase)) return { ok: false, code: 'INVALID_STATE', message: `reset 불가: phase=${phase}` };
        init('session.reset');
        return { ok: true };
      case 'tutorial.advance':
        if (phase !== 'tutorial' || tutorialStep == null) return { ok: false, code: 'INVALID_STATE', message: `tutorial.advance 는 tutorial 에서만 (phase=${phase})` };
        if (b.payload.expected_step !== tutorialStep) return { ok: false, code: 'TUTORIAL_STEP_MISMATCH', message: `현재 단계 ${tutorialStep}, 요청 ${b.payload.expected_step}` };
        if (tutorialStep >= LAST_STEP) {
          tutorialStep = null; phase = 'running';
          pushEvent('tutorial.completed', '안내 완료 · 실시간 시작');
        } else {
          tutorialStep = (tutorialStep + 1) as TutorialStep;
          pushEvent('tutorial.advanced', `안내 ${tutorialStep + 1}/${CFG.tutorial.length}`);
        }
        frameSeq += 1;
        return { ok: true };
      case 'session.pause':
        if (phase !== 'running' && phase !== 'tutorial') return { ok: false, code: 'INVALID_STATE', message: `pause 불가: phase=${phase}` };
        resumePhase = phase; phase = 'paused'; pauseReason = b.payload.reason;
        clearInputs();
        pushEvent('session.paused', `정지 (${b.payload.reason})`);
        frameSeq += 1;
        return { ok: true };
      case 'session.resume':
        if (phase !== 'paused' || !resumePhase) return { ok: false, code: 'INVALID_STATE', message: `resume 은 paused 에서만 (phase=${phase})` };
        phase = resumePhase; resumePhase = null; pauseReason = null;
        clearInputs();
        pushEvent('session.resumed', '재개');
        frameSeq += 1;
        return { ok: true };
    }
  }

  // ---- Snapshot (계약 형식) ----
  const vehicle = (c: Car, sig: SignalSide): VehicleState => ({
    x_m: r4(c.x), y_m: r4(c.y), psi_rad: r4(c.psi), v_mps: r4(c.v), a_mps2: r4(c.a), steer_rad: r4(c.steer), yaw_rate_radps: r4(c.r),
    length_m: 4.5, width_m: 1.8, signal_side: sig,
  });
  const zeroCounts = () => ({ accel_command: 0, steer_command: 0, steer_rate: 0, speed_lat: 0 });
  const plantLast = (c: Car) => (tick < 0 ? null : {
    a: r4(c.a), delta: r4(c.steer), r: r4(c.r), a_lat: r4(c.v * c.r), jerk_max: 0, a_lat_max: 0, r_max: 0,
    delta_rate_max: 0, rate_violation: 0, slip_max_deg: 0, clips: zeroCounts(),
  });

  function snapshot(): Snapshot {
    const sampled = tick >= 0;
    const dx = nb.x - ego.x, dy = nb.y - ego.y;
    const gl = Math.max(0, Math.abs(dx) - LEN), gt = Math.max(0, Math.abs(dy) - WID);
    const closing = Math.max(0, ego.v - nb.v);
    const ttc = sampled && dx > 0 && Math.abs(dy) < WID && closing > 0.3 ? r4(gl / closing) : null;
    const steerOf = (cmd: { a: number; ay: number }) => r4(Math.atan((WHEELBASE * cmd.ay) / Math.max(ego.v, 1) ** 2));
    const dev = nb.y - nearestLane(nb.y);
    const st = scen ? (tick - scen.startTick) * DT : 0;
    const active: ActiveScenario | null = scen && {
      kind: scen.kind, parameters: scen.params, start_tick: scen.startTick, start_sample_time_s: r4(scen.startTick * DT),
      elapsed_s: r4(Math.max(0, st)), direction_sign: scen.dir, lane_center_at_start_m: scen.laneCenter,
      steering_blocked: scenarioCfg(scen.kind).steering_blocked, auto_end_s: scenarioCfg(scen.kind).auto_end_s,
    };
    const w = CFG.weave_cycle[weaveIdx % CFG.weave_cycle.length]!;
    const cal = CFG.calibration;
    return {
      protocol_version: 'v1',
      session_id: id,
      generation,
      frame_seq: frameSeq,
      sample_tick: tick,
      sample_time_s: sampled ? r4(tick * DT) : null,
      sim_time_s: r4(simTime()),
      server_time: new Date().toISOString(), // 실제 전송 시각
      phase,
      initial_config: { ...CFG.defaults, solver },
      model_status: !sampled ? 'not_started' : m.active ? 'active' : 'warming_up',
      warmup_remaining_samples: Math.max(0, CFG.timing.detector_active_tick - tick),
      vehicles: { ego: vehicle(ego, 'off'), neighbor: vehicle(nb, signal) },
      observation: !sampled ? null : {
        a_obs_mps2: r4(nb.a), r_obs_radps: r4(nb.r), d_obs_m: r4(dev), v_meas_mps: r4(nb.v), y_obs_m: r4(nb.y), d_true_m: r4(dev),
        sent_signal: signal === 'off' ? 0 : 1,
      },
      model: !sampled ? null : {
        trust: {
          tau_det: r4(m.tauDet), tau_eff: r4(m.tauEff), tau_a2: r4(m.tauA2), tau_rule: r4(m.tauRule),
          tau_gru: r4(m.tauA2), tau_r1: 1, tau_r2: 1, tau_state: r4(m.tauDet), tau_state_eff: r4(m.tauEff),
        },
        alarms: {
          alarm_det: m.alarmDet, alarm_eff: m.alarmEff, alarm_a2: m.alarmA2, alarm_gru: false, alarm_rule: false,
          alarm_r1: false, alarm_r2: false, alarm_r2_shadow: false, alarm_path: false, alarm_eff_path: false,
        },
        scores: {
          e: r4(m.eLat + m.eLong), e_long: r4(m.eLong), e_lat: r4(m.eLat), r_a_mps2: r4(m.rA), R1_S: r4(m.R1),
          R2_ttb_s: m.R2, R2_ttb_reason: m.R2 == null ? 'no_crossing_within_horizon' : 'finite', d_hat_m: r4(dev), psi_hat_rad: r4(nb.psi),
        },
        accumulator: { S: r4(S), S_prime: r4(Sp), S_eff: r4(Math.min(S, Sp)), clipped: m.clipped, hold_on: m.holdOn },
        context: { active: m.active, intent_window: m.intent, zone, dx_m: r4(dx), g: m.g, r2_enabled: true, r2_calib_ok: cal.r2_calib_ok },
        calibration: { ...cal, source_ids: [...cal.source_ids] },
      },
      ego_control: {
        control_mode: 'combined', solver, state: egoState,
        command_applied: { a_mps2: r4(cmdApplied.a), steer_rad: steerOf(cmdApplied) },
        command_next: { a_mps2: r4(cmdNext.a), steer_rad: steerOf(cmdNext) },
        reacted, engaged: egoState !== 'NORMAL', hit, collision_count: collisionCount,
        dx_m: r4(dx), dy_m: r4(dy), gap_long_m: r4(gl), gap_lat_m: r4(gt), gap_m: r4(Math.hypot(gl, gt)),
        gap_min_m: gapMin == null ? null : r4(gapMin),
        ttc_s: ttc, ttc_reason: !sampled ? 'not_sampled' : ttc == null ? 'not_ahead_or_not_closing' : 'finite',
        policy: {
          stage, closing_mps: r4(closing), danger, rear_only: false, emergency: stage === 3,
          target_y_m: targetY, policy_target_y_m: targetY, nmpc_override: false, v_ref_mps: r4(vRef), brake_mps2: r4(Math.min(0, cmdNext.a)),
          ttc_s: danger ? ttc : null, ttc_reason: !sampled ? 'not_sampled' : danger && ttc != null ? 'finite' : 'no_predicted_contact',
          raw_a_mps2: sampled ? r4(cmdNext.a) : null, raw_steer_rad: sampled ? steerOf(cmdNext) : null, command_clips: 0,
        },
        nmpc: null,
      },
      plant: {
        ego: { last: plantLast(ego), counts: zeroCounts() },
        neighbor: { last: plantLast(nb), counts: zeroCounts() },
        neighbor_command: !sampled ? null : { a_mps2: r4(nb.a), steer_rad: r4(nb.steer) },
      },
      scenario: active,
      last_scenario_report: lastReport,
      next_weave: { amplitude_m: w.amplitude_m, period_s: w.period_s },
      inputs: {
        held_received: [...heldReceived], held_applied: [...heldApplied],
        last_received_input_seq: lastRecvSeq, last_applied_input_seq: lastAppliedSeq, input_connection_id: inputConn,
        a_user_mps2: r4(aUser), r_cmd_radps: r4(rCmd), steering_blocked: !!scen && scenarioCfg(scen.kind).steering_blocked,
      },
      tutorial: phase === 'tutorial' || (phase === 'paused' && resumePhase === 'tutorial')
        ? { step: tutorialStep, completed: false }
        : { step: null, completed: tutorialStep == null },
      runtime: { connected, step_ms: stepMs, lag_ms: lagMs, pause_reason: pauseReason, resume_phase: resumePhase, error: runtimeError },
    };
  }

  return {
    id, token, solver,
    get generation() { return generation; },
    get phase() { return phase; },
    snapshot,
    step,
    control,
    systemPause,
    // 모델·작업 실패 → phase=error (R 로만 빠져나온다, 명세 8.2)
    setError(err: ApiError): void {
      phase = 'error'; runtimeError = err; resumePhase = null; pauseReason = null;
      clearInputs();
      pushEvent('runtime.error', `서버 오류 (${err.code})`);
      frameSeq += 1;
    },
    // GET /history: after_tick 이후 표본 (오름차순). 보존 범위보다 오래된 구간을 요청하면 truncated
    history(afterTick: number) {
      const from = graph[0]?.sample_tick ?? null;
      return {
        available_from_tick: from, latest_tick: graph[graph.length - 1]?.sample_tick ?? null,
        truncated: from != null && afterTick + 1 < from,
        samples: graph.filter(g => g.sample_tick > afterTick),
      };
    },
    // GET /events: after_event_seq 이후 이벤트
    events(afterSeq: number) {
      const from = events[0]?.event_seq ?? null;
      return {
        available_from_event_seq: from, latest_event_seq: events[events.length - 1]?.event_seq ?? null,
        truncated: from != null && afterSeq + 1 < from,
        events: events.filter(e => e.event_seq > afterSeq),
      };
    },
    // 새 이벤트 (telemetry 에 실어 보낼 것)
    drainEvents(): DemoEvent[] { const e = undrained; undrained = []; return e; },
    // input.state 수신: 최신 held 로 교체, tapped 는 다음 step 까지 합집합
    setInput(held: DriveKey[], tap: DriveKey[], clientSeq: number, connectionId: string): void {
      heldReceived = [...held];
      for (const k of tap) tapped.add(k);
      lastRecvSeq = clientSeq;
      inputConn = connectionId;
    },
    setConnected(v: boolean): void { connected = v; },
  };
}

export type MockSession = ReturnType<typeof createSession>;
