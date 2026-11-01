/*
 * 신뢰도 그래프 (명세 7.2).
 * x축 = sample_time_s, 최근 60초 / 최대 history_samples(600) 고유 표본.
 * 곡선 tau_det(하늘)·tau_eff(주황)·tau_a2(회색), 참조선 0.7(경보 기준)·0.8(회복 참고), 아래 alarm_det/eff 구간 스트립.
 * 표본 중복 키는 (generation, sample_tick). 빠진 tick 은 선으로 잇지 않는다. 값은 서버 값 그대로 (경보는 alarm_* 사용).
 */
import type { ConfigView, GraphPointView, SnapshotView } from '../contract-access';
import { FONT_NUM, FONT_UI, PAPER } from './theme';
import { fitCanvas } from './camera';

export type ChartPoint = GraphPointView;

const WINDOW_S = 60;
const COLOR = PAPER;
// 곡선 3개 (명세 7.2). tau_a2 는 회색 점선으로 색 외의 구별도 준다
interface LineStyle { color: string; width: number; dash: number[] }
interface LegendItem extends LineStyle { text: string }
const CURVES: ({ key: 'tauDet' | 'tauEff' | 'tauA2'; name: string } & LineStyle)[] = [
  { key: 'tauDet', name: 'tau_det', color: COLOR.det, width: 2, dash: [] },
  { key: 'tauEff', name: 'tau_eff', color: COLOR.eff, width: 2, dash: [] },
  { key: 'tauA2', name: 'tau_a2', color: COLOR.a2, width: 1.6, dash: [1, 4] },
];

// 그래프 표본 저장소: generation 이 바뀌면 비운다
export function createChartHistory(maxSamples: number) {
  let generation: number | null = null;
  let points: ChartPoint[] = [];
  const seen = new Set<number>();

  return {
    add(v: SnapshotView): void {
      if (generation !== v.generation) {
        if (generation != null && v.generation < generation) return; // 과거 세대 무시
        generation = v.generation; points = []; seen.clear();
      }
      if (!v.model || v.sampleTime == null || seen.has(v.sampleTick)) return;
      seen.add(v.sampleTick);
      const m = v.model;
      points.push({
        sampleTick: v.sampleTick, sampleTime: v.sampleTime,
        tauDet: m.tauDet, tauEff: m.tauEff, tauA2: m.tauA2,
        alarmDet: m.alarmDet, alarmEff: m.alarmEff, active: m.active,
      });
      if (points[points.length - 2] && points[points.length - 2]!.sampleTick > v.sampleTick) points.sort((a, b) => a.sampleTick - b.sampleTick);
      while (points.length > maxSamples) seen.delete(points.shift()!.sampleTick);
    },
    // GET /history 로 받은 표본 합치기: (generation, sample_tick) 중복 제거, 오름차순, 최대 600
    addPoints(gen: number, pts: readonly ChartPoint[]): number {
      if (generation !== gen) {
        if (generation != null && gen < generation) return 0;
        generation = gen; points = []; seen.clear();
      }
      let added = 0;
      for (const p of pts) if (!seen.has(p.sampleTick)) { seen.add(p.sampleTick); points.push(p); added++; }
      points.sort((a, b) => a.sampleTick - b.sampleTick);
      while (points.length > maxSamples) seen.delete(points.shift()!.sampleTick);
      return added;
    },
    clear(): void { generation = null; points = []; seen.clear(); },
    // 표시 커서(generation, tick)까지의 표본
    upTo(gen: number, tick: number): ChartPoint[] {
      if (gen !== generation) return [];
      let n = points.length;
      while (n > 0 && points[n - 1]!.sampleTick > tick) n--;
      return points.slice(0, n);
    },
    get size() { return points.length; },
  };
}

export function createChartView(canvas: HTMLCanvasElement, cfg: ConfigView) {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('2D canvas 를 사용할 수 없습니다');

  function render(points: ChartPoint[], cursorTime: number | null): void {
    const c = ctx!;
    const { W, H } = fitCanvas(canvas, c);
    c.clearRect(0, 0, W, H);

    // 범례: 곡선 3개 + 참조선 2개. 한 줄에 안 들어가면 참조선을 둘째 줄로
    c.font = `12px ${FONT_UI}`;
    const title = `신뢰도 (최근 60초, sample_time_s) · 표본 `;
    const curveItems: LegendItem[] = CURVES.map(k => ({ text: k.name, color: k.color, dash: k.dash, width: k.width }));
    const refItems: LegendItem[] = [
      { text: `${cfg.tauAlarmRef} 경보 기준`, color: COLOR.alarmRef, dash: [], width: 1.2 },
      { text: `${cfg.tauRecoveryRef} 회복 참고`, color: COLOR.recoveryRef, dash: [5, 4], width: 1.2 },
    ];
    const itemW = (it: { text: string }) => 22 + c.measureText(it.text).width + 14;
    const sumW = (xs: { text: string }[]) => xs.reduce((a, it) => a + itemW(it), 0);
    c.font = `600 12px ${FONT_UI}`;
    const titleW = c.measureText(title + '600').width;
    c.font = `12px ${FONT_UI}`;
    const oneRow = 40 + titleW + 16 + sumW(curveItems) + sumW(refItems) <= W - 8;

    // 오른쪽 여백: 곡선 끝 이름표 자리
    c.font = `600 12px ${FONT_UI}`;
    const tagW = Math.max(...CURVES.map(k => c.measureText(k.name).width)) + 12;
    const L = 40, R = Math.ceil(tagW) + 4, T = oneRow ? 28 : 46, stripH = 9, stripGap = 4;
    const B = 18 + 2 * (stripH + stripGap) + 4;
    const pw = W - L - R, ph = H - T - B;
    if (pw < 40 || ph < 30) return;

    const tEnd = Math.max(WINDOW_S, cursorTime ?? 0);
    const tStart = tEnd - WINDOW_S;
    const xOf = (t: number) => L + ((t - tStart) / WINDOW_S) * pw;
    const yOf = (tau: number) => T + (1 - tau) * ph;
    const visible = points.filter(p => p.sampleTime >= tStart - 0.1);

    // 제목 + 범례 (선 모양 견본 포함: 색만으로 구별하지 않게)
    c.font = `600 12px ${FONT_UI}`;
    c.textBaseline = 'middle'; c.textAlign = 'left';
    c.fillStyle = PAPER.ink;
    c.fillText(`${title}${visible.length}`, L, 13);
    c.font = `12px ${FONT_UI}`;
    const drawItems = (items: LegendItem[], right: number, y: number) => {
      let x = right - sumW(items) + 14;
      for (const it of items) {
        c.strokeStyle = it.color; c.lineWidth = Math.max(2, it.width); c.setLineDash(it.dash); c.lineCap = 'round';
        c.beginPath(); c.moveTo(x, y); c.lineTo(x + 16, y); c.stroke();
        c.setLineDash([]); c.lineCap = 'butt';
        c.fillStyle = PAPER.ink2; c.textAlign = 'left'; c.fillText(it.text, x + 22, y);
        x += itemW(it);
      }
    };
    if (oneRow) drawItems([...curveItems, ...refItems], W - 8, 13);
    else { drawItems(curveItems, W - 8, 13); drawItems(refItems, W - 8, 31); }

    // 준비 구간 음영 (active=false 표본)
    c.fillStyle = PAPER.prep;
    for (const p of visible) if (!p.active) c.fillRect(xOf(p.sampleTime), T, Math.max(1, xOf(p.sampleTime + 0.1) - xOf(p.sampleTime)), ph);

    // y 격자 0~1, x 격자 10초
    c.strokeStyle = PAPER.grid; c.lineWidth = 1;
    c.fillStyle = PAPER.ink2; c.font = `500 12px ${FONT_NUM}`;
    c.textAlign = 'right';
    // 높이가 모자라면 눈금 수를 줄여 라벨이 겹치지 않게 (라벨 간격 16px 이상)
    const MIN_LABEL_GAP = 16;
    const step = ph * 0.2 >= MIN_LABEL_GAP ? 0.2 : ph * 0.5 >= MIN_LABEL_GAP ? 0.5 : 1;
    for (let v = 0; v <= 1 + 1e-9; v += step) {
      const y = yOf(v);
      c.beginPath(); c.moveTo(L, y); c.lineTo(L + pw, y); c.stroke();
      c.fillText(v.toFixed(1), L - 6, y);
    }
    c.textAlign = 'center'; c.textBaseline = 'top';
    for (let t = Math.ceil(tStart / 10) * 10; t <= tEnd + 1e-6; t += 10) {
      const x = xOf(t);
      c.beginPath(); c.moveTo(x, T); c.lineTo(x, T + ph); c.stroke();
      c.fillText(`${t}s`, x, T + ph + 3);
    }

    // 참조선
    c.strokeStyle = COLOR.alarmRef; c.lineWidth = 1.2;
    c.beginPath(); c.moveTo(L, yOf(cfg.tauAlarmRef)); c.lineTo(L + pw, yOf(cfg.tauAlarmRef)); c.stroke();
    c.setLineDash([5, 4]);
    c.strokeStyle = COLOR.recoveryRef;
    c.beginPath(); c.moveTo(L, yOf(cfg.tauRecoveryRef)); c.lineTo(L + pw, yOf(cfg.tauRecoveryRef)); c.stroke();
    c.setLineDash([]);
    // 참조선 설명은 위 범례에 둔다 (곡선과 겹치지 않게)

    // 곡선: tick 이 이어지지 않으면 끊는다
    c.save();
    c.beginPath(); c.rect(L, T - 2, pw, ph + 4); c.clip();
    for (const { key, color, width, dash } of [...CURVES].reverse()) {
      c.strokeStyle = color; c.lineWidth = width; c.setLineDash(dash); c.lineCap = dash.length ? 'round' : 'butt';
      c.beginPath();
      let prevTick: number | null = null;
      for (const p of visible) {
        const x = xOf(p.sampleTime), y = yOf(p[key]);
        if (prevTick != null && p.sampleTick === prevTick + 1) c.lineTo(x, y); else c.moveTo(x, y);
        prevTick = p.sampleTick;
      }
      c.stroke();
    }
    c.setLineDash([]); c.lineCap = 'butt';
    c.restore();

    // 곡선 끝 이름표: 마지막 표본 오른쪽, 겹치면 위아래로 벌린다
    const last = visible[visible.length - 1];
    if (last) {
      const lxEnd = Math.min(L + pw, xOf(last.sampleTime)) + 6;
      const tags = CURVES.map(k => ({ ...k, y: yOf(Math.max(0, Math.min(1, last[k.key]))) })).sort((a, b) => a.y - b.y);
      const GAP = 15;
      for (let i = 1; i < tags.length; i++) tags[i]!.y = Math.max(tags[i]!.y, tags[i - 1]!.y + GAP);
      const over = tags[tags.length - 1]!.y - (T + ph);
      if (over > 0) for (const t of tags) t.y -= over;
      for (let i = tags.length - 2; i >= 0; i--) tags[i]!.y = Math.min(tags[i]!.y, tags[i + 1]!.y - GAP);
      c.font = `600 12px ${FONT_UI}`; c.textAlign = 'left'; c.textBaseline = 'middle';
      c.lineJoin = 'round'; c.lineWidth = 4; c.strokeStyle = PAPER.sheet;
      for (const t of tags) { c.strokeText(t.name, lxEnd, t.y); c.fillStyle = t.color === COLOR.a2 ? PAPER.ink2 : t.color; c.fillText(t.name, lxEnd, t.y); }
    }

    // 경보 구간 스트립 (서버 alarm_det / alarm_eff 그대로)
    const s1 = T + ph + 17, s2 = s1 + stripH + stripGap;
    c.fillStyle = PAPER.strip;
    c.fillRect(L, s1, pw, stripH); c.fillRect(L, s2, pw, stripH);
    for (const p of visible) {
      const x0 = Math.max(L, xOf(p.sampleTime)), x1 = Math.min(L + pw, xOf(p.sampleTime + 0.1));
      if (x1 <= x0) continue;
      if (p.alarmDet) { c.fillStyle = COLOR.det; c.fillRect(x0, s1, x1 - x0 + 0.5, stripH); }
      if (p.alarmEff) { c.fillStyle = COLOR.eff; c.fillRect(x0, s2, x1 - x0 + 0.5, stripH); }
    }
    c.fillStyle = PAPER.ink2; c.textAlign = 'right'; c.textBaseline = 'middle'; c.font = `12px ${FONT_UI}`;
    c.fillText('탐지', L - 6, s1 + stripH / 2);
    c.fillText('반응', L - 6, s2 + stripH / 2);

    // 현재 커서
    if (cursorTime != null) {
      const x = xOf(cursorTime);
      c.strokeStyle = 'rgba(26,38,50,.55)'; c.lineWidth = 1;
      c.beginPath(); c.moveTo(x, T); c.lineTo(x, s2 + stripH); c.stroke();
    }

    if (!visible.length) {
      c.fillStyle = PAPER.ink2; c.textAlign = 'center'; c.font = `12px ${FONT_UI}`;
      c.fillText('표본 없음 — 그래프에는 가짜 표본을 넣지 않음', L + pw / 2, T + ph / 2);
    }
  }

  return { render };
}
