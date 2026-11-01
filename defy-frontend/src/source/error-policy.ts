/*
 * 오류 코드별 처리 (명세 9). 화면 문구는 ui/text-ko.ts.
 *  cancel      : 화면 표시 없음. 콘솔에 details, 해당 명령만 취소
 *  ended       : 세션 끝 (인증·만료) → 운전 잠금 + [새 세션 시작]
 *  conflict    : 다른 창에서 사용 중 → 운전 잠금 + [다시 시도]
 *  resync      : 조용히 최신 Snapshot 을 받아 화면을 맞춤 (사용자가 원하면 다시 시도)
 *  reconnect   : 조용히 최신 연결·커서 확인 후 재접속
 *  bug         : 화면 표시·자동 재시도 없음, 버그로 기록
 *  rateLimited : "잠시 후 다시 시도" + 입력 전송량 줄임 (REST 는 Retry-After 만큼 대기)
 *  unavailable : 제어 방식 사용 불가 → retryable 이면 [다시 시도]
 *  internal    : 서버 오류 → phase=error 에서는 R 만 허용
 */
import type { ErrorCode } from '../contracts';

export type ErrorHandling = 'cancel' | 'ended' | 'conflict' | 'resync' | 'reconnect' | 'bug' | 'rateLimited' | 'unavailable' | 'internal';

const POLICY: Record<ErrorCode, ErrorHandling> = {
  INVALID_ARGUMENT: 'cancel',
  UNAUTHENTICATED: 'ended',
  FORBIDDEN: 'ended',
  SESSION_NOT_FOUND: 'ended',
  SESSION_EXPIRED: 'ended',
  CONTROL_CONFLICT: 'conflict',
  STALE_GENERATION: 'resync',
  INVALID_STATE: 'resync',
  TUTORIAL_STEP_MISMATCH: 'resync',
  STALE_CONNECTION: 'reconnect',
  STALE_CLIENT_SEQ: 'reconnect',
  REQUEST_ID_CONFLICT: 'bug',
  IDEMPOTENCY_CONFLICT: 'bug',
  RESET_CANCELLED: 'bug',
  RATE_LIMITED: 'rateLimited',
  SOLVER_UNAVAILABLE: 'unavailable',
  MODEL_UNAVAILABLE: 'unavailable',
  INTERNAL_ERROR: 'internal',
};

const KNOWN = new Set<string>(Object.keys(POLICY));
export const isErrorCode = (c: string): c is ErrorCode => KNOWN.has(c);

// 모르는 코드는 서버 오류로 취급
export const errorPolicy = (code: string): ErrorHandling => (isErrorCode(code) ? POLICY[code] : 'internal');
