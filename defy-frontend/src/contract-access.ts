/*
 * 계약 필드 접근.
 * contracts.ts(원본 web-demo-contracts.ts 복사본)의 snake_case 필드는 이 파일에서만 읽는다.
 * 나머지 코드는 여기서 만든 view 객체만 쓴다. 값은 변환·보정하지 않고 그대로 옮긴다.
 */
import type {
  ClientMessage,
  CommandAction,
  CommandMessage,
  EventsResponse,
  GraphSample,
  HistoryResponse,
  CreateSessionResponse,
  ErrorResponse,
  ServerMessage,
  DriveKey,
  DemoConfig,
  DemoEvent,
  EventKind,
  EgoControl,
  ModelOutput,
  ScenarioKind,
  ScenarioParameters,
  SignalSide,
  Snapshot,
  TutorialStep,
  VehicleState,
  Phase,
  Zone,
  Solver,
  PauseReason,
  ErrorCode,
  AutoDriveLevel,
  AutoDriveState,
  AutoDriveStatus,
  AutoStopReason,
  CreateSessionRequest,
  HealthResponse,
} from './contracts';

export interface ConfigView {
  lanes: { id: number; centerY: number }[];
  laneWidth: number;
  outerEdges: readonly [number, number];
  innerEdges: readonly [number, number];
  dt: number;
  renderBufferMs: number;
  historySamples: number;
  inputRefreshMs: number;
  tauAlarmRef: number;
  tauRecoveryRef: number;
  keyboard: { code: string; label: string; description: string; mode: 'hold' | 'edge' }[];
  scenarios: { kind: ScenarioKind; key: string; label: string; steeringBlocked: boolean; autoEndS: number | null }[];
  tutorial: { step: TutorialStep; highlightCodes: string[]; text: string }[];
  banner: string;
  kDrainDefault: number;
  availableSolvers: Solver[];
}

export interface VehicleView {
  x: number;
  y: number;
  psi: number;
  v: number;
  a: number;
  steer: number;
  r: number;
  len: number;
  wid: number;
  signal: SignalSide;
}

export interface ModelView {
  tauDet: number;
  tauEff: number;
  tauA2: number;
  tauRule: number;
  tauGru: number;
  tauR1: number;
  tauR2: number;
  alarmDet: boolean;
  alarmEff: boolean;
  alarmA2: boolean;
  e: number;
  eLong: number;
  eLat: number;
  rA: number;
  R1S: number;
  R2ttb: number | null;
  R2reason: 'finite' | 'no_crossing_within_horizon';
  S: number;
  Sprime: number;
  Seff: number;
  clipped: boolean;
  holdOn: boolean;
  active: boolean;
  intentWindow: boolean;
  zone: Zone;
  dx: number;
  g: boolean;
  r2Enabled: boolean;
  r2CalibOk: boolean;
  theta: number;
  h: number;
  hD: number;
  Tc: number;
}

export interface EgoControlView {
  controlMode: 'combined';
  solver: Solver;
  state: EgoControl['state'];
  cmdAppliedA: number;
  cmdAppliedSteer: number;
  cmdNextA: number;
  cmdNextSteer: number;
  reacted: boolean;
  engaged: boolean;
  hit: boolean;
  collisionCount: number;
  dx: number;
  dy: number;
  gapLong: number;
  gapLat: number;
  gap: number;
  gapMin: number | null;
  ttc: number | null;
  ttcReason: EgoControl['ttc_reason'];
  stage: 0 | 1 | 2 | 3;
  danger: boolean;
  emergency: boolean;
  rearOnly: boolean;
  targetY: number;
  policyTargetY: number;
  nmpcOverride: boolean;
  vRef: number;
  brake: number;
  policyTtc: number | null;
  policyTtcReason: EgoControl['policy']['ttc_reason'];
  nmpc: {
    state: EgoControl['state'];
    solveMs: number;
    fallbackCount: number;
    fallbackNow: boolean;
    dSafe: number | null;
    targetY: number | null;
    predicted: { offset: number; x: number; y: number }[];
  } | null;
}

export interface SnapshotView {
  generation: number;
  frameSeq: number;
  sampleTick: number;
  sampleTime: number | null;
  simTime: number;
  phase: Phase;
  modelStatus: Snapshot['model_status'];
  warmupRemaining: number;
  kDrain: number;
  ego: VehicleView;
  nb: VehicleView;
  obs: { dObs: number; sentSignal: 0 | 1 } | null;
  model: ModelView | null;
  ec: EgoControlView;
  plantEgo: { a: number; delta: number; aLat: number } | null;
  scenario: {
    kind: ScenarioKind;
    params: ScenarioParameters;
    elapsed: number;
    directionSign: -1 | 1;
    startSampleTime: number;
    steeringBlocked: boolean;
    autoEnd: number | null;
  } | null;
  report: {
    kind: ScenarioKind;
    startSampleTime: number;
    boundaryDelay: number | null;
    detectionDelay: number | null;
    reactionDelay: number | null;
    a2Delay: number | null;
  } | null;
  nextWeave: { amp: number; period: number };
  inputs: { heldReceived: string[]; heldApplied: string[]; steeringBlocked: boolean };
  tutorial: { step: TutorialStep | null; completed: boolean };
  runtime: {
    connected: boolean;
    stepMs: number | null;
    lagMs: number;
    pauseReason: PauseReason | null;
    resumePhase: 'tutorial' | 'running' | null;
    error: { code: ErrorCode; message: string } | null;
  };
  auto: AutoView | null;
}

// 자동 모드 (명세 1.3, v1.5). Snapshot.auto 가 없거나 enabled=false 면 null (수동 세션)
export interface AutoView {
  seed: number;
  levelSetting: AutoDriveLevel;
  level: AutoDriveStatus['level'];
  state: AutoDriveState;
  stoppedReason: AutoStopReason | null;
  eventKind: string | null;
  nextEventIn: number | null;
  edgeMargin: number;
  log: { t: number; text: string }[];
}

// 데이터 근거 탭용: 서버가 보낸 값의 출처·순서·입력 반영을 확인하는 필드 (view/provenance.ts 가 사용)
export interface ProvenanceView {
  protocolVersion: string;
  sessionId: string;
  serverTime: string;          // UTC ISO 8601 전송 시각
  generation: number;
  frameSeq: number;
  sampleTick: number;
  stepMs: number | null;
  lagMs: number;
  lastReceivedInputSeq: number | null;
  lastAppliedInputSeq: number | null;
  inputConnectionId: string | null;
  heldApplied: DriveKey[];
}

export function readProvenance(s: Snapshot): ProvenanceView {
  const inp = s.inputs, rt = s.runtime;
  return {
    protocolVersion: s.protocol_version, sessionId: s.session_id, serverTime: s.server_time,
    generation: s.generation, frameSeq: s.frame_seq, sampleTick: s.sample_tick,
    stepMs: rt.step_ms, lagMs: rt.lag_ms,
    lastReceivedInputSeq: inp.last_received_input_seq, lastAppliedInputSeq: inp.last_applied_input_seq,
    inputConnectionId: inp.input_connection_id, heldApplied: inp.held_applied,
  };
}

// GET /health: 자동 모드 지원 여부만 쓴다
export function readHealth(h: HealthResponse): { ready: boolean; autoDriveAvailable: boolean } {
  return { ready: h.ready, autoDriveAvailable: h.auto_drive_available };
}

// POST /sessions 본문. 직접 조종 + rule 이면 기존과 같은 {} (생략 = 서버 기본값)
export interface SessionOptions { solver: Solver; auto: { level: AutoDriveLevel; seed: number | null } | null }
export function createSessionBody(o: SessionOptions): CreateSessionRequest {
  const body: CreateSessionRequest = {};
  if (o.solver !== 'rule') body.solver = o.solver;
  if (o.auto) {
    body.auto_drive = true;
    body.auto_level = o.auto.level;
    if (o.auto.seed != null) body.auto_seed = o.auto.seed; // 비우면 서버가 정한다
  }
  return body;
}

// 내가 보낸 input.state 의 순번·눌림 (다른 type 이면 null)
export function readSentInput(m: ClientMessage): { connectionId: string; clientSeq: number; held: DriveKey[] } | null {
  return m.type === 'input.state' ? { connectionId: m.connection_id, clientSeq: m.client_seq, held: m.held } : null;
}

// ---- 서버 응답 (서버 모드) ----
export interface CreatedSessionView { sessionId: string; token: string; wsPath: string; snapshot: Snapshot }
export function readCreateSession(r: CreateSessionResponse): CreatedSessionView {
  return { sessionId: r.session_id, token: r.session_token, wsPath: r.ws_path, snapshot: r.snapshot };
}

export interface ApiErrorView { code: ErrorCode; message: string; retryable: boolean; details: Record<string, unknown> }
export function readApiError(r: ErrorResponse): ApiErrorView {
  return { code: r.error.code, message: r.error.message, retryable: r.error.retryable, details: r.error.details };
}

export type ServerMessageView =
  | { type: 'ready'; connectionId: string; snapshot: Snapshot }
  | { type: 'telemetry'; snapshot: Snapshot; events: DemoEvent[] }
  | { type: 'ack'; requestId: string; clientSeq: number; action: CommandAction; generation: number; appliedTick: number | null; snapshot: Snapshot }
  | { type: 'error'; code: ErrorCode; message: string; retryable: boolean; details: Record<string, unknown>; requestId: string | null; clientSeq: number | null; currentGeneration: number | null }
  | { type: 'pong'; clientTimeMs: number };

export function readServerMessage(m: ServerMessage): ServerMessageView {
  switch (m.type) {
    case 'ready': return { type: 'ready', connectionId: m.connection_id, snapshot: m.snapshot };
    case 'telemetry': return { type: 'telemetry', snapshot: m.snapshot, events: m.events };
    case 'ack': return { type: 'ack', requestId: m.request_id, clientSeq: m.client_seq, action: m.action, generation: m.generation, appliedTick: m.applied_tick, snapshot: m.snapshot };
    case 'error': return { type: 'error', code: m.code, message: m.message, retryable: m.retryable, details: m.details, requestId: m.request_id, clientSeq: m.client_seq, currentGeneration: m.current_generation };
    case 'pong': return { type: 'pong', clientTimeMs: m.client_time_ms };
  }
}

// 그래프 한 점 (Snapshot 또는 GET /history 의 GraphSample 에서)
export interface GraphPointView {
  sampleTick: number;
  sampleTime: number;
  tauDet: number;
  tauEff: number;
  tauA2: number;
  alarmDet: boolean;
  alarmEff: boolean;
  active: boolean;
}
const readGraphSample = (g: GraphSample): GraphPointView => ({
  sampleTick: g.sample_tick, sampleTime: g.sample_time_s, tauDet: g.tau_det, tauEff: g.tau_eff, tauA2: g.tau_a2,
  alarmDet: g.alarm_det, alarmEff: g.alarm_eff, active: g.active,
});

export function readHistory(r: HistoryResponse) {
  return { generation: r.generation, truncated: r.truncated, availableFromTick: r.available_from_tick, latestTick: r.latest_tick, samples: r.samples.map(readGraphSample) };
}
export function readEventsResponse(r: EventsResponse) {
  return { generation: r.generation, truncated: r.truncated, availableFromEventSeq: r.available_from_event_seq, latestEventSeq: r.latest_event_seq, events: r.events };
}

// 보낸 command 의 재시도 판단용 (request_id·generation·action)
export function readSentCommand(m: ClientMessage): { requestId: string; generation: number; action: CommandAction; message: CommandMessage } | null {
  return m.type === 'command' ? { requestId: m.request_id, generation: m.generation, action: m.action, message: m } : null;
}

// 수신 순서 판단용 (generation, frame_seq)
export const snapshotOrder = (s: Snapshot): { generation: number; frameSeq: number } => ({ generation: s.generation, frameSeq: s.frame_seq });

// 최근 로그 한 줄 (TelemetryMessage.events). kind 는 프로그램 판단용, message 는 표시용
export interface EventView {
  generation: number;
  eventSeq: number;
  sampleTime: number | null;
  simTime: number;
  kind: EventKind;
  message: string;
}

export function readEvent(e: DemoEvent): EventView {
  return {
    generation: e.generation, eventSeq: e.event_seq, sampleTime: e.sample_time_s, simTime: e.sim_time_s,
    kind: e.kind, message: e.message,
  };
}

export function readConfig(c: DemoConfig): ConfigView {
  return {
    lanes: c.road.lanes.map(l => ({ id: l.id, centerY: l.center_y_m })),
    laneWidth: c.road.lane_width_m,
    outerEdges: c.road.outer_edges_y_m,
    innerEdges: c.road.inner_edges_y_m,
    dt: c.timing.dt_s,
    renderBufferMs: c.timing.render_buffer_ms,
    historySamples: c.timing.history_samples,
    inputRefreshMs: c.timing.input_refresh_ms,
    tauAlarmRef: c.thresholds.tau_alarm_reference,
    tauRecoveryRef: c.thresholds.tau_recovery_reference,
    keyboard: c.controls.keyboard.map(k => ({ code: k.code, label: k.label, description: k.description, mode: k.mode })),
    scenarios: c.scenarios.map(s => ({
      kind: s.kind, key: s.key, label: s.label, steeringBlocked: s.steering_blocked, autoEndS: s.auto_end_s,
    })),
    tutorial: c.tutorial.map(t => ({ step: t.step, highlightCodes: t.highlight_codes, text: t.text })),
    banner: c.banner,
    kDrainDefault: c.defaults.k_drain,
    availableSolvers: c.available_solvers,
  };
}

export function readVehicle(v: VehicleState): VehicleView {
  return {
    x: v.x_m, y: v.y_m, psi: v.psi_rad, v: v.v_mps, a: v.a_mps2, steer: v.steer_rad, r: v.yaw_rate_radps,
    len: v.length_m, wid: v.width_m, signal: v.signal_side,
  };
}

function readModel(m: ModelOutput | null): ModelView | null {
  if (!m) return null;
  const t = m.trust, al = m.alarms, sc = m.scores, ac = m.accumulator, cx = m.context, cal = m.calibration;
  return {
    tauDet: t.tau_det, tauEff: t.tau_eff, tauA2: t.tau_a2, tauRule: t.tau_rule,
    tauGru: t.tau_gru, tauR1: t.tau_r1, tauR2: t.tau_r2,
    alarmDet: al.alarm_det, alarmEff: al.alarm_eff, alarmA2: al.alarm_a2,
    e: sc.e, eLong: sc.e_long, eLat: sc.e_lat, rA: sc.r_a_mps2, R1S: sc.R1_S,
    R2ttb: sc.R2_ttb_s, R2reason: sc.R2_ttb_reason,
    S: ac.S, Sprime: ac.S_prime, Seff: ac.S_eff, clipped: ac.clipped, holdOn: ac.hold_on,
    active: cx.active, intentWindow: cx.intent_window, zone: cx.zone, dx: cx.dx_m, g: cx.g,
    r2Enabled: cx.r2_enabled, r2CalibOk: cx.r2_calib_ok,
    theta: cal.theta, h: cal.h, hD: cal.h_d, Tc: cal.T_c_s,
  };
}

function readEgoControl(ec: EgoControl): EgoControlView {
  const p = ec.policy, n = ec.nmpc;
  return {
    controlMode: ec.control_mode, solver: ec.solver, state: ec.state,
    cmdAppliedA: ec.command_applied.a_mps2, cmdAppliedSteer: ec.command_applied.steer_rad,
    cmdNextA: ec.command_next.a_mps2, cmdNextSteer: ec.command_next.steer_rad,
    reacted: ec.reacted, engaged: ec.engaged, hit: ec.hit, collisionCount: ec.collision_count,
    dx: ec.dx_m, dy: ec.dy_m, gapLong: ec.gap_long_m, gapLat: ec.gap_lat_m, gap: ec.gap_m, gapMin: ec.gap_min_m,
    ttc: ec.ttc_s, ttcReason: ec.ttc_reason,
    stage: p.stage, danger: p.danger, emergency: p.emergency, rearOnly: p.rear_only,
    targetY: p.target_y_m, policyTargetY: p.policy_target_y_m, nmpcOverride: p.nmpc_override,
    vRef: p.v_ref_mps, brake: p.brake_mps2, policyTtc: p.ttc_s, policyTtcReason: p.ttc_reason,
    nmpc: n && {
      state: n.state, solveMs: n.solve_ms, fallbackCount: n.fallback_count, fallbackNow: n.fallback_now,
      dSafe: n.d_safe_m, targetY: n.target_y_m,
      predicted: n.predicted_trajectory.map(q => ({ offset: q.offset_s, x: q.x_m, y: q.y_m })),
    },
  };
}

export function readSnapshot(s: Snapshot): SnapshotView {
  const ob = s.observation, sc = s.scenario, rep = s.last_scenario_report, inp = s.inputs, rt = s.runtime;
  const pe = s.plant.ego.last;
  return {
    generation: s.generation, frameSeq: s.frame_seq, sampleTick: s.sample_tick,
    sampleTime: s.sample_time_s, simTime: s.sim_time_s,
    phase: s.phase, modelStatus: s.model_status, warmupRemaining: s.warmup_remaining_samples,
    kDrain: s.initial_config.k_drain,
    ego: readVehicle(s.vehicles.ego), nb: readVehicle(s.vehicles.neighbor),
    obs: ob && { dObs: ob.d_obs_m, sentSignal: ob.sent_signal },
    model: readModel(s.model),
    ec: readEgoControl(s.ego_control),
    plantEgo: pe && { a: pe.a, delta: pe.delta, aLat: pe.a_lat },
    scenario: sc && {
      kind: sc.kind, params: sc.parameters, elapsed: sc.elapsed_s, directionSign: sc.direction_sign,
      startSampleTime: sc.start_sample_time_s, steeringBlocked: sc.steering_blocked, autoEnd: sc.auto_end_s,
    },
    report: rep && {
      kind: rep.kind, startSampleTime: rep.start_sample_time_s,
      boundaryDelay: rep.boundary_cross_delay_s, detectionDelay: rep.detection_delay_s,
      reactionDelay: rep.reaction_delay_s, a2Delay: rep.a2_delay_s,
    },
    nextWeave: { amp: s.next_weave.amplitude_m, period: s.next_weave.period_s },
    inputs: { heldReceived: inp.held_received, heldApplied: inp.held_applied, steeringBlocked: inp.steering_blocked },
    tutorial: { step: s.tutorial.step, completed: s.tutorial.completed },
    runtime: {
      connected: rt.connected, stepMs: rt.step_ms, lagMs: rt.lag_ms, pauseReason: rt.pause_reason, resumePhase: rt.resume_phase,
      error: rt.error && { code: rt.error.code, message: rt.error.message },
    },
    auto: s.auto && s.auto.enabled ? {
      seed: s.auto.seed, levelSetting: s.auto.level_setting, level: s.auto.level, state: s.auto.state,
      stoppedReason: s.auto.stopped_reason ?? null, eventKind: s.auto.event_kind, nextEventIn: s.auto.next_event_in_s,
      edgeMargin: s.auto.edge_margin_m, log: s.auto.log.map(l => ({ t: l.t_s, text: l.text })),
    } : null,
  };
}
