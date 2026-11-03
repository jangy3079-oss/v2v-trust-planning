/*
 * 굽은 도로 (명세 5.2, v1.7). 도로 모양의 원본은 서버이고, 화면은 서버가 보낸 곡률 구간으로 중심선을 적분해 그린다.
 *
 * 좌표 해석: 계약의 x_m = 도로 중심선을 따라간 거리 s, y_m = 중심선 기준 횡위치 (왼쪽 +), psi_rad = 도로 접선 기준 방향각.
 * 차로 중심(−3.5/0/3.5)·경계는 계약 그대로다. 곡률 구간이 없거나 끝난 뒤는 직선으로 본다.
 */
import type { RoadResponse, RoadTurnInfo } from './contracts';

export interface RoadPose { x: number; y: number; theta: number }

export interface Road {
  readonly coveredUntil: number;
  curvature(s: number): number;
  pose(s: number): RoadPose;                 // 중심선 위 월드 위치·접선 방향
  toWorld(s: number, d: number): { x: number; y: number };
  turnNear(s: number, aheadM: number): RoadTurnInfo | null; // 지금 지나는 회전, 없으면 aheadM 안에서 다가오는 회전
}

const SAMPLE_M = 1; // 자세 표본 간격

export const STRAIGHT_ROAD: Road = {
  coveredUntil: Infinity,
  curvature: () => 0,
  pose: s => ({ x: s, y: 0, theta: 0 }),
  toWorld: (s, d) => ({ x: s, y: d }),
  turnNear: () => null,
};

export function createRoad(r: Pick<RoadResponse, 'pieces' | 'turns' | 'covered_until_m'>): Road {
  const pieces = [...r.pieces].sort((a, b) => a.s0_m - b.s0_m);
  const turns = [...r.turns].sort((a, b) => a.start_s_m - b.start_s_m);

  function curvature(s: number): number {
    let lo = 0, hi = pieces.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1, p = pieces[mid]!;
      if (s < p.s0_m) hi = mid - 1;
      else if (s >= p.s1_m) lo = mid + 1;
      else return p.k0_1pm + (p.k1_1pm - p.k0_1pm) * ((s - p.s0_m) / (p.s1_m - p.s0_m));
    }
    return 0;
  }

  // 1m 간격 자세 표본을 필요한 만큼만 적분해 둔다 (0.25m 소단계)
  const xs: number[] = [0], ys: number[] = [0], ths: number[] = [0];
  function ensureSamples(s: number): void {
    const need = Math.ceil(s / SAMPLE_M) + 1;
    while (xs.length <= need) {
      const i = xs.length - 1;
      let x = xs[i]!, y = ys[i]!, th = ths[i]!, sc = i * SAMPLE_M;
      const h = SAMPLE_M / 4;
      for (let j = 0; j < 4; j++) {
        const thMid = th + curvature(sc + h / 4) * (h / 2);
        x += Math.cos(thMid) * h;
        y += Math.sin(thMid) * h;
        th += ((curvature(sc) + curvature(sc + h)) / 2) * h;
        sc += h;
      }
      xs.push(x); ys.push(y); ths.push(th);
    }
  }
  function pose(s: number): RoadPose {
    if (s <= 0) return { x: s, y: 0, theta: 0 }; // 시작점 뒤는 직선 연장
    ensureSamples(s);
    const f = s / SAMPLE_M, i = Math.floor(f), u = f - i;
    const lerp = (a: number[]) => a[i]! + (a[i + 1]! - a[i]!) * u;
    return { x: lerp(xs), y: lerp(ys), theta: lerp(ths) };
  }

  return {
    coveredUntil: r.covered_until_m,
    curvature,
    pose,
    toWorld(s, d) {
      const p = pose(s);
      return { x: p.x - d * Math.sin(p.theta), y: p.y + d * Math.cos(p.theta) };
    },
    turnNear(s, aheadM) {
      const t = turns.find(t => t.end_s_m > s);
      return t && t.start_s_m <= s + aheadM ? t : null;
    },
  };
}
