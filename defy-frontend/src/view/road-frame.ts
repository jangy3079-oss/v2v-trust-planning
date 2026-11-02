// 도로 기준 좌표(s, 횡위치) → 화면용 좌표. 자차 위치의 도로 접선이 화면 위쪽이 되도록 돌린다 (카메라가 도로를 따라 회전)
// 결과는 기존 카메라가 쓰는 (x=종, y=횡) 좌표라 직선 도로면 입력 그대로다: pt(s, d) = (s, d)
import type { VehicleView } from '../contract-access';
import type { Road } from '../road-geometry';

export interface RoadFrame {
  pt(s: number, d: number): { x: number; y: number };
  vehicle(v: VehicleView): VehicleView; // 위치·방향각을 화면용 좌표로 바꾼 사본 (속도 등은 그대로)
}

export function makeRoadFrame(road: Road, egoS: number): RoadFrame {
  const p0 = road.pose(egoS);
  const c = Math.cos(p0.theta), sn = Math.sin(p0.theta);
  const pt = (s: number, d: number) => {
    const w = road.toWorld(s, d);
    const dx = w.x - p0.x, dy = w.y - p0.y;
    return { x: egoS + dx * c + dy * sn, y: -dx * sn + dy * c };
  };
  return {
    pt,
    vehicle(v) {
      const p = pt(v.x, v.y);
      return { ...v, x: p.x, y: p.y, psi: v.psi + road.pose(v.x).theta - p0.theta };
    },
  };
}
