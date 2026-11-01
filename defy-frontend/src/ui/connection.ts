/*
 * 서버 연결 상태 표시 (서버 모드).
 * - 첫 연결 중: 화면 가운데 카드. 실패는 ui/error.ts 의 오류 카드 (샘플 모드로 바꾸지 않음).
 * - 연결 후 끊김·재연결·복구: 상태 줄과 정지 오버레이(ui/pause.ts)에 표시.
 */
import type { LinkState } from '../source/ws-client';
import type { LinkPhase } from './pause';

export function createConnectionOverlay() {
  const el = document.createElement('div');
  el.id = 'connOverlay';
  el.hidden = true;
  el.innerHTML = `<div class="conn-card"><div class="conn-title">서버 연결 중…</div><div class="conn-detail"></div></div>`;
  document.body.appendChild(el);
  const detail = el.querySelector<HTMLElement>('.conn-detail')!;

  return {
    // 첫 연결 중만 표시. 실패는 오류 카드(ui/error.ts)가 표시한다
    show(link: LinkState): void {
      el.hidden = link.state !== 'connecting';
      if (link.state === 'connecting') detail.textContent = link.step;
    },
    hide(): void { el.hidden = true; },
  };
}

// 상태 줄 "연결" 항목 문구
export function linkLabel(link: LinkState | null): { text: string; bad: boolean } | null {
  if (!link) return null;
  switch (link.state) {
    case 'open': return { text: link.recovered ? '서버 WebSocket 연결됨 (재접속·복구 완료)' : '서버 WebSocket 연결됨', bad: false };
    case 'connecting': return { text: `서버 연결 중 (${link.step})`, bad: false };
    case 'reconnecting': return { text: `연결 끊김 — 재연결 ${link.attempt}번째 시도`, bad: true };
    case 'recovering': return { text: `재접속됨 — ${link.step}`, bad: true };
    case 'failed': return { text: `서버 연결 실패 (${link.step})`, bad: true };
  }
}

// 정지 오버레이용 연결 단계
export function linkPhase(link: LinkState | null): LinkPhase {
  if (!link) return { kind: 'none' };
  switch (link.state) {
    case 'open': return { kind: 'open', recovered: link.recovered };
    case 'reconnecting': return { kind: 'down', text: `다시 연결하는 중… (${link.attempt}번째 시도 · ${link.reason})` };
    case 'recovering': return { kind: 'recovering', text: `${link.step} 중…` };
    case 'failed': return { kind: 'down', text: '연결이 종료되었습니다 (오류 카드 참고)' }; // 재개 버튼 비활성
    case 'connecting': return { kind: 'none' }; // 가운데 연결 카드가 표시
  }
}
