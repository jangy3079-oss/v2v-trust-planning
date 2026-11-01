/*
 * 목업 서버의 DemoConfig (GET /demo-config).
 * 목업 서버는 화면·연결 검증용이며 실제 15c 시뮬레이션·모델이 아니다. 보정값·문구는 모두 목업용이다 (// MOCK).
 * 계약 필드와 리터럴 타입은 src/contracts.ts 를 따른다. 프론트 코드·상수는 공유하지 않는다.
 */
import type { DemoConfig } from '../src/contracts.ts';

export const MOCK_DEMO_CONFIG: DemoConfig = {
  protocol_version: 'v1',
  model_version: 'demo_v15c', // 계약상 리터럴. 목업 서버는 이 모델을 실행하지 않는다
  available_solvers: ['rule'],
  defaults: {
    control_mode: 'combined', solver: 'rule', seed: 0, sigma_meas: 0.1, signal_assumption: true, bg: true,
    gap_m: 40, neighbor_lane_id: 2, neighbor_speed_mps: 25, ego_speed_mps: 25, k_drain: 1.3,
  },
  calibration: {
    setting: 'nom', model_name: 'B1_sd0.23_s0', // 계약상 리터럴
    // MOCK: 목업 누적기용 임의 값 (실제 보정값 아님)
    theta: 1.0, nu: 0.3, h: 3.0, h_d: 2.0, T_c_s: 2.0, r2_calib_ok: false,
    hold_s: 3, decay_time_s: 20, cap_theta_multiple: 2,
    source_ids: ['mock/not-calibrated'],
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
  banner: '목업 서버 (실제 15c 아님) · 합성 환경 · 직선 도로 · 단순 근사 시뮬레이션',
};
