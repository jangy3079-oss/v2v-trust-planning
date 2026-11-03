/* 실제 15c 서버의 DemoConfig. 화면·키·시나리오 문구는 목업과 같고, 보정값·해결기 목록·배너는 15c 브리지에서 읽는다. */
import type { DemoConfig } from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG as BASE } from '../mock/config.ts';
import { callBridge } from './bridge-sync.ts';

// 굽은 도로 (명세 5.2, shared/road.ts): straight = 직선, 숫자 = 고정 seed, 그 밖(없음 포함) = 세대마다 무작위. 목업의 MOCK_ROAD 와 같은 규칙
// 곡선은 화면 표시용이다. 15c 물리·탐지기는 직선 그대로 (배너에 알린다)
export const REAL_ROAD = process.env['REAL_ROAD'] ?? 'random';

const hello = callBridge<{ available_solvers: ('rule' | 'nmpc')[]; calibration: DemoConfig['calibration'] }>({ cmd: 'hello' });

// server.ts 가 이 이름으로 가져온다 (목업과 같은 이름 유지)
export const MOCK_DEMO_CONFIG: DemoConfig = {
  ...BASE,
  available_solvers: hello.available_solvers,
  calibration: hello.calibration,
  banner: REAL_ROAD === 'straight' ? '실제 15c (demo_v15c) · 합성 환경 · 직선 도로 · 시뮬레이션'
    : '실제 15c (demo_v15c) · 합성 환경 · 굽은 도로는 표시용 (시뮬레이션은 직선) · 시뮬레이션',
};
