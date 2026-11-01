// 계기판 부품 (SVG / HTML 문자열). 값은 받은 그대로 그리고, 범위 밖이면 바늘만 끝에 멈춘다 (숫자는 실제 값)
import { PAPER } from './theme';

export interface ArcTick { at: number; color: string; label?: string }

export interface ArcGaugeOpts {
  value: number | null;
  min: number;
  max: number;
  color: string;
  center: string;          // 가운데 큰 글자
  ticks?: ArcTick[];       // 눈금 (참조선 등)
  marker?: { at: number; color: string } | null; // 바깥 삼각 표식 (예: v_ref)
  alarm?: boolean;         // 서버 경보 켜짐: 바깥 링 표시 (값은 서버 alarm_* 그대로)
}

const CX = 60, CY = 58, R = 46;
const A0 = (135 * Math.PI) / 180;   // 왼쪽 아래에서 시작
const SWEEP = (270 * Math.PI) / 180; // 시계 방향 270°

const xy = (a: number, r = R) => ({ x: (CX + r * Math.cos(a)).toFixed(2), y: (CY + r * Math.sin(a)).toFixed(2) });
const pt = (a: number, r = R) => { const p = xy(a, r); return `${p.x} ${p.y}`; };

function arcPath(a0: number, a1: number, r = R): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${pt(a0, r)} A ${r} ${r} 0 ${large} 1 ${pt(a1, r)}`;
}

export function arcGauge(o: ArcGaugeOpts): string {
  const frac = (v: number) => Math.max(0, Math.min(1, (v - o.min) / (o.max - o.min)));
  const angle = (v: number) => A0 + frac(v) * SWEEP;
  const parts: string[] = [];
  parts.push(`<path d="${arcPath(A0, A0 + SWEEP)}" stroke="${PAPER.track}" stroke-width="10" fill="none" stroke-linecap="round"/>`);
  if (o.value != null && frac(o.value) > 0.001) {
    parts.push(`<path d="${arcPath(A0, angle(o.value))}" stroke="${o.color}" stroke-width="10" fill="none" stroke-linecap="round"/>`);
  }
  for (const t of o.ticks ?? []) {
    const a = angle(t.at);
    const p1 = xy(a, R - 8), p2 = xy(a, R + 7);
    parts.push(`<line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="${t.color}" stroke-width="2"/>`);
    if (t.label) {
      const p = xy(a, R - 17);
      parts.push(`<text x="${p.x}" y="${p.y}" fill="${t.color}" font-size="8.5" font-weight="600" class="num" text-anchor="middle" dominant-baseline="middle">${t.label}</text>`);
    }
  }
  if (o.marker) {
    const a = angle(o.marker.at);
    const tip = pt(a, R + 6), l = pt(a - 0.07, R + 13), r = pt(a + 0.07, R + 13);
    parts.push(`<path d="M ${tip} L ${l} L ${r} Z" fill="${o.marker.color}"/>`);
  }
  if (o.value != null) {
    const n = xy(angle(o.value), R - 4);
    parts.push(`<line x1="${CX}" y1="${CY}" x2="${n.x}" y2="${n.y}" stroke="${PAPER.ink}" stroke-width="2.2" stroke-linecap="round"/>`);
    parts.push(`<circle cx="${CX}" cy="${CY}" r="3.5" fill="${PAPER.ink}"/>`);
  }
  // 숫자·보조 문구는 원호 아래 빈 구간에 둔다 (바늘과 겹치지 않게)
  parts.push(`<text x="${CX}" y="${CY + 40}" fill="${PAPER.ink}" font-size="24" font-weight="600" class="num" text-anchor="middle">${o.center}</text>`);
  if (o.alarm) parts.unshift(`<path d="${arcPath(A0, A0 + SWEEP, R + 9)}" stroke="${o.color}" stroke-width="2.5" fill="none" stroke-linecap="round"/>`);
  return `<svg class="arc${o.alarm ? ' alarm' : ''}" viewBox="0 0 120 104" role="img">${parts.join('')}</svg>`;
}

// 세로 막대 계기 (0~max, 임계선 표시)
export function barGauge(o: { label: string; value: number | null; max: number; threshold: number; color: string }): string {
  const h = o.value == null ? 0 : (Math.max(0, Math.min(o.max, o.value)) / o.max) * 100;
  const th = (o.threshold / o.max) * 100;
  return `<div class="vbar">
    <div class="vtrack"><i style="height:${h}%;background:${o.color}"></i><b style="bottom:${th}%"></b><span style="bottom:${th}%">${o.threshold.toFixed(1)}</span></div>
    <div class="vval" style="color:${o.color}">${o.value == null ? '—' : o.value.toFixed(2)}</div>
    <div class="vlbl">${o.label}</div>
  </div>`;
}

// 경보 등
export function lamp(label: string, on: boolean, kind: string, detail = ''): string {
  return `<div class="lamp ${kind}${on ? ' on' : ''}"><i></i><span>${label}</span>${detail ? `<em>${detail}</em>` : ''}</div>`;
}
