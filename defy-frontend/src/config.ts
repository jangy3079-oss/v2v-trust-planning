// ===== 설정 =====
import type { AutoDriveLevel } from './contracts';
// 서버 주소 (HTTP 기준 경로). Vite dev/preview 서버가 /api 를 목업·실제 서버로 프록시한다 (vite.config.ts, CORS 불필요).
// 다른 출처의 서버를 직접 쓰려면 'http://host:port/api/v1' 처럼 절대 주소로 바꾼다 (그 서버가 CORS·Origin 을 허용해야 함).
export const SERVER_BASE_URL = '/api/v1';

// 데이터 출처: 기본은 샘플(로컬 재생). 주소에 ?source=server 를 붙이면 서버 모드
export const DATA_SOURCE: 'sample' | 'server' =
  new URLSearchParams(location.search).get('source') === 'server' ? 'server' : 'sample';

// 자동 모드 (명세 1.3): 자동 모드를 지원하는 서버면 시작 카드 없이 이 위험도로 자동 세션을 만든다 (시드는 서버가 정함, solver 는 기본 rule).
// 무작위 단계로 바꾸려면 'random'
export const AUTO_START_LEVEL: AutoDriveLevel = 'high';

// 개발·비교용 숨은 경로: ?mode=manual 이면 자동을 지원하는 서버에서도 직접 조종 세션({} 요청)을 만든다 (화면에는 노출하지 않음)
export const FORCE_MANUAL = new URLSearchParams(location.search).get('mode') === 'manual';

