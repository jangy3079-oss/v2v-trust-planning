// 차량 이름·속도 라벨 자리 고르기 (순수 함수, 화면 좌표 CSS px). 라벨 문구·값은 road.ts 가 정하고 여기서는 위치만 정한다
export interface ScreenRect { x: number; y: number; w: number; h: number }

export const hitsAny = (rs: readonly ScreenRect[], x: number, y: number, w: number, h: number): ScreenRect | null =>
  rs.find(r => x < r.x + r.w && r.x < x + w && y < r.y + r.h && r.y < y + h) ?? null;

const LABEL_H = 22;   // 라벨 바탕 높이 (글자 가운데 기준 위아래 11 px)
const PAD_X = 4;      // 글자 좌우 여백
const GAP_Y = 16;     // 차 앞·뒤 끝에서 라벨 가운데까지
const SEARCH_STEP = 4;

// 글자 시작 x · 가운데 y 에 놓인 라벨의 바탕 상자
export const labelBox = (x: number, y: number, tw: number): ScreenRect => ({ x: x - PAD_X, y: y - LABEL_H / 2, w: tw + 2 * PAD_X, h: LABEL_H });

export interface LabelInput {
  cx: number; cy: number; // 차 중심 (화면)
  side: number;           // 차 중심에서 옆 라벨까지 가로 거리
  halfLen: number;        // 차 길이 절반 (화면)
  tw: number;             // 글자 폭
  obstacles: readonly ScreenRect[]; // 도크·거리 눈금·먼저 놓인 라벨
  W: number; H: number;   // 캔버스 크기
}

// 차 왼쪽 → 오른쪽 → 아래 → 위. 모두 막히면 장애물과 겹치지 않고 캔버스 안에 드는 자리 중 차 중심에서 가장 가까운 곳.
// 그런 자리도 없으면 차 아래 (이전 동작). 반환: 글자 시작 x, 가운데 y
export function placeVehicleLabel(o: LabelInput): { x: number; y: number } {
  const { cx, cy, side, halfLen, tw, obstacles, W, H } = o;
  const free = (x: number, y: number) => { const b = labelBox(x, y, tw); return !hitsAny(obstacles, b.x, b.y, b.w, b.h); };
  const inside = (x: number, y: number) => { const b = labelBox(x, y, tw); return b.x >= 0 && b.y >= 0 && b.x + b.w <= W && b.y + b.h <= H; };
  const left = { x: cx - side - tw, y: cy };
  const right = { x: cx + side, y: cy };
  const below = { x: cx - tw / 2, y: cy + halfLen + GAP_Y };
  const above = { x: cx - tw / 2, y: cy - halfLen - GAP_Y };
  for (const p of [left, right, below]) if (free(p.x, p.y)) return p;
  if (free(above.x, above.y) && inside(above.x, above.y)) return above;
  // 가장 가까운 빈자리: 왼쪽·가운데·오른쪽 세 열에서 위아래로 훑는다
  let best: { x: number; y: number } | null = null, bestD = Infinity;
  for (const x of [left.x, below.x, right.x]) {
    for (let y = LABEL_H / 2; y <= H - LABEL_H / 2; y += SEARCH_STEP) {
      if (!inside(x, y) || !free(x, y)) continue;
      const d = Math.hypot(x + tw / 2 - cx, y - cy);
      if (d < bestD) { bestD = d; best = { x, y }; }
    }
  }
  return best ?? below;
}
