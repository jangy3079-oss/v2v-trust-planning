// 명세 3.1: screenX = cx − y·scale, screenY = egoAnchorY − (x − ego.x)·scale, 종·횡 같은 px/m
export const VIEW_BACK_M = 12;     // 자차 뒤 약 12m
export const VIEW_FWD_M = 55;      // 자차 앞 약 55m → 기본 40m 앞 이웃차가 화면 안
const MIN_LAT_SPAN_M = 14;         // 도로 폭 10.5m + 여유. 좁은 화면에서는 이 폭에 맞춘다 (종·횡 동일 척도 유지)

export interface Camera {
  s: number;        // px/m (종·횡 공통)
  egoX: number;     // 카메라가 추적하는 자차 종위치
  W: number;
  H: number;
  sx(y: number): number;   // 월드 y → 화면 X
  sy(x: number): number;   // 월드 x → 화면 Y
  xAt(py: number): number; // 화면 Y → 월드 x
}

export function makeCamera(W: number, H: number, egoX: number): Camera {
  const s = Math.min(H / (VIEW_BACK_M + VIEW_FWD_M), W / MIN_LAT_SPAN_M);
  const anchorY = H - VIEW_BACK_M * s;
  const cx = W / 2;
  return {
    s, egoX, W, H,
    sx: y => cx - y * s,
    sy: x => anchorY - (x - egoX) * s,
    xAt: py => egoX + (anchorY - py) / s,
  };
}

// 캔버스 픽셀 크기를 CSS 크기 × devicePixelRatio 에 맞추고 CSS px 단위 좌표계를 설정
export function fitCanvas(canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D): { W: number; H: number } {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { W: w, H: h };
}
