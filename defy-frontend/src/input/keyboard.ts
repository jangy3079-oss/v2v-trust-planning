/*
 * 물리 키보드 수집 (명세 2.2, 3.2, 3.3).
 * - event.code 로 물리 키를 본다 (한글 입력 상태에서도 동작).
 * - 자동 반복(event.repeat)은 무시한다. WASD 도 새 keydown 만 눌림으로 받는다
 *   → blur·정지·초기화 전부터 누르고 있던 키는 keyup 후 다시 눌러야 반영된다.
 * - Ctrl/Meta/Alt 조합, 단독 수식키·Tab·F키, 입력창·선택창에서 누른 키는 대상이 아니다.
 * - 안내 중에는 intercept 가 키를 소비한다 (운전·깜빡이·시나리오로 전달하지 않음).
 * - 완료 잠금: lockHeldKeys() 시점에 눌려 있던 모든 키가 올라올 때까지 운전·명령 입력을 받지 않는다 (R 제외).
 * - blur / visibilitychange(hidden) 때 눌림·잠금·표시를 모두 비운다.
 */
import type { DriveKey } from '../contracts';
import { orderDriveKeys } from './messages';

const DRIVE: Record<string, DriveKey> = { KeyW: 'W', KeyA: 'A', KeyS: 'S', KeyD: 'D' };
const EDGE_CODES = new Set(['KeyQ', 'KeyE', 'KeyR', 'Digit0', 'Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5']);
// "아무 키"에서 빼는 키: 단독 수식키·포커스 이동·OS/브라우저 기능키
const NON_GENERAL = /^(Shift|Control|Alt|Meta|OS)(Left|Right)?$|^(CapsLock|NumLock|ScrollLock|Tab|ContextMenu|Fn|FnLock)$|^F\d{1,2}$/;

export interface KeyboardHandlers {
  intercept(code: string): boolean;                       // true 면 소비 (안내 진행 등)
  onDrive(held: DriveKey[], tapped: DriveKey | null): void; // WASD 눌림 집합 변경 (tapped = 이번에 새로 눌린 키)
  onEdgeKey(code: string): void;                          // Q/E/0~5/R (keydown 1회)
  onFocusLost(): void;                                     // blur 또는 탭 숨김
  onPhysical(pressed: ReadonlySet<string>): void;          // 키 표시용 물리 눌림
}

function isEditable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName);
}

export function createKeyboard(h: KeyboardHandlers) {
  const held = new Set<DriveKey>();
  const physical = new Set<string>(); // 표시용 (WASD·Q/E·숫자·R)
  const down = new Set<string>();     // 받은 keydown 중 아직 keyup 이 없는 모든 일반 키
  const lock = new Set<string>();     // 완료 잠금: 이 키들이 모두 올라올 때까지 운전 입력 차단

  function clear(): void {
    held.clear();
    physical.clear();
    down.clear();
    lock.clear();
    h.onPhysical(physical);
  }

  window.addEventListener('keydown', e => {
    const code = e.code;
    if (e.ctrlKey || e.metaKey || e.altKey || isEditable(e.target) || NON_GENERAL.test(code)) return;
    if (e.repeat) return; // 자동 반복 무시 (안내 진행도 만들지 않음)
    down.add(code);
    const drive = DRIVE[code];
    const known = !!drive || EDGE_CODES.has(code);
    if (known) { physical.add(code); h.onPhysical(physical); }
    if (h.intercept(code)) return;
    if (!known) return;
    if (lock.size && code !== 'KeyR') return; // 안내 완료 때 눌려 있던 키가 아직 올라오지 않음
    if (drive) {
      held.add(drive);
      h.onDrive(orderDriveKeys(held), drive);
    } else {
      h.onEdgeKey(code);
    }
  });

  window.addEventListener('keyup', e => {
    const code = e.code;
    down.delete(code);
    lock.delete(code);
    if (!physical.delete(code)) return; // 받은 적 없는 키 (조합키·정지 전 눌림 등)
    h.onPhysical(physical);
    const drive = DRIVE[code];
    if (drive && held.delete(drive)) h.onDrive(orderDriveKeys(held), null);
  });

  const lost = () => { clear(); h.onFocusLost(); };
  window.addEventListener('blur', lost);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') lost(); });

  return {
    clear,
    held: (): DriveKey[] => orderDriveKeys(held),
    // 지금 눌려 있는 모든 키를 잠근다 (안내 마지막 단계 완료 시)
    lockHeldKeys(): void { for (const c of down) lock.add(c); },
    isLocked: (): boolean => lock.size > 0,
  };
}
