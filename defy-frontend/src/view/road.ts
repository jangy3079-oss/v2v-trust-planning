// 왼쪽 도로·차량 렌더링 (Canvas 2D)
import type { ConfigView, ModelView, SnapshotView, VehicleView } from '../contract-access';
import { FONT_UI } from './theme';
import { fitCanvas, makeCamera, type Camera } from './camera';
import { fmt, fmtSigned, kmh, LABEL } from './format';
import type { Road } from '../road-geometry';
import { makeRoadFrame, type RoadFrame } from './road-frame';

export interface RoadScene {
  ego: VehicleView;   // 표시 시각의 자차 (2단계부터 보간값)
  nb: VehicleView;    // 표시 시각의 이웃차
  simTime: number;    // 표시 시각 (깜빡이 위상 기준)
  view: SnapshotView; // 최신 표본 (경보·HUD 값은 보간하지 않음)
  showHud: boolean;   // 속도·v_ref·간격·dx 표 (기본 숨김, 토글). 차량 옆 km/h 라벨은 항상
  avoid: ScreenRect[]; // 캔버스 위에 떠 있는 DOM(왼쪽 아래 도크 등) 영역. 라벨·화면 밖 표식이 피해 간다
  road: Road;          // 굽은 도로 (계약 외). 차량 x/y 는 도로 기준 (s, 횡위치) 로 보고 그 위에 얹어 그린다
}

export interface ScreenRect { x: number; y: number; w: number; h: number }
const hitsAny = (rs: readonly ScreenRect[], x: number, y: number, w: number, h: number): ScreenRect | null =>
  rs.find(r => x < r.x + r.w && r.x < x + w && y < r.y + r.h && r.y < y + h) ?? null;

const BLINK_PERIOD_S = 0.3; // 명세 3.1: 300ms 켬 / 300ms 끔
const DASH_PERIOD_M = 12;
const DASH_LEN_M = 4;
const ROAD_STEP_M = 2;        // 경계선 꺾은선 간격
const ROAD_MARGIN_M = 20;     // 화면 위·아래 밖으로 더 그리는 길이 (곡선에서 옆으로 들어오는 부분)
const TURN_PREVIEW_M = 250;   // 이만큼 앞의 회전부터 안내

const COLOR = { ego: '#3b82f6', nb: '#facc15', det: '#38bdf8', eff: '#fb923c', signal: '#fbbf24', hit: '#ef4444' };

interface WorldPt { x: number; y: number }
interface ScreenPt { X: number; Y: number }
type Quad<T> = [T, T, T, T];

// 월드에서 psi 로 회전한 꼭짓점: [앞-왼, 앞-오, 뒤-오, 뒤-왼]
function vehicleCorners(v: VehicleView): Quad<WorldPt> {
  const hl = v.len / 2, hw = v.wid / 2;
  return [localPoint(v, hl, hw), localPoint(v, hl, -hw), localPoint(v, -hl, -hw), localPoint(v, -hl, hw)];
}
function localPoint(v: VehicleView, lx: number, ly: number): WorldPt {
  const c = Math.cos(v.psi), s = Math.sin(v.psi);
  return { x: v.x + lx * c - ly * s, y: v.y + lx * s + ly * c };
}
const project = (cam: Camera, p: WorldPt): ScreenPt => ({ X: cam.sx(p.y), Y: cam.sy(p.x) });

function tracePolygon(ctx: CanvasRenderingContext2D, pts: ScreenPt[]): void {
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.X, p.Y) : ctx.moveTo(p.X, p.Y)));
  ctx.closePath();
}

interface OffscreenInfo { X: number; Y: number; front: boolean; rear: boolean; left: boolean; right: boolean }

// 차량의 투영 꼭짓점이 모두 화면 밖이면 화면 밖으로 본다 (위치는 clamp 하지 않음)
function offscreenInfo(cam: Camera, v: VehicleView): OffscreenInfo | null {
  const pts = vehicleCorners(v).map(p => project(cam, p));
  const minX = Math.min(...pts.map(p => p.X)), maxX = Math.max(...pts.map(p => p.X));
  const minY = Math.min(...pts.map(p => p.Y)), maxY = Math.max(...pts.map(p => p.Y));
  if (maxX >= 0 && minX <= cam.W && maxY >= 0 && minY <= cam.H) return null;
  return { X: cam.sx(v.y), Y: cam.sy(v.x), front: maxY < 0, rear: minY > cam.H, left: maxX < 0, right: minX > cam.W };
}

export function createRoadView(canvas: HTMLCanvasElement, cfg: ConfigView) {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2D canvas 를 사용할 수 없습니다');
  let avoid: ScreenRect[] = [];

  function drawRoadSurface(cam: Camera, frame: RoadFrame): void {
    const { W, H } = cam;
    const c = ctx!;
    c.fillStyle = '#1F2427'; // 갓길
    c.fillRect(0, 0, W, H);

    const sMin = cam.xAt(H) - ROAD_MARGIN_M, sMax = cam.xAt(0) + ROAD_MARGIN_M;
    const edge = (d: number): ScreenPt[] => {
      const pts: ScreenPt[] = [];
      for (let s = sMin; s < sMax + ROAD_STEP_M; s += ROAD_STEP_M) pts.push(project(cam, frame.pt(s, d)));
      return pts;
    };
    const strokeLine = (pts: ScreenPt[]) => {
      c.beginPath();
      pts.forEach((p, i) => (i ? c.lineTo(p.X, p.Y) : c.moveTo(p.X, p.Y)));
      c.stroke();
    };
    const [rightY, leftY] = cfg.outerEdges;
    const leftEdge = edge(leftY), rightEdge = edge(rightY);
    tracePolygon(c, [...leftEdge, ...rightEdge.reverse()]);
    c.fillStyle = '#33383E'; // 아스팔트
    c.fill();
    rightEdge.reverse();

    // 바깥 경계 실선
    c.strokeStyle = '#e5e7eb';
    c.lineWidth = 2;
    strokeLine(leftEdge);
    strokeLine(rightEdge);

    // 내부 경계 점선: 도로 s 에 고정 → 자차가 전진하면 아래로 흘러간다
    const k0 = Math.floor(sMin / DASH_PERIOD_M) * DASH_PERIOD_M;
    c.strokeStyle = '#d1d5db';
    for (const y of cfg.innerEdges) {
      c.beginPath();
      for (let s = k0; s < sMax; s += DASH_PERIOD_M) {
        const a = project(cam, frame.pt(s, y)), b = project(cam, frame.pt(s + DASH_LEN_M, y));
        c.moveTo(a.X, a.Y);
        c.lineTo(b.X, b.Y);
      }
      c.stroke();
    }

    // 차로 ID (원본 규칙: 0=오른쪽, 1=중앙, 2=왼쪽). 화면 위쪽 차로 위치에. 범례는 HUD 에 표시
    c.font = `bold 13px ${FONT_UI}`;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    c.fillStyle = 'rgba(229,231,235,.6)';
    for (const l of cfg.lanes) {
      const p = project(cam, frame.pt(cam.xAt(25), l.centerY));
      c.fillText(String(l.id), p.X, p.Y);
    }
    const legend = '차로 ID ' + [...cfg.lanes].sort((a, b) => a.id - b.id).map(l => `${l.id}=${LABEL.laneSide[l.id] ?? ''}`).join(' · ');
    c.font = `12px ${FONT_UI}`;
    c.textAlign = 'right';
    c.textBaseline = 'top';
    const lw = c.measureText(legend).width, lx = cam.sx(leftY) - 8;
    c.fillStyle = 'rgba(31,36,39,.85)'; // 곡선에서 차로 ID 가 밑으로 지나가도 읽히게
    c.fillRect(lx - lw - 4, 16, lw + 8, 18);
    c.fillStyle = 'rgba(229,231,235,.6)';
    c.fillText(legend, lx, 19);

    // 자차 기준 거리 눈금 (−10 ~ +50m, 도로를 따라 잰 거리) — 세로 −12~+55m 척도 확인용
    c.font = `12px ${FONT_UI}`;
    c.textAlign = 'left'; c.textBaseline = 'middle';
    for (let d = -10; d <= 50; d += 10) {
      const p = project(cam, frame.pt(cam.egoX + d, rightY));
      if (p.Y < 0 || p.Y > H) continue;
      c.strokeStyle = d === 0 ? '#93c5fd' : 'rgba(229,231,235,.45)';
      c.lineWidth = 1;
      c.beginPath(); c.moveTo(p.X + 3, p.Y); c.lineTo(p.X + 11, p.Y); c.stroke();
      c.fillStyle = d === 0 ? '#93c5fd' : 'rgba(229,231,235,.6)';
      c.fillText(`${d > 0 ? '+' : ''}${d} m`, p.X + 14, p.Y);
    }
  }

  // 도로 안내 (계약 외): 지금 회전 중이면 방향·반경, 아니면 다가오는 회전까지 거리
  function drawRoadInfo(cam: Camera, road: Road): void {
    const t = road.turnNear(cam.egoX, TURN_PREVIEW_M);
    if (!t && !road.turnNear(cam.egoX, Infinity)) return; // 앞에 회전이 없는 도로 (직선 도로 포함)
    const side = t?.turn === 'left' ? '좌' : '우';
    const arrow = t?.turn === 'left' ? '↰' : '↱';
    const text = !t ? '↑ 직진 구간'
      : t.start_s_m <= cam.egoX ? `${arrow} ${side}회전 중 · 반경 ${Math.round(t.radius_m)} m · ${Math.round(t.angle_deg)}°`
      : `${arrow} ${Math.round(t.start_s_m - cam.egoX)} m 앞 ${side}회전 (${Math.round(t.angle_deg)}°)`;
    const c = ctx!;
    c.font = `600 13px ${FONT_UI}`;
    c.textAlign = 'center'; c.textBaseline = 'middle';
    const w = c.measureText(text).width + 16, x = cam.W / 2, y = 50;
    c.fillStyle = 'rgba(0,0,0,.65)';
    c.fillRect(x - w / 2, y - 11, w, 22);
    c.fillStyle = t ? '#fde68a' : '#cbd5e1';
    c.fillText(text, x, y);
  }

  // 하단 문구: 왼쪽 = 고정 문구(명세 10.3), 오른쪽 = 척도. 경보 테두리 안쪽에 둔다
  function drawFooter(cam: Camera): void {
    const c = ctx!;
    const scaleText = `척도 ${cam.s.toFixed(1)} px/m (종·횡 동일) · 세로 ${fmtSigned(cam.xAt(cam.H) - cam.egoX, 0)} ~ ${fmtSigned(cam.xAt(0) - cam.egoX, 0)} m`;
    c.font = `12px ${FONT_UI}`;
    c.textBaseline = 'middle';
    const y = cam.H - 28;
    const label = (text: string, x: number, yy: number) => {
      const w = c.measureText(text).width;
      c.fillStyle = 'rgba(0,0,0,.65)'; c.fillRect(x - 6, yy - 10, w + 12, 20);
      c.fillStyle = '#cbd5e1'; c.textAlign = 'left'; c.fillText(text, x, yy);
    };
    const sw = c.measureText(scaleText).width, bw = c.measureText(cfg.banner).width;
    if (sw + bw + 60 <= cam.W) {
      label(cfg.banner, 24, y);
      label(scaleText, cam.W - 24 - sw, y);
    } else { // 좁으면 두 줄
      label(scaleText, 24, y - 20);
      label(cfg.banner, 24, y);
    }
  }

  // NMPC 예측 궤적: 계획 정보일 뿐 실제 자차 경로가 아니다 (명세 10.3). 점선 + 라벨로 구별
  function drawNmpcPlan(cam: Camera, frame: RoadFrame, view: SnapshotView): void {
    const plan = (view.ec.nmpc?.predicted ?? []).map(q => frame.pt(q.x, q.y));
    if (view.ec.solver !== 'nmpc' || plan.length < 2) return;
    const c = ctx!;
    c.save();
    c.setLineDash([4, 4]);
    c.strokeStyle = '#c084fc';
    c.lineWidth = 2;
    c.beginPath();
    plan.forEach((q, i) => (i ? c.lineTo(cam.sx(q.y), cam.sy(q.x)) : c.moveTo(cam.sx(q.y), cam.sy(q.x))));
    c.stroke();
    c.restore();
    const last = plan[plan.length - 1]!;
    c.font = `12px ${FONT_UI}`;
    c.fillStyle = '#c084fc';
    c.textAlign = 'left'; c.textBaseline = 'bottom';
    c.fillText('NMPC 계획 (실제 경로 아님)', cam.sx(last.y) + 6, cam.sy(last.x));
  }

  function drawVehicle(cam: Camera, v: VehicleView, color: string, label: string, blinkOn: boolean, hit = false): void {
    const c = ctx!;
    const pts = vehicleCorners(v).map(p => project(cam, p)) as Quad<ScreenPt>;
    tracePolygon(c, pts);
    c.fillStyle = color;
    c.fill();
    c.lineWidth = 1.5;
    c.strokeStyle = 'rgba(0,0,0,.6)';
    c.stroke();

    // 앞유리 (방향 표시)
    tracePolygon(c, [
      localPoint(v, v.len * 0.18, v.wid * 0.38), localPoint(v, v.len * 0.18, -v.wid * 0.38),
      localPoint(v, v.len * 0.05, -v.wid * 0.34), localPoint(v, v.len * 0.05, v.wid * 0.34),
    ].map(p => project(cam, p)));
    c.fillStyle = 'rgba(15,23,42,.75)';
    c.fill();

    // 깜빡이: 켠 방향의 전·후 모서리 (signal_side 가 원본)
    if (v.signal !== 'off' && blinkOn) {
      const lamps = v.signal === 'left' ? [pts[0], pts[3]] : [pts[1], pts[2]];
      c.fillStyle = COLOR.signal;
      c.shadowColor = COLOR.signal;
      c.shadowBlur = 10;
      for (const p of lamps) {
        c.beginPath();
        c.arc(p.X, p.Y, Math.max(3, cam.s * 0.3), 0, Math.PI * 2);
        c.fill();
      }
      c.shadowBlur = 0;
    }

    // 충돌 표시 (자동 리셋·정지 없음)
    if (hit) {
      const X = cam.sx(v.y), Y = cam.sy(v.x);
      c.strokeStyle = COLOR.hit;
      c.lineWidth = 3;
      c.beginPath(); c.arc(X, Y, cam.s * 3.4, 0, Math.PI * 2); c.stroke();
      c.fillStyle = COLOR.hit;
      c.font = `bold 12px ${FONT_UI}`;
      c.textAlign = 'center'; c.textBaseline = 'bottom';
      c.fillText('충돌', X, Y - cam.s * 3.6);
    }

    // 이름·속도 라벨: 차량 왼쪽 → (도크와 겹치면) 오른쪽 → (거리 눈금과도 겹치면) 차량 아래
    let ly = cam.sy(v.x);
    c.font = `600 14px ${FONT_UI}`;
    c.textBaseline = 'middle';
    const text = `${label} ${kmh(v.v)} km/h`;
    const tw = c.measureText(text).width;
    const ticks: ScreenRect = { x: cam.sx(cfg.outerEdges[0]) + 2, y: 0, w: 70, h: cam.H }; // drawRoadSurface 의 거리 눈금 열
    const blocked = (x: number, y: number) => hitsAny([...avoid, ticks], x - 4, y - 11, tw + 8, 22);
    let x0 = cam.sx(v.y) - cam.s * 1.6 - tw;
    if (blocked(x0, ly)) x0 = cam.sx(v.y) + cam.s * 1.6;
    if (blocked(x0, ly)) { x0 = cam.sx(v.y) - tw / 2; ly = cam.sy(v.x) + (v.len / 2) * cam.s + 16; }
    c.fillStyle = 'rgba(0,0,0,.6)';
    c.fillRect(x0 - 4, ly - 11, tw + 8, 22);
    c.fillStyle = color;
    c.textAlign = 'left';
    c.fillText(text, x0, ly);
  }

  // 화면 밖 표식: 가장자리에 방향 삼각형 + 상대 거리 (dx/dy 는 서버 ego_control 값)
  function drawOffscreenMarker(cam: Camera, info: OffscreenInfo, color: string, name: string, dx: number, dy: number): void {
    const c = ctx!;
    // 표식 위치 범위: 위는 차로 번호 아래, 아래는 하단 문구 위
    const m = 26, top = 44, bottom = 56;
    const X = Math.min(cam.W - m, Math.max(m, info.X));
    let Y = Math.min(cam.H - bottom, Math.max(top, info.Y));
    const under = hitsAny(avoid, X - 14, Y - 14, 28, 28);
    if (under) Y = Math.max(top, under.y - 18); // 도크에 가리면 도크 위로
    c.save();
    c.translate(X, Y);
    c.rotate(Math.atan2(info.Y - Y, info.X - X));
    c.fillStyle = color;
    c.strokeStyle = 'rgba(0,0,0,.7)';
    c.lineWidth = 1.5;
    c.beginPath(); c.moveTo(14, 0); c.lineTo(-8, -10); c.lineTo(-8, 10); c.closePath();
    c.fill(); c.stroke();
    c.restore();

    const dir = [info.front && '앞', info.rear && '뒤', info.left && '왼쪽', info.right && '오른쪽'].filter(Boolean).join('·');
    const lines = [`${name} ${dir} (화면 밖)`, `dx ${fmtSigned(dx)} m`];
    if (info.left || info.right) lines.push(`dy ${fmtSigned(dy)} m`);
    c.font = `bold 14px ${FONT_UI}`;
    const tw = Math.max(...lines.map(t => c.measureText(t).width));
    const bh = lines.length * 18;
    let bx = X - tw / 2, by = info.front ? Y + 18 : Y - 18 - bh;
    if (info.left) { bx = X + 18; by = Y - bh / 2; }
    if (info.right) { bx = X - 18 - tw; by = Y - bh / 2; }
    bx = Math.min(cam.W - tw - 6, Math.max(6, bx));
    by = Math.min(cam.H - bh - 6, Math.max(6, by));
    const box = hitsAny(avoid, bx - 5, by - 3, tw + 10, bh + 6);
    if (box) bx = Math.min(cam.W - tw - 6, box.x + box.w + 10); // 설명 상자는 도크 오른쪽으로
    c.fillStyle = 'rgba(0,0,0,.72)';
    c.fillRect(bx - 5, by - 3, tw + 10, bh + 6);
    c.fillStyle = color;
    c.textAlign = 'left'; c.textBaseline = 'top';
    lines.forEach((t, i) => c.fillText(t, bx, by + i * 18));
  }

  // HUD 표 (토글로 켤 때만). v_ref·간격은 패널의 자차 반응 요약에도 항상 표시된다 (명세 1.2)
  function drawHud(view: SnapshotView, ego: VehicleView): void {
    const c = ctx!;
    const ec = view.ec;
    const rows: [string, string, string][] = [
      ['자차 속도', `${kmh(ego.v)} km/h`, '#93c5fd'],
      ['이웃 속도', `${kmh(view.nb.v)} km/h`, '#fde68a'],
      ['v_ref', `${kmh(ec.vRef)} km/h`, '#cbd5e1'],
      ['차체 간격', `${fmt(ec.gap, 1)} m`, '#cbd5e1'],
      ['dx', `${fmtSigned(ec.dx)} m`, '#cbd5e1'],
    ];
    const pad = 8, lh = 21, w = 176, left = 12, top = 64; // 토글 버튼 아래
    c.fillStyle = 'rgba(0,0,0,.6)';
    c.fillRect(left, top, w, rows.length * lh + pad);
    c.textBaseline = 'top';
    rows.forEach(([k, v, col], i) => {
      c.font = `12px ${FONT_UI}`;
      c.textAlign = 'left'; c.fillStyle = '#9aa4b2'; c.fillText(k, left + pad, top + 7 + i * lh);
      c.font = `600 14px ${FONT_UI}`;
      c.textAlign = 'right'; c.fillStyle = col; c.fillText(v, left + w - pad, top + 6 + i * lh);
    });
  }

  // 경보 테두리: 탐지=하늘색, 자차 반응=주황색. 서버 alarm_det / alarm_eff 그대로
  function drawAlarmBorders(cam: Camera, model: ModelView | null): void {
    if (!model) return;
    const c = ctx!;
    const tags: [string, string][] = [];
    c.lineWidth = 6;
    if (model.alarmDet) {
      c.strokeStyle = COLOR.det;
      c.strokeRect(3, 3, cam.W - 6, cam.H - 6);
      tags.push(['탐지 경보', COLOR.det]);
    }
    if (model.alarmEff) {
      c.strokeStyle = COLOR.eff;
      c.strokeRect(10, 10, cam.W - 20, cam.H - 20);
      tags.push(['반응 경보', COLOR.eff]);
    }
    c.font = `bold 12px ${FONT_UI}`;
    c.textAlign = 'right'; c.textBaseline = 'top';
    tags.forEach(([t, col], i) => {
      const w = c.measureText(t).width + 12;
      c.fillStyle = col; c.fillRect(cam.W - 18 - w, 24 + i * 22, w, 18);
      c.fillStyle = '#0b0e12'; c.fillText(t, cam.W - 24, 27 + i * 22);
    });
  }

  function render(scene: RoadScene): void {
    avoid = scene.avoid;
    const { W, H } = fitCanvas(canvas, ctx!);
    const cam = makeCamera(W, H, scene.ego.x);
    const frame = makeRoadFrame(scene.road, scene.ego.x);
    const ego = frame.vehicle(scene.ego), nb = frame.vehicle(scene.nb);
    drawRoadSurface(cam, frame);

    // 명세 3.1: floor(sim_time_s/0.3)%2==0 이면 켬 (부동소수 경계 보호용 1e-9)
    const blinkOn = Math.floor(scene.simTime / BLINK_PERIOD_S + 1e-9) % 2 === 0;
    const ec = scene.view.ec;

    drawNmpcPlan(cam, frame, scene.view);
    const nbOff = offscreenInfo(cam, nb);
    if (!nbOff) drawVehicle(cam, nb, COLOR.nb, '이웃', blinkOn);
    const egoOff = offscreenInfo(cam, ego);
    if (!egoOff) drawVehicle(cam, ego, COLOR.ego, '자차', blinkOn, ec.hit);

    drawRoadInfo(cam, scene.road);
    drawAlarmBorders(cam, scene.view.model);
    if (scene.showHud) drawHud(scene.view, scene.ego);
    drawFooter(cam);
    if (nbOff) drawOffscreenMarker(cam, nbOff, COLOR.nb, '이웃차', ec.dx, ec.dy);
    if (egoOff) drawOffscreenMarker(cam, egoOff, COLOR.ego, '자차', 0, scene.ego.y);
  }

  return { render };
}
