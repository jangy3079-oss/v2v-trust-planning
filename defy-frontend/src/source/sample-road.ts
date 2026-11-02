/*
 * 샘플 모드 도로 (고정) — 90초 샘플(약 2.2km) 동안 직진·우·좌가 번갈아 나오도록 고른 화면 확인용 도로.
 * 샘플 타임라인의 차량 값은 도로와 무관하게 만든 값이라, 곡선 때문에 생기는 이웃 밀림은 서버(목업) 모드에서만 보인다.
 */
import type { RoadResponse } from '../road-geometry';

const LT = 40; // 완화곡선 길이 (목업 서버와 같은 값으로 고름)

// [시작 s, 방향, 반경 m, 각도 °]
const TURNS: [number, 'left' | 'right', number, number][] = [
  [300, 'right', 400, 45],
  [800, 'left', 300, 70],
  [1350, 'right', 350, 35],
  [1750, 'left', 450, 50],
];

function build(): RoadResponse {
  const pieces: RoadResponse['pieces'] = [];
  const turns: RoadResponse['turns'] = [];
  for (const [s0, turn, R, angle] of TURNS) {
    const k = (turn === 'left' ? 1 : -1) / R;
    const La = ((angle * Math.PI) / 180) * R - LT;
    const s1 = s0 + LT, s2 = s1 + La, s3 = s2 + LT;
    pieces.push({ s0_m: s0, s1_m: s1, k0_1pm: 0, k1_1pm: k }, { s0_m: s1, s1_m: s2, k0_1pm: k, k1_1pm: k }, { s0_m: s2, s1_m: s3, k0_1pm: k, k1_1pm: 0 });
    turns.push({ turn, start_s_m: s0, end_s_m: s3, radius_m: R, angle_deg: angle });
  }
  return { generation: 1, generator: 'defy-road-v1', seed: null, covered_until_m: Infinity, pieces, turns };
}

export const SAMPLE_ROAD: RoadResponse = build();
