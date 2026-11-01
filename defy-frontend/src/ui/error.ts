/*
 * 오류 화면: 세션을 더 쓸 수 없는 오류는 가운데 카드(버튼 포함), 잠깐 알릴 것은 상단 토스트.
 * 문구는 ui/text-ko.ts, 처리 방식은 source/error-policy.ts. 서버 message·details 는 보여주지 않는다 (코드만).
 */
import { BUTTON, ERROR_TEXT } from './text-ko';

export interface ErrorCardAction { label: string; run: () => void }

export function createErrorUi() {
  const card = document.createElement('div');
  card.id = 'errorOverlay';
  card.hidden = true;
  card.innerHTML = `<div class="err-card" role="alertdialog" aria-live="assertive">
    <div class="err-title"></div>
    <div class="err-body"></div>
    <div class="err-code"></div>
    <div class="err-actions"></div>
  </div>`;
  document.body.appendChild(card);
  const title = card.querySelector<HTMLElement>('.err-title')!;
  const body = card.querySelector<HTMLElement>('.err-body')!;
  const codeEl = card.querySelector<HTMLElement>('.err-code')!;
  const actions = card.querySelector<HTMLElement>('.err-actions')!;

  const toastEl = document.createElement('div');
  toastEl.id = 'errorToast';
  toastEl.hidden = true;
  document.body.appendChild(toastEl);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  return {
    // 가운데 카드 (화면 조작을 막는다)
    show(code: string, buttons: ErrorCardAction[]): void {
      const t = ERROR_TEXT[code as keyof typeof ERROR_TEXT] ?? ERROR_TEXT.CONNECTION_FAILED!;
      title.textContent = t.title;
      body.textContent = t.body;
      codeEl.textContent = `오류 코드: ${code}`;
      actions.innerHTML = '';
      for (const b of buttons) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = b.label;
        btn.addEventListener('click', () => { card.hidden = true; b.run(); });
        actions.appendChild(btn);
      }
      card.hidden = false;
    },
    hide(): void { card.hidden = true; },
    get visible(): boolean { return !card.hidden; },

    // 상단 토스트 (조작은 막지 않음)
    toast(text: string, ms = 4000): void {
      toastEl.textContent = text;
      toastEl.hidden = false;
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { toastEl.hidden = true; }, ms);
    },
  };
}

export const newSessionAction = (): ErrorCardAction => ({ label: BUTTON.newSession, run: () => location.reload() });
