/*
 * 샘플 데이터 — 실제 15c 출력이 아니다. 화면 확인용 가짜 값이다.
 * 형식은 contracts.ts 의 DemoConfig / Snapshot 타입 검사를 받는다.
 * 여기의 수치(신뢰도·경보·궤적)는 실제 알고리즘 동작과 다를 수 있으며, 화면 코드는 이 값을 근거로
 * 어떤 로직도 만들지 않는다. 서버가 붙으면 source/ 의 샘플 재생은 통째로 빠진다.
 */
import type {
  ActiveScenario,
  DemoConfig,
  DemoEvent,
  Solver,
  EgoControl,
  ModelOutput,
  PauseReason,
  PlantLimitCounts,
  ScenarioReport,
  SignalSide,
  Snapshot,
  TutorialStep,
  VehicleState,
  Zone,
} from '../contracts';

const r4 = (n: number): number => Math.round(n * 1e4) / 1e4;
const BASE_TIME_MS = Date.UTC(2026, 9, 31, 0, 0, 0);

// ---- DemoConfig (GET /demo-config 응답 형식) ----
export const SAMPLE_DEMO_CONFIG: DemoConfig = {
  protocol_version: 'v1',
  model_version: 'demo_v15c',
  available_solvers: ['rule'],
  defaults: {
    control_mode: 'combined', solver: 'rule', seed: 0, sigma_meas: 0.1, signal_assumption: true, bg: true,
    gap_m: 40, neighbor_lane_id: 2, neighbor_speed_mps: 25, ego_speed_mps: 25, k_drain: 1.3,
  },
  calibration: {
    setting: 'nom', model_name: 'B1_sd0.23_s0',
    // 샘플 보정값 (가짜)
    theta: 2.0, nu: 0.5, h: 4.0, h_d: 3.0, T_c_s: 2.0, r2_calib_ok: true,
    hold_s: 3, decay_time_s: 20, cap_theta_multiple: 2,
    source_ids: ['sample/not-real'],
  },
  road: {
    lane_count: 3,
    lane_width_m: 3.5,
    lanes: [{ id: 0, center_y_m: -3.5 }, { id: 1, center_y_m: 0 }, { id: 2, center_y_m: 3.5 }],
    outer_edges_y_m: [-5.25, 5.25],
    inner_edges_y_m: [-1.75, 1.75],
    vehicle_length_m: 4.5,
    vehicle_width_m: 1.8,
  },
  timing: {
    dt_s: 0.1, telemetry_hz: 10, render_target_fps: 60, render_buffer_ms: 100, detector_active_tick: 100,
    history_samples: 600, event_limit: 200, input_lease_ms: 750, input_refresh_ms: 250, disconnected_retention_s: 120,
  },
  controls: {
    keyboard: [
      { code: 'KeyW', label: 'W', description: '가속', mode: 'hold' },
      { code: 'KeyA', label: 'A', description: '좌조향', mode: 'hold' },
      { code: 'KeyS', label: 'S', description: '감속', mode: 'hold' },
      { code: 'KeyD', label: 'D', description: '우조향', mode: 'hold' },
      { code: 'KeyQ', label: 'Q', description: '좌 깜빡이', mode: 'edge' },
      { code: 'KeyE', label: 'E', description: '우 깜빡이', mode: 'edge' },
      { code: 'Digit1', label: '1', description: '드리프트', mode: 'edge' },
      { code: 'Digit2', label: '2', description: '조향 고정', mode: 'edge' },
      { code: 'Digit3', label: '3', description: '위빙', mode: 'edge' },
      { code: 'Digit4', label: '4', description: '급제동', mode: 'edge' },
      { code: 'Digit5', label: '5', description: '급가속', mode: 'edge' },
      { code: 'Digit0', label: '0', description: '시나리오 해제', mode: 'edge' },
      { code: 'KeyR', label: 'R', description: '초기화', mode: 'edge' },
    ],
    accel_target_mps2: 1.5, brake_target_mps2: -3, pedal_ramp_s: 2,
    yaw_command_limit_radps: 0.05, yaw_command_ramp_radps2: 0.05, yaw_release_time_s: 0.3,
    vehicle_speed_min_mps: 0, vehicle_speed_max_mps: null,
    plant_accel_limits_mps2: [-8, 3], plant_lateral_accel_limit_mps2: 8,
    actuator_lag_s: 0.3, plant_substep_s: 0.01,
  },
  scenarios: [
    { kind: 'drift', key: '1', label: '드리프트', first_parameters: { kind: 'drift', lateral_speed_mps: 0.2, ramp_s: 2 }, steering_blocked: true, auto_end_s: null },
    { kind: 'stuck', key: '2', label: '조향 고정', first_parameters: { kind: 'stuck', r0_radps: 0.01 }, steering_blocked: true, auto_end_s: null },
    { kind: 'weave', key: '3', label: '위빙', first_parameters: { kind: 'weave', amplitude_m: 0.8, period_s: 3.5, ramp_s: 7.5 }, steering_blocked: true, auto_end_s: null },
    { kind: 'brake', key: '4', label: '급제동', first_parameters: { kind: 'brake', added_accel_mps2: -6, duration_s: 2 }, steering_blocked: false, auto_end_s: 2 },
    { kind: 'accel', key: '5', label: '급가속', first_parameters: { kind: 'accel', added_accel_mps2: 3, ramp_s: 1.5, hold_s: 2 }, steering_blocked: false, auto_end_s: 3.5 },
  ],
  weave_cycle: [
    { amplitude_m: 0.8, period_s: 3.5 },
    { amplitude_m: 1.2, period_s: 4.0 },
    { amplitude_m: 0.5, period_s: 5.0 },
  ],
  thresholds: { tau_alarm_reference: 0.7, tau_recovery_reference: 0.8 },
  tutorial: [
    { step: 0, highlight_codes: ['KeyW', 'KeyA', 'KeyS', 'KeyD'], text: 'W/S로 가속·감속하고 A/D로 좌우 조향합니다. 이웃차를 직접 운전해 보세요.' },
    { step: 1, highlight_codes: ['KeyQ', 'KeyE'], text: 'Q는 왼쪽, E는 오른쪽 깜빡이입니다. 같은 키를 다시 누르면 꺼집니다.' },
    { step: 2, highlight_codes: ['Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5'], text: '1 드리프트 · 2 조향 고정 · 3 위빙 · 4 급제동 · 5 급가속을 실행합니다. 자차는 이웃차가 자기 진로에 들어올 때만 피합니다(시작 직후 약 10초는 준비 중).' },
    { step: 3, highlight_codes: ['KeyR'], text: 'R을 누르면 차량, 속도, 신뢰도, 그래프와 이 안내까지 처음으로 돌아갑니다.' },
  ],
  banner: '합성 환경 · 직선 도로 · 시뮬레이션 · 방향지시등 신호 가정',
};

// ---- Snapshot 조립 도우미 (가짜 서버 역할) ----
interface VehicleOpts { a?: number; steer?: number; r?: number; signal?: SignalSide }

export function vehicle(x: number, y: number, psi: number, v: number, o: VehicleOpts = {}): VehicleState {
  return {
    x_m: r4(x), y_m: r4(y), psi_rad: r4(psi), v_mps: r4(v),
    a_mps2: r4(o.a ?? 0), steer_rad: r4(o.steer ?? 0), yaw_rate_radps: r4(o.r ?? 0),
    length_m: 4.5, width_m: 1.8, signal_side: o.signal ?? 'off',
  };
}

const zeroCounts = (): PlantLimitCounts => ({ accel_command: 0, steer_command: 0, steer_rate: 0, speed_lat: 0 });

const laneCenterOf = (y: number): number =>
  SAMPLE_DEMO_CONFIG.road.lanes.reduce((best, l) => (Math.abs(l.center_y_m - y) < Math.abs(best - y) ? l.center_y_m : best), 0);

// 샘플용 근사 간격 (실제 서버는 회전 차체 폴리곤 거리)
function sampleGaps(ego: VehicleState, nb: VehicleState) {
  const gl = Math.max(0, Math.abs(nb.x_m - ego.x_m) - 4.5);
  const gt = Math.max(0, Math.abs(nb.y_m - ego.y_m) - 1.8);
  return { gap_long_m: r4(gl), gap_lat_m: r4(gt), gap_m: r4(Math.hypot(gl, gt)) };
}

export interface ModelOpts {
  tau_det: number;
  tau_eff: number;
  tau_a2: number;
  tau_rule?: number;
  alarm_det?: boolean;
  alarm_eff?: boolean;
  alarm_a2?: boolean;
  active: boolean;
  intent_window?: boolean;
  zone?: Zone;
  g?: boolean;
  r2_enabled?: boolean;
  e_long?: number;
  e_lat?: number;
  r_a?: number;
  R1_S?: number;
  R2_ttb_s?: number;
  S?: number;
  S_prime?: number;
  S_eff?: number;
  clipped?: boolean;
  hold_on?: boolean;
  d_hat?: number;
  psi_hat?: number;
}

function model(o: ModelOpts, dx: number): ModelOutput {
  const cal = SAMPLE_DEMO_CONFIG.calibration;
  return {
    trust: {
      tau_det: r4(o.tau_det), tau_eff: r4(o.tau_eff), tau_a2: r4(o.tau_a2),
      tau_rule: r4(o.tau_rule ?? 1), tau_gru: r4(o.tau_a2), tau_r1: 1, tau_r2: 1,
      tau_state: r4(o.tau_det), tau_state_eff: r4(o.tau_eff),
    },
    alarms: {
      alarm_det: !!o.alarm_det, alarm_eff: !!o.alarm_eff, alarm_a2: !!o.alarm_a2, alarm_gru: false, alarm_rule: false,
      alarm_r1: false, alarm_r2: false, alarm_r2_shadow: false, alarm_path: false, alarm_eff_path: false,
    },
    scores: {
      e: r4((o.e_long ?? 0) + (o.e_lat ?? 0)), e_long: r4(o.e_long ?? 0), e_lat: r4(o.e_lat ?? 0),
      r_a_mps2: r4(o.r_a ?? 0), R1_S: r4(o.R1_S ?? 0),
      R2_ttb_s: o.R2_ttb_s != null ? r4(o.R2_ttb_s) : null,
      R2_ttb_reason: o.R2_ttb_s != null ? 'finite' : 'no_crossing_within_horizon',
      d_hat_m: r4(o.d_hat ?? 0), psi_hat_rad: r4(o.psi_hat ?? 0),
    },
    accumulator: {
      S: r4(o.S ?? 0), S_prime: r4(o.S_prime ?? o.S ?? 0), S_eff: r4(o.S_eff ?? 0),
      clipped: !!o.clipped, hold_on: !!o.hold_on,
    },
    context: {
      active: o.active, intent_window: !!o.intent_window,
      zone: o.zone ?? (dx > 10 ? 'front' : dx < -10 ? 'rear' : 'alongside'),
      dx_m: r4(dx), g: o.g ?? true, r2_enabled: o.r2_enabled ?? true, r2_calib_ok: cal.r2_calib_ok,
    },
    calibration: { ...cal, source_ids: [...cal.source_ids] },
  };
}

export interface SnapshotOpts {
  generation: number;
  frame_seq: number;
  sample_tick: number;
  phase: Snapshot['phase'];
  tutorial_step?: TutorialStep;
  ego: VehicleState;
  neighbor: VehicleState;
  model?: ModelOpts;
  ego_control?: Partial<Pick<EgoControl, 'state' | 'reacted' | 'engaged' | 'ttc_s' | 'gap_min_m' | 'command_next' | 'command_applied'>>;
  policy?: Partial<EgoControl['policy']>;
  scenario?: ActiveScenario;
  last_scenario_report?: ScenarioReport;
  next_weave?: Snapshot['next_weave'];
  collision_count?: number;
  hit?: boolean;
  solver?: Solver;
  nmpc?: EgoControl['nmpc'];
}

export function makeSnapshot(o: SnapshotOpts): Snapshot {
  const tick = o.sample_tick;
  const sampled = tick >= 0;
  const ego = o.ego, nb = o.neighbor;
  const dx = nb.x_m - ego.x_m, dy = nb.y_m - ego.y_m;
  const gaps = sampleGaps(ego, nb);
  const ec = o.ego_control ?? {};
  const policy: EgoControl['policy'] = {
    stage: 0, closing_mps: 0, danger: false, rear_only: false, emergency: false,
    target_y_m: 0, policy_target_y_m: 0, nmpc_override: false, v_ref_mps: 25, brake_mps2: 0,
    ttc_s: null, ttc_reason: sampled ? 'no_predicted_contact' : 'not_sampled',
    raw_a_mps2: sampled ? 0 : null, raw_steer_rad: sampled ? 0 : null, command_clips: 0,
    ...o.policy,
  };
  const cmdNext = ec.command_next ?? { a_mps2: 0, steer_rad: 0 };
  const cmdApplied = ec.command_applied ?? { a_mps2: 0, steer_rad: 0 };
  const ttc = ec.ttc_s ?? null;

  const plantLast = (v: VehicleState) => (!sampled ? null : {
    a: v.a_mps2, delta: v.steer_rad, r: v.yaw_rate_radps, a_lat: r4(v.v_mps * v.yaw_rate_radps),
    jerk_max: 0, a_lat_max: 0, r_max: 0, delta_rate_max: 0, rate_violation: 0, slip_max_deg: 0, clips: zeroCounts(),
  });

  return {
    protocol_version: 'v1',
    session_id: 'sample_session',
    generation: o.generation,
    frame_seq: o.frame_seq,
    sample_tick: tick,
    sample_time_s: sampled ? r4(tick * 0.1) : null,
    sim_time_s: r4((tick + 1) * 0.1),
    server_time: new Date(BASE_TIME_MS + (tick + 1) * 100).toISOString(),
    phase: o.phase,
    initial_config: { ...SAMPLE_DEMO_CONFIG.defaults, solver: o.solver ?? 'rule' },
    model_status: !sampled ? 'not_started' : tick < 100 ? 'warming_up' : 'active',
    warmup_remaining_samples: Math.max(0, 100 - tick),
    vehicles: { ego, neighbor: nb },
    observation: !sampled ? null : {
      a_obs_mps2: nb.a_mps2, r_obs_radps: nb.yaw_rate_radps,
      d_obs_m: r4(nb.y_m - laneCenterOf(nb.y_m)), v_meas_mps: nb.v_mps,
      y_obs_m: nb.y_m, d_true_m: r4(nb.y_m - laneCenterOf(nb.y_m)),
      sent_signal: nb.signal_side === 'off' ? 0 : 1,
    },
    model: !sampled ? null : model(o.model ?? { tau_det: 1, tau_eff: 1, tau_a2: 1, active: tick >= 100 }, dx),
    ego_control: {
      control_mode: 'combined',
      solver: o.solver ?? 'rule',
      state: ec.state ?? 'NORMAL',
      command_applied: { a_mps2: r4(cmdApplied.a_mps2), steer_rad: r4(cmdApplied.steer_rad) },
      command_next: { a_mps2: r4(cmdNext.a_mps2), steer_rad: r4(cmdNext.steer_rad) },
      reacted: !!ec.reacted,
      engaged: !!ec.engaged,
      hit: !!o.hit,
      collision_count: o.collision_count ?? 0,
      dx_m: r4(dx), dy_m: r4(dy),
      gap_long_m: gaps.gap_long_m, gap_lat_m: gaps.gap_lat_m, gap_m: gaps.gap_m,
      gap_min_m: sampled ? r4(ec.gap_min_m ?? gaps.gap_m) : null,
      ttc_s: ttc != null ? r4(ttc) : null,
      ttc_reason: !sampled ? 'not_sampled' : ttc != null ? 'finite' : 'not_ahead_or_not_closing',
      policy,
      nmpc: o.nmpc ?? null,
    },
    plant: {
      ego: { last: plantLast(ego), counts: zeroCounts() },
      neighbor: { last: plantLast(nb), counts: zeroCounts() },
      neighbor_command: !sampled ? null : { a_mps2: nb.a_mps2, steer_rad: nb.steer_rad },
    },
    scenario: o.scenario ?? null,
    last_scenario_report: o.last_scenario_report ?? null,
    next_weave: o.next_weave ?? { amplitude_m: 0.8, period_s: 3.5 },
    inputs: {
      held_received: [], held_applied: [],
      last_received_input_seq: null, last_applied_input_seq: null, input_connection_id: null,
      a_user_mps2: 0, r_cmd_radps: 0,
      steering_blocked: !!o.scenario?.steering_blocked,
    },
    tutorial: o.phase === 'tutorial' ? { step: o.tutorial_step ?? 0, completed: false } : { step: null, completed: true },
    runtime: { connected: false, step_ms: sampled ? 3.0 : null, lag_ms: 0, pause_reason: null, resume_phase: null, error: null },
  };
}

// 초기 화면 (명세 8.1: 자차 중앙, 이웃 왼쪽 차로 40m 앞, 각 25m/s, tick −1)
export function initialSnapshot(generation: number, phase: 'tutorial' | 'running' = 'tutorial', frameSeq = 0, tutorialStep: TutorialStep = 0): Snapshot {
  return makeSnapshot({
    generation, frame_seq: frameSeq, sample_tick: -1, phase, tutorial_step: tutorialStep,
    ego: vehicle(0, 0, 0, 25),
    neighbor: vehicle(40, 3.5, 0, 25),
  });
}

// 정지/재개 직후 서버가 보내는 상태 변경 snapshot 흉내: 같은 표본, phase·runtime 만 바뀌고 입력은 빈 집합
export function withPhase(
  s: Snapshot, frameSeq: number, phase: Snapshot['phase'],
  pauseReason: PauseReason | null, resumePhase: 'tutorial' | 'running' | null,
): Snapshot {
  return {
    ...s,
    frame_seq: frameSeq,
    phase,
    inputs: { ...s.inputs, held_received: [], held_applied: [] },
    runtime: { ...s.runtime, pause_reason: pauseReason, resume_phase: resumePhase },
  };
}

// 로그 이벤트 (TelemetryMessage.events 형식)
export function event(generation: number, seq: number, tick: number, kind: DemoEvent['kind'], message: string): DemoEvent {
  return {
    generation, event_seq: seq, sample_tick: tick, sample_time_s: tick >= 0 ? r4(tick * 0.1) : null,
    sim_time_s: r4((tick + 1) * 0.1), kind, message, request_id: null,
  };
}

// ---- 정적 확인용 프레임 (표시 확인 전용, 재생 타임라인 아님) ----
export interface StaticFrame { id: string; label: string; snapshot: Snapshot; events: DemoEvent[] }

export const STATIC_FRAMES: StaticFrame[] = [
  { id: 'initial', label: '초기 화면 (tick −1, 안내 전)', snapshot: initialSnapshot(1), events: [] },
  {
    id: 'active', label: '활성 · 드리프트 진입 · 경보 (표시 확인용)',
    events: [
      event(1, 1, -1, 'session.created', '세션 생성 (샘플)'),
      event(1, 2, 120, 'signal.changed', '이웃차 왼쪽 깜빡이 켬 (샘플)'),
      event(1, 3, 280, 'scenario.started', '드리프트 시작 (샘플)'),
      event(1, 4, 305, 'alarm.det.started', '탐지 경보 시작 (샘플)'),
      event(1, 5, 312, 'alarm.eff.started', '반응 경보 시작 (샘플)'),
    ],
    snapshot: makeSnapshot({
      generation: 1, frame_seq: 321, sample_tick: 320, phase: 'running',
      ego: vehicle(800, -0.9, -0.035, 21.4, { a: -2.6, steer: -0.004, r: -0.012 }),
      neighbor: vehicle(822, 1.1, -0.05, 24.6, { signal: 'left', r: -0.01 }),
      model: { tau_det: 0.52, tau_eff: 0.61, tau_a2: 0.44, tau_rule: 0.9, alarm_det: true, alarm_eff: true, active: true, e_long: 1.1, e_lat: 3.2, r_a: -0.4, R1_S: 1.6, R2_ttb_s: 1.3, S: 6.1, S_eff: 5.2, d_hat: 1.1 },
      ego_control: { state: 'AVOIDING', reacted: true, engaged: true, ttc_s: 3.1, gap_min_m: 14.2, command_next: { a_mps2: -2.8, steer_rad: -0.004 }, command_applied: { a_mps2: -2.6, steer_rad: -0.003 } },
      policy: { stage: 2, closing_mps: 3.2, danger: true, target_y_m: -3.5, policy_target_y_m: -3.5, v_ref_mps: 21, brake_mps2: -2.8, ttc_s: 2.9, ttc_reason: 'finite' },
      scenario: { kind: 'drift', parameters: { kind: 'drift', lateral_speed_mps: 0.2, ramp_s: 2 }, start_tick: 280, start_sample_time_s: 28, elapsed_s: 4, direction_sign: -1, lane_center_at_start_m: 3.5, steering_blocked: true, auto_end_s: null },
    }),
  },
  {
    id: 'nmpc', label: 'NMPC 세션 · 회피 중 · 계획 궤적 (표시 확인용)',
    events: [
      event(1, 1, -1, 'session.created', '세션 생성 (샘플)'),
      event(1, 2, 400, 'scenario.started', '급제동 시작 (샘플)'),
      event(1, 3, 420, 'scenario.finished', '급제동 자동 종료 (샘플)'),
    ],
    snapshot: makeSnapshot({
      generation: 1, frame_seq: 431, sample_tick: 430, phase: 'running', solver: 'nmpc',
      ego: vehicle(1060, -0.6, -0.04, 20.5, { a: -3.1, steer: -0.005, r: -0.015 }),
      neighbor: vehicle(1088, 0.1, 0, 14.2, { a: -1.5 }),
      model: { tau_det: 0.66, tau_eff: 0.68, tau_a2: 0.58, active: true, alarm_det: true, alarm_eff: true, e_long: 2.4, e_lat: 0.3, r_a: -2.1, R1_S: 0.9, S: 4.6, S_eff: 4.3, clipped: true, hold_on: true },
      ego_control: { state: 'AVOIDING', reacted: true, engaged: true, ttc_s: 4.4, gap_min_m: 21.8, command_next: { a_mps2: -3.2, steer_rad: -0.006 }, command_applied: { a_mps2: -3.0, steer_rad: -0.005 } },
      policy: { stage: 2, closing_mps: 6.3, danger: true, target_y_m: -3, policy_target_y_m: -3.5, nmpc_override: true, v_ref_mps: 18, brake_mps2: -3.2, ttc_s: 3.6, ttc_reason: 'finite' },
      nmpc: {
        state: 'AVOIDING', solve_ms: 14.2, fallback_count: 1, fallback_now: false, d_safe_m: 9.5, target_y_m: -3,
        predicted_trajectory: Array.from({ length: 11 }, (_, i) => ({
          offset_s: r4(i * 0.1), x_m: r4(1060 + 20.5 * i * 0.1), y_m: r4(-0.6 - 0.12 * i), psi_rad: -0.05, v_mps: r4(20.5 - 0.3 * i),
        })),
      },
      last_scenario_report: { kind: 'brake', parameters: { kind: 'brake', added_accel_mps2: -6, duration_s: 2 }, start_sample_time_s: 40, boundary_cross_delay_s: null, detection_delay_s: 1.2, reaction_delay_s: 1.6, a2_delay_s: 0.8 },
    }),
  },
  {
    id: 'ego_left', label: '자차 왼쪽 차로 (라벨·도크 겹침 확인용)', events: [],
    snapshot: makeSnapshot({ generation: 1, frame_seq: 600, sample_tick: 599, phase: 'running', ego: vehicle(1500, 3.5, 0, 25), neighbor: vehicle(1530, 0, 0, 25), policy: { target_y_m: 3.5, policy_target_y_m: 3.5 } }),
  },
  {
    id: 'off_front', label: '이웃차 앞쪽 화면 밖 (dx +72m)', events: [],
    snapshot: makeSnapshot({ generation: 1, frame_seq: 500, sample_tick: 499, phase: 'running', ego: vehicle(1250, 0, 0, 25), neighbor: vehicle(1322, 3.5, 0, 32, { a: 2.4 }) }),
  },
  {
    id: 'off_rear', label: '이웃차 뒤쪽 화면 밖 (dx −30m)', events: [],
    snapshot: makeSnapshot({ generation: 1, frame_seq: 700, sample_tick: 699, phase: 'running', ego: vehicle(1750, 0, 0, 25), neighbor: vehicle(1720, 3.5, 0, 21, { a: -1.0 }) }),
  },
  {
    id: 'off_side', label: '이웃차 왼쪽 화면 밖 (극단값, 표시 확인 전용)', events: [],
    snapshot: makeSnapshot({ generation: 1, frame_seq: 800, sample_tick: 799, phase: 'running', ego: vehicle(2000, 0, 0, 25), neighbor: vehicle(2010, 60, 0.2, 25) }),
  },
];
