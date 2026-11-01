/*
 * 샘플 타임라인 (90초, 10Hz) — 화면 확인용 가짜 값. 실제 15c 알고리즘 동작과 다를 수 있다.
 * 여기서 만든 값은 "서버가 보낸 Snapshot" 역할만 한다. 화면 코드는 이 값을 근거로 로직을 만들지 않는다.
 *
 *   0–10s   준비 구간 (active=false, tau 1.0, 경보 없음). 10.0s 첫 활성 (tick 100)
 *   12–15s  이웃 Q 왼쪽 깜빡이 (의도 구간 ~18s)
 *   20–22s  4 급제동 — 이웃은 다른 차로. 탐지 경보만, 자차 무반응
 *   28–40s  1 드리프트 → 이웃이 자차 차로로 진입 → 탐지·반응 경보 → 자차 감속 + 오른쪽 차로(y=−3.5)로 차선변경. 40s 에 0(해제)
 *   48–51.5s 5 급가속 → 이웃이 앞으로 멀어져 화면 위로 나감 (51–56s 왼쪽 깜빡이 + 왼쪽 차로 복귀)
 *   56–59.6s 자차 원래 차로 복귀
 *   66–80s  3 위빙 (0.8m, 3.5s) — 이웃이 느려져 자차 뒤로 빠지며 화면 아래로 나감. 80s 에 0(해제)
 *   80–90s  회복
 */
import type { ActiveScenario, DemoEvent, ScenarioKind, ScenarioParameters, ScenarioReport, SignalSide, Snapshot } from '../contracts';
import { event, makeSnapshot, SAMPLE_DEMO_CONFIG, vehicle, type ModelOpts } from './fixtures';

export const TIMELINE_TICKS = 900;
const DT = 0.1;

const smooth = (u: number) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));
const bell = (t: number, c: number, w: number) => Math.exp(-(((t - c) / w) ** 2));
const inRange = (t: number, a: number, b: number) => t >= a && t < b;

// 키프레임 사이를 smoothstep 으로 잇는다
function keyed(keys: [number, number][], t: number): number {
  const first = keys[0]!, last = keys[keys.length - 1]!;
  if (t <= first[0]) return first[1];
  for (let i = 0; i < keys.length - 1; i++) {
    const [t0, v0] = keys[i]!, [t1, v1] = keys[i + 1]!;
    if (t <= t1) return v0 + (v1 - v0) * smooth((t - t0) / (t1 - t0));
  }
  return last[1];
}

const EGO_V: [number, number][] = [[0, 25], [34.9, 25], [37.9, 20.5], [46, 25]];
const EGO_Y: [number, number][] = [[0, 0], [35, 0], [38.6, -3.5], [56, -3.5], [59.6, 0]];
const NB_DX: [number, number][] = [
  [0, 40], [20, 40], [22.5, 30], [30, 24], [33, 22], [40, 26], [48, 30], [52, 46], [56, 70], [60, 85],
  [64, 70], [70, 20], [74, -14], [78, -26], [84, -10], [90, 40],
];
const NB_Y: [number, number][] = [[0, 3.5], [28, 3.5], [40, 0.4], [51, 0.3], [56, 3.5]];

interface ScenarioWindow { kind: ScenarioKind; start: number; end: number; how: 'finished' | 'stopped'; report: Omit<ScenarioReport, 'kind' | 'parameters' | 'start_sample_time_s'> }
const SCENARIOS: ScenarioWindow[] = [
  { kind: 'brake', start: 20, end: 22, how: 'finished', report: { boundary_cross_delay_s: null, detection_delay_s: 1.9, reaction_delay_s: null, a2_delay_s: 1.2 } },
  { kind: 'drift', start: 28, end: 40, how: 'stopped', report: { boundary_cross_delay_s: 4.6, detection_delay_s: 5.4, reaction_delay_s: 6.9, a2_delay_s: 4.1 } },
  { kind: 'accel', start: 48, end: 51.5, how: 'finished', report: { boundary_cross_delay_s: null, detection_delay_s: null, reaction_delay_s: null, a2_delay_s: null } },
  { kind: 'weave', start: 66, end: 80, how: 'stopped', report: { boundary_cross_delay_s: null, detection_delay_s: 8.5, reaction_delay_s: null, a2_delay_s: 6.8 } },
];
const SIGNAL_LEFT: [number, number][] = [[12, 15], [51, 56.5]];
const INTENT: [number, number][] = [[12, 18], [51, 59.5]];
const ALARM_DET: [number, number][] = [[21.9, 24.2], [33.4, 46], [74.5, 79.5]];
const ALARM_EFF: [number, number][] = [[34.9, 45.5]];
const HOLD_ON: [number, number][] = [[23.7, 24.2], [42.6, 46], [77.5, 79.5]];
const anyIn = (t: number, ranges: [number, number][]) => ranges.some(([a, b]) => inRange(t, a, b));

const firstParams = (kind: ScenarioKind): ScenarioParameters => {
  const s = SAMPLE_DEMO_CONFIG.scenarios.find(x => x.kind === kind);
  if (!s) throw new Error(`scenario ${kind} 없음`);
  return s.first_parameters;
};

function weaveOffset(t: number): number {
  if (!inRange(t, 66, 80)) return 0;
  const p = firstParams('weave');
  if (p.kind !== 'weave') return 0;
  return -p.amplitude_m * Math.sin((2 * Math.PI * (t - 66)) / p.period_s) * smooth((t - 66) / p.ramp_s);
}

export interface TimelineFrame { snapshot: Snapshot; events: DemoEvent[] }

export function buildTimeline(generation: number, firstEventSeq: number): TimelineFrame[] {
  // 1차: 연속량 (미분용으로 앞뒤 한 칸씩 여유)
  const N = TIMELINE_TICKS;
  const at = (i: number) => i * DT;
  const egoV = (i: number) => keyed(EGO_V, at(i));
  const egoY = (i: number) => keyed(EGO_Y, at(i));
  const dx = (i: number) => keyed(NB_DX, at(i));
  const nbY = (i: number) => keyed(NB_Y, at(i)) + weaveOffset(at(i));

  const egoX: number[] = [];
  let x = 0;
  for (let i = 0; i <= N; i++) { x += egoV(i) * DT; egoX.push(x); } // 첫 step 에서 전진한 뒤 t=0 (명세 4.1)

  const d = (f: (i: number) => number, i: number) => (f(i + 1) - f(i - 1)) / (2 * DT);
  const nbV = (i: number) => egoV(i) + d(dx, i);

  const frames: TimelineFrame[] = [];
  let seq = firstEventSeq;
  let prev: { det: boolean; eff: boolean; signal: SignalSide; scen: ScenarioKind | null } = { det: false, eff: false, signal: 'off', scen: null };
  let gapMin = Infinity;
  let lastReport: ScenarioReport | undefined;
  let prevCmd = { a_mps2: 0, steer_rad: 0 };
  const scenarioLabel = (k: ScenarioKind) => SAMPLE_DEMO_CONFIG.scenarios.find(s => s.kind === k)?.label ?? k;

  for (let i = 0; i < N; i++) {
    const t = at(i);
    const active = i >= 100;

    // 차량 (참 상태)
    const ev = egoV(i), ea = d(egoV, i), eyr = d(egoY, i);
    const epsi = Math.atan2(eyr, ev);
    const er = (Math.atan2(d(egoY, i + 1), egoV(i + 1)) - Math.atan2(d(egoY, i - 1), egoV(i - 1))) / (2 * DT);
    const nv = Math.max(0, nbV(i)), na = (nbV(i + 1) - nbV(i - 1)) / (2 * DT), nyr = d(nbY, i);
    const npsi = Math.atan2(nyr, Math.max(nv, 1));
    const nr = (Math.atan2(d(nbY, i + 1), Math.max(nbV(i + 1), 1)) - Math.atan2(d(nbY, i - 1), Math.max(nbV(i - 1), 1))) / (2 * DT);
    const signal: SignalSide = anyIn(t, SIGNAL_LEFT) ? 'left' : 'off';
    const ego = vehicle(egoX[i]!, egoY(i), epsi, ev, { a: ea, steer: Math.atan((2.7 * er) / ev), r: er });
    const nb = vehicle(egoX[i]! + dx(i), nbY(i), npsi, nv, { a: na, steer: Math.atan((2.7 * nr) / Math.max(nv, 1)), r: nr, signal });

    // 시나리오
    const win = SCENARIOS.find(s => inRange(t, s.start, s.end));
    const scenario: ActiveScenario | undefined = win && {
      kind: win.kind, parameters: firstParams(win.kind),
      start_tick: Math.round(win.start / DT), start_sample_time_s: win.start,
      elapsed_s: Math.round((t - win.start) * 10) / 10,
      direction_sign: win.kind === 'drift' || win.kind === 'weave' ? -1 : 1,
      lane_center_at_start_m: 3.5,
      steering_blocked: win.kind === 'drift' || win.kind === 'weave' || win.kind === 'stuck',
      auto_end_s: win.kind === 'brake' ? 2 : win.kind === 'accel' ? 3.5 : null,
    };
    const ended = SCENARIOS.filter(s => s.end <= t).pop();
    if (ended) lastReport = { kind: ended.kind, parameters: firstParams(ended.kind), start_sample_time_s: ended.start, ...ended.report };

    // 모델 (가짜): 준비 구간은 1.0 / 경보 없음
    const tauDet = 0.97 + 0.01 * Math.sin(t * 0.7) - 0.36 * bell(t, 22.8, 1.6) - 0.62 * bell(t, 38, 5) - 0.38 * bell(t, 76, 2.5);
    const tauEff = 0.97 + 0.01 * Math.sin(t * 0.7) - 0.14 * bell(t, 22.8, 1.6) - 0.55 * bell(t, 38.8, 4.6) - 0.18 * bell(t, 76, 2.5);
    const h = SAMPLE_DEMO_CONFIG.calibration.h;
    const ra = na - ea;
    const zone = dx(i) > 10 ? 'front' : dx(i) < -10 ? 'rear' : 'alongside';
    const model: ModelOpts = active ? {
      tau_det: tauDet, tau_eff: tauEff,
      tau_a2: Math.max(0, Math.min(1, tauDet - 0.08 + 0.06 * Math.sin(1.7 * t))),
      tau_rule: 1 - 0.15 * bell(t, 38, 4),
      alarm_det: anyIn(t, ALARM_DET), alarm_eff: anyIn(t, ALARM_EFF), alarm_a2: tauDet - 0.08 < 0.6,
      active: true, intent_window: anyIn(t, INTENT), zone,
      g: zone === 'front' ? ra < 0 : zone === 'rear' ? ra > 0 : true,
      e_long: 2.5 * bell(t, 22.8, 1.2) + 1.5 * bell(t, 50, 1.5),
      e_lat: 3.5 * bell(t, 36, 4) + 2 * bell(t, 76, 2.5),
      r_a: ra, R1_S: 1.2 * bell(t, 38, 4),
      ...(inRange(t, 29, 33) ? { R2_ttb_s: 33 - t } : {}),
      S: (h * Math.log(Math.min(tauDet, 1))) / Math.log(0.7),
      S_eff: (h * Math.log(Math.min(tauEff, 1))) / Math.log(0.7),
      hold_on: anyIn(t, HOLD_ON),
      d_hat: nbY(i) - 3.5,
    } : { tau_det: 1, tau_eff: 1, tau_a2: 1, active: false, intent_window: anyIn(t, INTENT) };

    // 자차 제어 (가짜)
    const avoiding = inRange(t, 34.9, 46), returning = inRange(t, 56, 59.6);
    const stage = inRange(t, 34.9, 37.9) ? 2 : inRange(t, 37.9, 44) ? 1 : 0;
    const closing = Math.max(0, -(nv - ev));
    const gap = Math.hypot(Math.max(0, Math.abs(dx(i)) - 4.5), Math.max(0, Math.abs(nbY(i) - egoY(i)) - 1.8));
    gapMin = Math.min(gapMin, gap);
    const cmdNext = { a_mps2: ea, steer_rad: Math.atan((2.7 * er) / ev) };
    const sameLaneAhead = dx(i) > 0 && Math.abs(nbY(i) - egoY(i)) < 1.8;

    const snapshot = makeSnapshot({
      generation, frame_seq: i + 2, sample_tick: i, phase: 'running',
      ego, neighbor: nb, model,
      ego_control: {
        state: avoiding ? 'AVOIDING' : returning ? 'RETURNING' : 'NORMAL',
        reacted: inRange(t, 34.9, 45.5), engaged: avoiding,
        ttc_s: sameLaneAhead && closing > 0.3 ? dx(i) / closing : null,
        gap_min_m: gapMin,
        command_next: cmdNext, command_applied: prevCmd,
      },
      policy: {
        stage, danger: stage > 0, closing_mps: closing,
        target_y_m: inRange(t, 34.9, 56) ? -3.5 : 0, policy_target_y_m: inRange(t, 34.9, 56) ? -3.5 : 0,
        v_ref_mps: inRange(t, 34.9, 38) ? 20.5 : 25, brake_mps2: stage === 2 ? Math.min(0, ea) : 0,
        ...(stage > 0 && closing > 0.3 ? { ttc_s: dx(i) / closing, ttc_reason: 'finite' as const } : {}),
      },
      ...(scenario ? { scenario } : {}),
      ...(lastReport ? { last_scenario_report: lastReport } : {}),
      next_weave: t >= 66 ? { amplitude_m: 1.2, period_s: 4.0 } : { amplitude_m: 0.8, period_s: 3.5 },
    });
    prevCmd = cmdNext;

    // 이벤트 (상태 변화 시점)
    const events: DemoEvent[] = [];
    const push = (kind: DemoEvent['kind'], msg: string) => events.push(event(generation, seq++, i, kind, `${msg} (샘플)`));
    const scen = win?.kind ?? null;
    if (prev.scen !== scen) {
      if (prev.scen) {
        const w = SCENARIOS.find(s => s.kind === prev.scen)!;
        push(w.how === 'finished' ? 'scenario.finished' : 'scenario.stopped', `${scenarioLabel(prev.scen)} ${w.how === 'finished' ? '자동 종료' : '해제'}`);
      }
      if (scen) push('scenario.started', `${scenarioLabel(scen)} 시작`);
    }
    if (prev.signal !== signal) push('signal.changed', signal === 'off' ? '이웃차 깜빡이 끔' : '이웃차 왼쪽 깜빡이 켬');
    const det = !!model.alarm_det, eff = !!model.alarm_eff;
    if (prev.det !== det) push(det ? 'alarm.det.started' : 'alarm.det.ended', det ? '탐지 경보 시작' : '탐지 경보 해제');
    if (prev.eff !== eff) push(eff ? 'alarm.eff.started' : 'alarm.eff.ended', eff ? '반응 경보 시작' : '반응 경보 해제');
    prev = { det, eff, signal, scen };

    frames.push({ snapshot, events });
  }
  return frames;
}
