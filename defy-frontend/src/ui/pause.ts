/*
 * 정지 오버레이 (명세 8.2). 주행 화면 위에 보인다.
 * - 서버 Snapshot 의 phase=paused 이면 정지 이유와 [재개] 버튼.
 * - 서버 모드에서 연결이 끊겼거나 복구 중이면 "연결이 끊어졌습니다" + 비활성 재개 버튼 (복구되면 활성).
 * - 연결이 돌아와도 자동 재개하지 않는다. 끊김으로 정지했다가 복구되면 [연결 복구됨 · 재개] 버튼.
 */
import type { SnapshotView } from '../contract-access';
import { LABEL } from '../view/format';
import { PAUSE_COMMON, PAUSE_TEXT } from './text-ko';

// 화면 쪽 연결 상태 (샘플 모드는 none)
export type LinkPhase =
  | { kind: 'none' }
  | { kind: 'open'; recovered: boolean }
  | { kind: 'down'; text: string }
  | { kind: 'recovering'; text: string };

export function createPauseOverlay(host: HTMLElement, onResume: () => void) {
  const el = document.createElement('div');
  el.id = 'pauseOverlay';
  el.hidden = true;
  el.innerHTML = `<div class="pause-card">
    <div class="pause-title"></div>
    <div class="pause-reason"></div>
    <div class="pause-note"></div>
    <button type="button" class="pause-resume">재개</button>
  </div>`;
  host.appendChild(el);
  const titleEl = el.querySelector<HTMLElement>('.pause-title')!;
  const reasonEl = el.querySelector<HTMLElement>('.pause-reason')!;
  const noteEl = el.querySelector<HTMLElement>('.pause-note')!;
  const btn = el.querySelector<HTMLButtonElement>('.pause-resume')!;
  btn.addEventListener('click', () => { if (!btn.disabled) onResume(); });

  return {
    render(v: SnapshotView | null, link: LinkPhase): void {
      if (link.kind === 'down' || link.kind === 'recovering') {
        el.hidden = false;
        titleEl.textContent = link.kind === 'down' ? '연결이 끊어졌습니다' : '연결 복구 중';
        reasonEl.textContent = link.text;
        noteEl.textContent = '입력은 비워졌습니다. 복구되면 아래 [재개] 버튼이 활성화됩니다 (자동으로 재개하지 않음).';
        btn.textContent = '재개';
        btn.disabled = true;
        return;
      }
      const paused = !!v && v.phase === 'paused';
      el.hidden = !paused;
      if (!paused || !v) return;
      const reason = v.runtime.pauseReason;
      const t = reason ? PAUSE_TEXT[reason] : null;
      const back = v.runtime.resumePhase ? ` 재개하면 ${LABEL.phase[v.runtime.resumePhase]} 상태로 돌아갑니다.` : '';
      const recovered = link.kind === 'open' && link.recovered && reason === 'disconnected';
      titleEl.textContent = recovered ? '연결 복구됨' : t?.title ?? '일시정지';
      reasonEl.textContent = `${t?.body ?? '정지 상태입니다.'} (${reason ?? '—'})`;
      noteEl.textContent = `${t?.hint ?? ''}${back} ${PAUSE_COMMON}`;
      btn.textContent = recovered ? '연결 복구됨 · 재개' : '재개';
      btn.disabled = false;
    },
  };
}
