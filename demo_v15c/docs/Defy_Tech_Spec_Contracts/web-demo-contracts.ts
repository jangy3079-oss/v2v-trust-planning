/**
 * 15c web demo protocol v1 — implementation contract, not an existing server.
 * Spec 1.2 (v15c-fix): lane-anchored ego targets, NMPC as planner (policy_target_y_m / nmpc_override added).
 * Spec 1.4: optional auto drive (server-side virtual driver for the neighbor). Additive only; protocol stays v1.
 * Normative behavior: WEB_DEMO_TECH_SPEC.md.
 * JSON numbers must be finite; integer IDs/cursors must be safe integers.
 * All object fields are required unless marked ?. Unknown request fields fail.
 * Arrays representing sets must contain unique values. Times use SI units.
 */
export type ProtocolVersion = "v1";
export type Solver = "rule" | "nmpc";
export type DriveKey = "W" | "A" | "S" | "D";
export type SignalSide = "off" | "left" | "right";
export type ScenarioKind = "drift" | "stuck" | "weave" | "brake" | "accel";
export type TutorialStep = 0 | 1 | 2 | 3;
export type Phase = "tutorial" | "running" | "paused" | "error";
export type PauseReason = "focus_lost" | "user" | "input_timeout" | "disconnected" | "overload";
export type Zone = "front" | "rear" | "alongside";
export type Timestamp = string; // UTC ISO 8601

export type ErrorCode =
  | "INVALID_ARGUMENT" | "UNAUTHENTICATED" | "FORBIDDEN"
  | "SESSION_NOT_FOUND" | "SESSION_EXPIRED" | "CONTROL_CONFLICT"
  | "STALE_GENERATION" | "INVALID_STATE" | "TUTORIAL_STEP_MISMATCH"
  | "STALE_CONNECTION" | "STALE_CLIENT_SEQ" | "REQUEST_ID_CONFLICT"
  | "IDEMPOTENCY_CONFLICT" | "RESET_CANCELLED" | "RATE_LIMITED"
  | "SOLVER_UNAVAILABLE" | "MODEL_UNAVAILABLE" | "INTERNAL_ERROR";

export interface ApiError {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  details: Record<string, string | number | boolean | null>;
}
export interface ErrorResponse { error: ApiError }

// GET /api/v1/health (200 or 503)
export interface HealthResponse {
  protocol_version: ProtocolVersion;
  ready: boolean;
  model_version: "demo_v15c";
  available_solvers: Solver[];
  auto_drive_available: boolean; // 1.4: false on sample/mock servers (client hides the toggle)
  server_time: Timestamp;
}

export interface InitialConfig {
  control_mode: "combined";
  solver: Solver;
  seed: 0;
  sigma_meas: 0.1;
  signal_assumption: true;
  bg: true;
  gap_m: 40;
  neighbor_lane_id: 2;
  neighbor_speed_mps: 25;
  ego_speed_mps: 25;
  k_drain: 1.3;
}
export interface Calibration {
  setting: "nom";
  model_name: "B1_sd0.23_s0";
  theta: number; // > 0
  nu: number;
  h: number; // > 0
  h_d: number; // > 0
  T_c_s: number;
  r2_calib_ok: boolean;
  hold_s: 3;
  decay_time_s: 20;
  cap_theta_multiple: 2;
  source_ids: string[]; // public relative identifiers, not absolute server paths
}
export type ScenarioParameters =
  | { kind: "drift"; lateral_speed_mps: number; ramp_s: 2 }
  | { kind: "stuck"; r0_radps: number }
  | { kind: "weave"; amplitude_m: number; period_s: number; ramp_s: 7.5 }
  | { kind: "brake"; added_accel_mps2: -6; duration_s: 2 }
  | { kind: "accel"; added_accel_mps2: 3; ramp_s: 1.5; hold_s: 2 };

// GET /api/v1/demo-config (200)
export interface DemoConfig {
  protocol_version: ProtocolVersion;
  model_version: "demo_v15c";
  available_solvers: Solver[];
  defaults: InitialConfig; // solver=rule
  calibration: Calibration;
  road: {
    lane_count: 3;
    lane_width_m: 3.5;
    // Exactly [{id:0,center_y_m:-3.5},{id:1,center_y_m:0},{id:2,center_y_m:3.5}]
    lanes: { id: 0 | 1 | 2; center_y_m: number }[];
    outer_edges_y_m: [-5.25, 5.25];
    inner_edges_y_m: [-1.75, 1.75];
    vehicle_length_m: 4.5;
    vehicle_width_m: 1.8;
  };
  timing: {
    dt_s: 0.1;
    telemetry_hz: 10;
    render_target_fps: 60;
    render_buffer_ms: 100;
    detector_active_tick: 100;
    history_samples: 600;
    event_limit: 200;
    input_lease_ms: 750;
    input_refresh_ms: 250;
    disconnected_retention_s: 120;
  };
  controls: {
    keyboard: { code: string; label: string; description: string; mode: "hold" | "edge" }[];
    accel_target_mps2: 1.5;
    brake_target_mps2: -3;
    pedal_ramp_s: 2;
    yaw_command_limit_radps: 0.05;
    yaw_command_ramp_radps2: 0.05;
    yaw_release_time_s: 0.3;
    vehicle_speed_min_mps: 0;
    vehicle_speed_max_mps: null;
    plant_accel_limits_mps2: [-8, 3];
    plant_lateral_accel_limit_mps2: 8;
    actuator_lag_s: 0.3;
    plant_substep_s: 0.01;
  };
  scenarios: {
    kind: ScenarioKind;
    key: "1" | "2" | "3" | "4" | "5";
    label: string;
    first_parameters: ScenarioParameters;
    steering_blocked: boolean;
    auto_end_s: number | null;
  }[];
  // Actual execution cycle starting from reset, not raw SM.WEAVE_LEVELS order.
  weave_cycle: { amplitude_m: number; period_s: number }[];
  thresholds: { tau_alarm_reference: 0.7; tau_recovery_reference: 0.8 };
  tutorial: { step: TutorialStep; highlight_codes: string[]; text: string }[];
  banner: string;
}

// POST /api/v1/sessions. Header Idempotency-Key: UUID required.
// 1.4: auto_drive (default false) lets the server drive the neighbor; auto_seed is a safe integer >= 0 (default 0)
// that seeds only the virtual driver. Both take part in the Idempotency-Key body comparison.
export interface CreateSessionRequest { solver?: Solver; auto_drive?: boolean; auto_seed?: number }
export interface CreateSessionResponse {
  protocol_version: ProtocolVersion;
  session_id: string;
  session_token: string;
  ws_path: string;
  expires_after_disconnect_s: 120;
  snapshot: Snapshot;
}

export interface VehicleState {
  x_m: number;
  y_m: number;
  psi_rad: number;
  v_mps: number;
  a_mps2: number; // Vehicle.a at end of step
  steer_rad: number; // Vehicle.delta at end of step
  yaw_rate_radps: number; // Vehicle.r (not neighbor obs.r, which adds background)
  length_m: 4.5;
  width_m: 1.8;
  signal_side: SignalSide; // ego always off in v1
}
export interface Observation {
  a_obs_mps2: number;
  r_obs_radps: number;
  d_obs_m: number;
  v_meas_mps: number;
  y_obs_m: number;
  d_true_m: number;
  sent_signal: 0 | 1; // Session.sent_signal()
}
export interface TrustValues {
  tau_det: number;
  tau_eff: number;
  tau_a2: number;
  tau_rule: number;
  tau_gru: number;
  tau_r1: number;
  tau_r2: number;
  tau_state: number;
  tau_state_eff: number;
} // all in [0,1], not probabilities
export interface AlarmValues {
  alarm_det: boolean;
  alarm_eff: boolean;
  alarm_a2: boolean;
  alarm_gru: boolean;
  alarm_rule: boolean;
  alarm_r1: boolean;
  alarm_r2: boolean;
  alarm_r2_shadow: boolean;
  alarm_path: boolean;
  alarm_eff_path: boolean;
}
export interface ModelOutput {
  trust: TrustValues;
  alarms: AlarmValues;
  scores: {
    e: number;
    e_long: number;
    e_lat: number;
    r_a_mps2: number;
    R1_S: number;
    R2_ttb_s: number | null;
    R2_ttb_reason: "finite" | "no_crossing_within_horizon";
    d_hat_m: number;
    psi_hat_rad: number;
  };
  accumulator: {
    S: number;
    S_prime: number;
    S_eff: number;
    clipped: boolean;
    hold_on: boolean;
  };
  context: {
    active: boolean;
    intent_window: boolean;
    zone: Zone;
    dx_m: number;
    g: boolean;
    r2_enabled: boolean;
    r2_calib_ok: boolean;
  };
  calibration: Calibration;
}
export interface ControlCommand {
  a_mps2: number;
  steer_rad: number;
}
export interface EgoControl {
  control_mode: "combined";
  solver: Solver;
  state: "NORMAL" | "AVOIDING" | "RETURNING";
  command_applied: ControlCommand; // prior requested u=(a,delta), NOT filtered plant output
  command_next: ControlCommand; // Session.u and ego_rec.steer; all 0 before step
  reacted: boolean; // original Session.ego_rec.reacted
  engaged: boolean; // current common policy engagement for either solver
  hit: boolean;
  collision_count: number;
  dx_m: number;
  dy_m: number;
  gap_long_m: number;
  gap_lat_m: number;
  gap_m: number;
  gap_min_m: number | null; // null before any step
  ttc_s: number | null;
  ttc_reason: "finite" | "not_ahead_or_not_closing" | "not_sampled";
  policy: {
    stage: 0 | 1 | 2 | 3;
    closing_mps: number;
    danger: boolean;
    rear_only: boolean;
    emergency: boolean;
    // Tracked lateral goal = ctl.info.target_y (policy.active_target). Always one of
    // {-4,-3.5,-3,-0.5,0,0.5,3,3.5,4} (lane centre ± 0.5 m).
    target_y_m: number;
    policy_target_y_m: number; // ctl.policy.target_y: the state machine's own goal
    nmpc_override: boolean; // ctl.policy.ov !== null; always false for rule
    v_ref_mps: number;
    brake_mps2: number;
    ttc_s: number | null; // ctl.info.ttc, includes lateral risk
    ttc_reason: "finite" | "no_predicted_contact" | "not_sampled";
    raw_a_mps2: number | null;
    raw_steer_rad: number | null;
    command_clips: number;
  };
  nmpc: {
    state: "NORMAL" | "AVOIDING" | "RETURNING";
    solve_ms: number;
    fallback_count: number;
    fallback_now: boolean;
    d_safe_m: number | null; // null before the first solve
    target_y_m: number | null; // same value as policy.target_y_m
    // NMPC plan only, NOT the executed ego path (NMPC steering is not applied to the Vehicle).
    // Rows of impl.x_pred transposed. offset_s=index*cfg.dt, or [] before solve.
    predicted_trajectory: { offset_s: number; x_m: number; y_m: number; psi_rad: number; v_mps: number }[];
  } | null;
}
export interface PlantTelemetry {
  // null last before first step; counts start at 0. Direct Vehicle.last/counts.
  last: {
    a: number; delta: number; r: number; a_lat: number;
    jerk_max: number; a_lat_max: number; r_max: number;
    delta_rate_max: number; rate_violation: number; slip_max_deg: number;
    clips: PlantLimitCounts;
  } | null;
  counts: PlantLimitCounts;
}
export interface PlantLimitCounts {
  accel_command: number;
  steer_command: number;
  steer_rate: number;
  speed_lat: number;
}
export interface ActiveScenario {
  kind: ScenarioKind;
  parameters: ScenarioParameters; // parameters.kind must match kind
  start_tick: number; // sim.inj.k0
  start_sample_time_s: number;
  elapsed_s: number;
  direction_sign: -1 | 1; // ignored physically for brake/accel
  lane_center_at_start_m: number;
  steering_blocked: boolean;
  auto_end_s: number | null;
}
export interface ScenarioReport {
  kind: ScenarioKind;
  parameters: ScenarioParameters;
  start_sample_time_s: number;
  boundary_cross_delay_s: number | null;
  detection_delay_s: number | null;
  reaction_delay_s: number | null;
  a2_delay_s: number | null;
} // null delay means not observed in source report; not necessarily impossible

// 1.4 auto drive. cruise = before the first countdown, wait = counting down to the next event,
// event = an event is running, recover = event over, waiting for the recovery condition.
export type AutoDriveState = "cruise" | "wait" | "event" | "recover";
export interface AutoDriveStatus {
  enabled: boolean; // false: manual session (other fields are inert)
  seed: number;
  state: AutoDriveState;
  event_kind: string | null; // ScenarioKind or "cut_in"; null outside event/recover
  next_event_in_s: number | null; // only in wait
  edge_margin_m: number; // 5.25 − (|neighbor y| + 0.9): distance from the neighbor body to the road edge
  log: { t_s: number; text: string }[]; // latest 20, oldest first
}

// GET /sessions/{id}, also nested in WS ready/telemetry/ack.
export interface Snapshot {
  protocol_version: ProtocolVersion;
  session_id: string;
  generation: number; // positive integer
  frame_seq: number; // integer >=0, independent of sample_tick
  sample_tick: number; // integer >=-1
  sample_time_s: number | null;
  sim_time_s: number;
  server_time: Timestamp;
  phase: Phase;
  initial_config: InitialConfig;
  model_status: "not_started" | "warming_up" | "active" | "error";
  // max(0, 100-sample_tick), including 101 before first step.
  warmup_remaining_samples: number;
  vehicles: { ego: VehicleState; neighbor: VehicleState };
  observation: Observation | null;
  model: ModelOutput | null;
  ego_control: EgoControl;
  plant: { ego: PlantTelemetry; neighbor: PlantTelemetry; neighbor_command: { a_mps2: number; steer_rad: number } | null };
  scenario: ActiveScenario | null;
  last_scenario_report: ScenarioReport | null;
  next_weave: { amplitude_m: number; period_s: number };
  inputs: {
    held_received: DriveKey[];
    held_applied: DriveKey[]; // includes consumed tapped for the last step
    last_received_input_seq: number | null;
    last_applied_input_seq: number | null;
    input_connection_id: string | null; // seq is meaningful only within this connection
    a_user_mps2: number;
    r_cmd_radps: number;
    steering_blocked: boolean;
  };
  tutorial: { step: TutorialStep | null; completed: boolean };
  runtime: {
    connected: boolean;
    step_ms: number | null;
    lag_ms: number;
    pause_reason: PauseReason | null;
    resume_phase: "tutorial" | "running" | null;
    error: ApiError | null;
  };
  // 1.4: absent from pre-1.4 servers. In auto mode inputs.held_applied are the driver's keys and held_received is [].
  auto?: AutoDriveStatus;
}

// Graph history: the server stores one sample per completed step.
export interface GraphSample {
  sample_tick: number;
  sample_time_s: number;
  tau_det: number;
  tau_eff: number;
  tau_a2: number;
  alarm_det: boolean;
  alarm_eff: boolean;
  active: boolean;
}
export interface HistoryQuery { generation: number; after_tick?: number }
export interface HistoryResponse {
  session_id: string;
  generation: number;
  available_from_tick: number | null;
  latest_tick: number | null;
  truncated: boolean;
  samples: GraphSample[];
}
export type EventKind =
  | "session.created" | "session.reset" | "session.paused" | "session.resumed"
  | "tutorial.advanced" | "tutorial.completed" | "signal.changed"
  | "scenario.started" | "scenario.stopped" | "scenario.finished"
  | "alarm.det.started" | "alarm.det.ended" | "alarm.eff.started" | "alarm.eff.ended"
  | "collision" | "runtime.error";
export interface DemoEvent {
  generation: number;
  event_seq: number; // starts at 1 per generation
  sample_tick: number; // -1 if no sample exists
  sample_time_s: number | null;
  sim_time_s: number;
  kind: EventKind;
  message: string; // Korean display text; use kind for program logic
  request_id: string | null;
}
export interface EventsQuery { generation: number; after_event_seq?: number }
export interface EventsResponse {
  session_id: string;
  generation: number;
  available_from_event_seq: number | null;
  latest_event_seq: number | null;
  truncated: boolean;
  events: DemoEvent[];
}

export interface HelloMessage {
  protocol_version: ProtocolVersion;
  type: "hello";
  session_token: string;
}
export interface ClientEnvelope {
  protocol_version: ProtocolVersion;
  connection_id: string;
  client_seq: number; // integer >=1, strictly increasing per connection
}
export interface InputMessage extends ClientEnvelope {
  type: "input.state";
  generation: number;
  held: DriveKey[];
  tapped: DriveKey[];
}
export type CommandBody =
  | { action: "signal.toggle"; payload: { side: "left" | "right" } }
  | { action: "scenario.start"; payload: { kind: ScenarioKind } }
  | { action: "scenario.stop"; payload: Record<string, never> }
  | { action: "session.reset"; payload: Record<string, never> }
  | { action: "tutorial.advance"; payload: { expected_step: TutorialStep } }
  | { action: "session.pause"; payload: { reason: "focus_lost" | "user" } }
  | { action: "session.resume"; payload: Record<string, never> };
export type CommandAction = CommandBody["action"];
export type CommandMessage = ClientEnvelope & {
  type: "command";
  generation: number;
  request_id: string;
} & CommandBody;
export interface PingMessage extends ClientEnvelope {
  type: "ping";
  client_time_ms: number;
}
export type ClientMessage = HelloMessage | InputMessage | CommandMessage | PingMessage;

export interface ServerEnvelope {
  protocol_version: ProtocolVersion;
  session_id: string;
  server_time: Timestamp;
}
export interface ReadyMessage extends ServerEnvelope {
  type: "ready";
  connection_id: string;
  snapshot: Snapshot;
  retention: { history_samples: 600; event_limit: 200; disconnected_seconds: 120 };
}
export interface TelemetryMessage extends ServerEnvelope {
  type: "telemetry";
  snapshot: Snapshot;
  events: DemoEvent[];
}
export interface AckMessage extends ServerEnvelope {
  type: "ack";
  request_id: string;
  client_seq: number; // the currently acknowledged delivery (may be a retry)
  action: CommandAction;
  generation: number; // resulting generation, increments for reset
  applied_tick: number | null;
  snapshot: Snapshot; // original result snapshot for duplicate request IDs
}
export interface ErrorMessage extends ServerEnvelope, ApiError {
  type: "error";
  request_id: string | null;
  client_seq: number | null;
  current_generation: number | null; // null before authentication
}
export interface PongMessage extends ServerEnvelope {
  type: "pong";
  client_time_ms: number;
}
export type ServerMessage = ReadyMessage | TelemetryMessage | AckMessage | ErrorMessage | PongMessage;
