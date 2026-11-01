/*
 * 안내 4단계 (명세 3.3).
 * - 원래 화면 위 rgba(0,0,0,0.65) 마스크. 활성 키 묶음은 마스크의 구멍이라 어두워지지 않는다
 *   (배경 전체 opacity 를 내리는 방식이 아님).
 * - 구멍 위치는 실제 키 요소의 getBoundingClientRect() 로 구하고, 매 프레임(리사이즈·도크 이동 포함) 다시 계산한다.
 * - 단계·표시 여부는 서버 Snapshot(phase=tutorial, tutorial.step)을 따른다. 정지(paused) 중에는 숨긴다.
 * - 클릭 1회 = onAdvanceClick 1회. 키 진행·R 우선은 input/controller.ts 가 처리한다.
 */
import type { ConfigView, SnapshotView } from '../contract-access';

// 웹 추가 보조 줄 (합의 사항): 2단계에 덧붙인다. 본문은 DemoConfig.tutorial 문구 그대로
const EXTRA_NOTE: Partial<Record<number, string>> = {
  2: '기본 배치에서는 이웃차가 다른 차로에 있어 4(급제동)를 눌러도 자차가 반응하지 않는 것이 정상입니다.',
};
const HOLE_PAD = 3; // 키마다 구멍 (같은 줄의 비활성 키는 어둡게 남는다)
const MASK_FILL = 'rgba(0,0,0,0.65)';

interface Rect { x: number; y: number; w: number; h: number }

export function createTutorialOverlay(cfg: ConfigView, onAdvanceClick: () => void) {
  const root = document.createElement('div');
  root.id = 'tutorialOverlay';
  root.hidden = true;
  root.innerHTML = `
    <svg class="tut-mask" aria-hidden="true"><path class="tut-shade" fill-rule="evenodd"/><g class="tut-rings"></g></svg>
    <div class="tut-bubble" role="dialog" aria-live="polite">
      <div class="tut-step"></div>
      <div class="tut-text"></div>
      <div class="tut-extra"></div>
      <div class="tut-foot"></div>
      <div class="tut-r">R 은 어느 단계에서나 초기화입니다 (안내 진행 키로 쓰지 않음).</div>
    </div>`;
  document.body.appendChild(root);

  const svg = root.querySelector<SVGSVGElement>('.tut-mask')!;
  const shade = root.querySelector<SVGPathElement>('.tut-shade')!;
  const rings = root.querySelector<SVGGElement>('.tut-rings')!;
  const bubble = root.querySelector<HTMLElement>('.tut-bubble')!;
  const stepEl = root.querySelector<HTMLElement>('.tut-step')!;
  const textEl = root.querySelector<HTMLElement>('.tut-text')!;
  const extraEl = root.querySelector<HTMLElement>('.tut-extra')!;
  const footEl = root.querySelector<HTMLElement>('.tut-foot')!;
  shade.setAttribute('fill', MASK_FILL);

  // 클릭 1회당 한 단계 (안내 영역 전체가 클릭 대상)
  root.addEventListener('click', e => { e.preventDefault(); onAdvanceClick(); });

  let shownStep: number | null = null;

  // 강조 키마다 구멍 상자 (실제 요소 위치)
  function holeRects(codes: readonly string[]): Rect[] {
    return codes
      .map(c => document.querySelector<HTMLElement>(`#keyRows [data-code="${c}"]`)?.getBoundingClientRect())
      .filter((r): r is DOMRect => !!r && r.width > 0)
      .map(r => ({ x: r.left - HOLE_PAD, y: r.top - HOLE_PAD, w: r.width + 2 * HOLE_PAD, h: r.height + 2 * HOLE_PAD }));
  }

  function layout(holes: Rect[]): void {
    const W = window.innerWidth, H = window.innerHeight;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('width', String(W));
    svg.setAttribute('height', String(H));
    // 바깥 사각형 + 키마다 둥근 사각형 구멍 → evenodd 로 구멍만 비움 (활성 키는 어두워지지 않음)
    let d = `M0 0H${W}V${H}H0Z`;
    for (const { x, y, w, h } of holes) {
      const r = 6;
      d += `M${x + r} ${y}H${x + w - r}A${r} ${r} 0 0 1 ${x + w} ${y + r}V${y + h - r}A${r} ${r} 0 0 1 ${x + w - r} ${y + h}`
        + `H${x + r}A${r} ${r} 0 0 1 ${x} ${y + h - r}V${y + r}A${r} ${r} 0 0 1 ${x + r} ${y}Z`;
    }
    shade.setAttribute('d', d);
    rings.innerHTML = holes.map(h => `<rect class="tut-ring" x="${h.x}" y="${h.y}" width="${h.w}" height="${h.h}" rx="6" ry="6"/>`).join('');

    // 말풍선 기준: 활성 키 묶음 전체
    const hole: Rect | null = holes.length ? (() => {
      const x0 = Math.min(...holes.map(h => h.x)), y0 = Math.min(...holes.map(h => h.y));
      const x1 = Math.max(...holes.map(h => h.x + h.w)), y1 = Math.max(...holes.map(h => h.y + h.h));
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    })() : null;

    // 말풍선: 구멍 오른쪽(공간이 없으면 위쪽), 화면 안으로 맞춤
    const bw = bubble.offsetWidth, bh = bubble.offsetHeight, m = 12;
    let bx = W / 2 - bw / 2, by = H / 2 - bh / 2;
    if (hole) {
      bx = hole.x + hole.w + 16;
      by = hole.y + hole.h / 2 - bh / 2;
      if (bx + bw > W - m) { bx = hole.x; by = hole.y - bh - 16; }
    }
    bubble.style.left = `${Math.max(m, Math.min(W - bw - m, bx))}px`;
    bubble.style.top = `${Math.max(m, Math.min(H - bh - m, by))}px`;
  }

  return {
    // 매 프레임 호출: 최신 수신 상태 기준
    update(v: SnapshotView | null): void {
      const step = v && v.phase === 'tutorial' ? v.tutorial.step : null;
      root.hidden = step == null;
      if (step == null) { shownStep = null; return; }
      const t = cfg.tutorial.find(x => x.step === step);
      if (!t) return;
      if (shownStep !== step) {
        shownStep = step;
        const total = cfg.tutorial.length, last = step === total - 1;
        stepEl.textContent = `안내 ${step + 1}/${total}`;
        textEl.textContent = t.text;
        extraEl.textContent = EXTRA_NOTE[step] ?? '';
        extraEl.hidden = !EXTRA_NOTE[step];
        footEl.textContent = `Enter 또는 아무 키/클릭으로 ${last ? '완료' : '다음'} · ${step + 1}/${total}`;
      }
      layout(holeRects(t.highlightCodes));
    },
  };
}
