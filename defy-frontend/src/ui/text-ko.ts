/*
 * 사용자 화면 문구 (오류·정지·운전 잠금). 문구는 이 파일에서만 고친다.
 * 서버가 보낸 message·details 는 화면에 그대로 내보내지 않는다 (콘솔·디버그 로그에만). stack trace 노출 금지.
 */
import type { ErrorCode, PauseReason } from '../contracts';

export interface ErrorText { title: string; body: string }

// 사용자에게 보이는 오류 (명세 9). 조용히 처리하는 코드는 여기 없다 (source/error-policy.ts 참고)
export const ERROR_TEXT: Partial<Record<ErrorCode | 'CONNECTION_FAILED', ErrorText>> = {
  UNAUTHENTICATED: { title: '세션 인증이 끝났습니다', body: '이 세션으로는 더 조작할 수 없습니다. 새 세션을 시작하면 안내부터 다시 시작합니다.' },
  FORBIDDEN: { title: '세션 인증이 끝났습니다', body: '이 세션으로는 더 조작할 수 없습니다. 새 세션을 시작하면 안내부터 다시 시작합니다.' },
  SESSION_NOT_FOUND: { title: '세션이 만료되었습니다', body: '서버에 이 세션이 없습니다. 새 세션을 시작하면 안내부터 다시 시작합니다.' },
  SESSION_EXPIRED: { title: '세션이 만료되었습니다', body: '연결이 끊긴 뒤 오래 지나 세션이 정리되었습니다. 새 세션을 시작하면 안내부터 다시 시작합니다.' },
  CONTROL_CONFLICT: { title: '다른 창에서 사용 중입니다', body: '한 세션은 한 창에서만 조작할 수 있습니다. 다른 창을 닫은 뒤 다시 시도하세요.' },
  RATE_LIMITED: { title: '잠시 후 다시 시도해 주세요', body: '요청이 너무 많습니다. 입력 전송을 잠시 줄였습니다.' },
  SOLVER_UNAVAILABLE: { title: '지금은 이 제어 방식을 쓸 수 없습니다', body: '서버에서 선택한 제어 방식(solver)이 준비되지 않았습니다.' },
  MODEL_UNAVAILABLE: { title: '지금은 이 제어 방식을 쓸 수 없습니다', body: '서버의 모델이 준비되지 않았습니다.' },
  INTERNAL_ERROR: { title: '서버 오류입니다. R로 다시 시작하세요', body: '운전 입력은 막혀 있고 R(전체 초기화)만 받습니다.' },
  CONNECTION_FAILED: { title: '서버 연결 실패', body: '서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인한 뒤 새 세션을 시작하세요.' },
};

export const BUTTON = { newSession: '새 세션 시작', retry: '다시 시도', close: '닫기' };

export const RATE_LIMIT_WAIT = (s: number) => `잠시 후 다시 시도해 주세요 — ${s}초 뒤 자동으로 다시 요청합니다`;

// 정지 사유 5종 (명세 6.2, 8.2). title: 오버레이 제목, body: 이유, hint: 다음 행동
export const PAUSE_TEXT: Record<PauseReason, { title: string; body: string; hint: string }> = {
  focus_lost: { title: '일시정지 · 창을 벗어남', body: '창 포커스를 잃었거나 탭이 숨겨져 정지했습니다.', hint: '돌아와서 [재개]를 누르세요.' },
  user: { title: '일시정지', body: '사용자가 정지했습니다.', hint: '[재개]를 누르면 이어서 진행합니다.' },
  input_timeout: { title: '일시정지 · 입력 끊김', body: '0.75초 동안 입력 신호가 오지 않아 서버가 정지했습니다 (키가 눌린 채 남지 않게 하는 안전 장치).', hint: '[재개] 후 키를 새로 누르세요.' },
  disconnected: { title: '일시정지 · 연결 끊김', body: '서버 연결이 끊겨 정지했습니다.', hint: '연결이 복구되면 [연결 복구됨 · 재개]를 누르세요.' },
  overload: { title: '일시정지 · 서버 지연', body: '서버 처리가 늦어져(누적 0.5초 초과) 정지했습니다. 시간 간격을 늘리거나 계산을 건너뛰지 않기 위해서입니다.', hint: '잠시 뒤 [재개]를 누르세요. 밀린 시간은 버립니다.' },
};
export const PAUSE_COMMON = '시간·그래프가 멈춰 있습니다. 입력은 비워졌으며, 재개 후 새로 누른 키부터 반영됩니다.';

// 운전 입력이 막힌 이유 (키 영역·도로에 작게)
export const LOCK_TEXT = {
  fatal: '입력 잠김 · 세션 종료',
  link: '입력 잠김 · 연결 끊김',
  error: '서버 오류 · R 만 입력 가능',
  tutorial: '안내 중 · 키는 안내 진행에 쓰임',
  paused: '정지 중 · [재개] 후 입력',
  completion: '누르고 있던 키를 떼면 운전 입력 시작',
  rateLimited: '전송 줄임 · 요청 과다',
};
