// ===== 설정 =====
// 서버 주소 (HTTP 기준 경로). Vite dev/preview 서버가 /api 를 목업·실제 서버로 프록시한다 (vite.config.ts, CORS 불필요).
// 다른 출처의 서버를 직접 쓰려면 'http://host:port/api/v1' 처럼 절대 주소로 바꾼다 (그 서버가 CORS·Origin 을 허용해야 함).
export const SERVER_BASE_URL = '/api/v1';

// 데이터 출처: 기본은 샘플(로컬 재생). 주소에 ?source=server 를 붙이면 서버 모드
export const DATA_SOURCE: 'sample' | 'server' =
  new URLSearchParams(location.search).get('source') === 'server' ? 'server' : 'sample';
