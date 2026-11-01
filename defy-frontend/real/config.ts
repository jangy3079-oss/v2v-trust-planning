/* 실제 15c 서버의 DemoConfig. 화면·키·시나리오 문구는 목업과 같고, 보정값·해결기 목록·배너는 15c 브리지에서 읽는다. */
import type { DemoConfig } from '../src/contracts.ts';
import { MOCK_DEMO_CONFIG as BASE } from '../mock/config.ts';
import { callBridge } from './bridge-sync.ts';

const hello = callBridge<{ available_solvers: ('rule' | 'nmpc')[]; calibration: DemoConfig['calibration'] }>({ cmd: 'hello' });

// server.ts 가 이 이름으로 가져온다 (목업과 같은 이름 유지)
export const MOCK_DEMO_CONFIG: DemoConfig = {
  ...BASE,
  available_solvers: hello.available_solvers,
  calibration: hello.calibration,
  banner: '실제 15c (demo_v15c) · 합성 환경 · 직선 도로 · 시뮬레이션',
};
