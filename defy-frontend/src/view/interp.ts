/*
 * 렌더 버퍼 (명세 4.2).
 * - 도착 시각 기준 render_buffer_ms(100ms) 뒤의 "표시 시각"에서 같은 generation 두 표본 사이를 보간한다.
 * - 보간은 차량 위치(x, y)와 방향(psi)만. 속도·신뢰도·경보 등은 표시 시각 직전 표본 값을 그대로 쓴다.
 * - 새 표본이 없으면 마지막 상태를 유지한다 (외삽 없음).
 * - 새 generation 이 오면 이전 표본을 모두 버린다. 낮은 generation / 이미 받은 frame_seq 는 무시한다.
 */
import type { SnapshotView, VehicleView } from '../contract-access';

interface Entry { view: SnapshotView; recvMs: number }

export interface DisplayState {
  current: SnapshotView; // 표시 시각 직전 표본 (패널·그래프 커서 기준)
  ego: VehicleView;      // 보간된 자차
  nb: VehicleView;       // 보간된 이웃차
  simTime: number;       // 보간된 표시 시각 (깜빡이 위상)
}

const KEEP_MS = 2000;

const lerp = (a: number, b: number, u: number) => a + (b - a) * u;
const lerpAngle = (a: number, b: number, u: number) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * u;
const lerpVehicle = (a: VehicleView, b: VehicleView, u: number): VehicleView =>
  ({ ...a, x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), psi: lerpAngle(a.psi, b.psi, u) });

export function createRenderBuffer(bufferMs: number) {
  let items: Entry[] = [];

  return {
    // 받은 표본을 넣는다. 반환: 새 generation 으로 바뀌었으면 true
    push(view: SnapshotView, nowMs: number): boolean {
      const last = items[items.length - 1];
      if (last) {
        if (view.generation < last.view.generation) return false;
        if (view.generation === last.view.generation && view.frameSeq <= last.view.frameSeq) return false;
      }
      const newGen = !last || view.generation !== last.view.generation;
      if (newGen) items = [];
      items.push({ view, recvMs: nowMs });
      while (items.length > 2 && items[1]!.recvMs < nowMs - KEEP_MS) items.shift();
      return newGen;
    },

    clear(): void { items = []; },

    lastReceivedMs(): number | null {
      return items.length ? items[items.length - 1]!.recvMs : null;
    },

    sample(nowMs: number): DisplayState | null {
      if (!items.length) return null;
      const t = nowMs - bufferMs;
      let i = -1;
      for (let k = items.length - 1; k >= 0; k--) if (items[k]!.recvMs <= t) { i = k; break; }
      if (i < 0) { const f = items[0]!.view; return { current: f, ego: f.ego, nb: f.nb, simTime: f.simTime }; }
      const a = items[i]!, b = items[i + 1];
      if (!b) { const f = a.view; return { current: f, ego: f.ego, nb: f.nb, simTime: f.simTime }; } // 유지, 외삽 없음
      const u = Math.min(1, Math.max(0, (t - a.recvMs) / (b.recvMs - a.recvMs)));
      return {
        current: a.view,
        ego: lerpVehicle(a.view.ego, b.view.ego, u),
        nb: lerpVehicle(a.view.nb, b.view.nb, u),
        simTime: lerp(a.view.simTime, b.view.simTime, u),
      };
    },
  };
}
