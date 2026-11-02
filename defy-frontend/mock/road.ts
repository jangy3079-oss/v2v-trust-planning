/*
 * 목업 도로 생성기 (계약 외 확장) — 세대마다 seed 로 굽은 도로를 만든다. 실제 15c 서버는 같은 RoadResponse 형식만 맞추면 된다.
 *
 * 구간 = [직선 리드] + (직진 | 좌회전 | 우회전) 중 하나. 갈림길은 없다 (교차로 판단 기능 전).
 * 회전 = 완화곡선(곡률 0→κ) + 원호 + 완화곡선(κ→0). 같은 seed → 같은 도로.
 */
import type { RoadResponse, RoadTurnInfo } from '../src/road-geometry.ts';

export const ROAD_PARAMS = {
  startStraightM: 300,               // 시작 직선: 안내·탐지기 준비(10초≈250m) 동안은 곧게
  leadStraightM: [80, 220] as const, // 구간마다 앞에 붙는 직선
  radiusM: [250, 500] as const,      // 회전 반경 (25m/s 에서 횡가속 1.3~2.5 m/s²)
  angleDeg: [25, 90] as const,       // 한 번에 꺾이는 각도
  transitionM: 40,                   // 완화곡선 길이
  sendAheadM: 3000,                  // GET /road 가 보내는 범위 (가장 앞 차량 기준)
};

type Piece = RoadResponse['pieces'][number];
const r4 = (n: number) => Math.round(n * 1e4) / 1e4;

// 32비트 seed 의사난수 (mulberry32)
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// seed=null → 직선 도로
export function createMockRoad(seed: number | null) {
  const pieces: Piece[] = [];
  const turns: RoadTurnInfo[] = [];
  let end = seed == null ? Infinity : ROAD_PARAMS.startStraightM;
  const rand = rng(seed ?? 0);
  const between = ([lo, hi]: readonly [number, number]) => lo + (hi - lo) * rand();

  function addSection(): void {
    const s0 = end + between(ROAD_PARAMS.leadStraightM);
    const pick = rand();
    if (pick < 1 / 3) { end = s0; return; } // 직진: 직선만 (다음 구간과 이어져 더 긴 직선)
    const dir = pick < 2 / 3 ? 1 : -1;
    const R = between(ROAD_PARAMS.radiusM), angleDeg = between(ROAD_PARAMS.angleDeg);
    const k = dir / R, Lt = ROAD_PARAMS.transitionM;
    const La = Math.max(0, ((angleDeg * Math.PI) / 180) * R - Lt); // 완화곡선 둘이 합쳐 κ·Lt 만큼 돈다
    const s1 = s0 + Lt, s2 = s1 + La, s3 = s2 + Lt;
    pieces.push(
      { s0_m: r4(s0), s1_m: r4(s1), k0_1pm: 0, k1_1pm: k },
      { s0_m: r4(s1), s1_m: r4(s2), k0_1pm: k, k1_1pm: k },
      { s0_m: r4(s2), s1_m: r4(s3), k0_1pm: k, k1_1pm: 0 },
    );
    turns.push({ turn: dir > 0 ? 'left' : 'right', start_s_m: r4(s0), end_s_m: r4(s3), radius_m: r4(R), angle_deg: r4(angleDeg) });
    end = s3;
  }
  const ensure = (s: number) => { while (end <= s) addSection(); };

  let cursor = 0; // 물리는 대개 앞으로만 가므로 마지막으로 찾은 위치부터 찾는다
  function curvature(s: number): number {
    ensure(s);
    if (cursor >= pieces.length || pieces[cursor]!.s0_m > s) cursor = 0;
    while (cursor < pieces.length && pieces[cursor]!.s1_m <= s) cursor++;
    const p = pieces[cursor];
    if (!p || s < p.s0_m) return 0;
    return p.k0_1pm + (p.k1_1pm - p.k0_1pm) * ((s - p.s0_m) / (p.s1_m - p.s0_m));
  }

  function response(generation: number, frontS: number): RoadResponse {
    const until = seed == null ? 0 : Math.max(0, frontS) + ROAD_PARAMS.sendAheadM;
    ensure(until);
    return {
      generation, generator: 'defy-road-v1', seed,
      covered_until_m: seed == null ? 1e9 : r4(until),
      pieces: pieces.filter(p => p.s0_m < until),
      turns: turns.filter(t => t.start_s_m < until),
    };
  }

  return { curvature, response };
}
