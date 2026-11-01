// 오른쪽 아래 키 표시 (실제 키보드 배열). 키 그림은 표시용이며 운전은 물리 키보드
import type { ConfigView, SnapshotView } from '../contract-access';
import { esc } from './format';

// 물리 배열: 숫자 행 → Q W E R (반 칸 들여쓰기) → A S D (조금 더 들여쓰기). 들여쓰기 폭은 CSS(.krow[data-indent])
const KEY_ROWS: { indent: 0 | 1 | 2; codes: string[]; stopButton?: boolean }[] = [
  { indent: 0, codes: ['Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5'] },
  { indent: 1, codes: ['KeyQ', 'KeyW', 'KeyE', 'KeyR'] },
  { indent: 2, codes: ['KeyA', 'KeyS', 'KeyD'], stopButton: true },
];

// 키캡 설명 줄바꿈: 띄어쓰기에서만 줄을 바꾸고(CSS keep-all), 4글자 이상 낱말은 가운데에 줄바꿈 기회(<wbr>)를 둔다.
// 좁은 키캡에서 "드리/프트"처럼 나뉘고, 넓으면 한 줄 그대로 (문구 자체는 서버 설정 값 그대로)
const descHtml = (text: string): string =>
  text.split(' ').map(w => (w.length >= 4 ? `${esc(w.slice(0, Math.ceil(w.length / 2)))}<wbr>${esc(w.slice(Math.ceil(w.length / 2)))}` : esc(w))).join(' ');

export function createKeysView(rowsRoot: HTMLElement, noteEl: HTMLElement, cfg: ConfigView) {
  const keyEls = new Map<string, HTMLElement>();
  const byCode = new Map(cfg.keyboard.map(k => [k.code, k]));

  for (const row of KEY_ROWS) {
    const r = document.createElement('div');
    r.className = 'krow';
    r.dataset.indent = String(row.indent);
    for (const code of row.codes) {
      const k = byCode.get(code);
      const d = document.createElement('div');
      d.className = 'key';
      d.dataset.code = code;
      d.title = `${k?.label ?? code} — ${k?.description ?? ''}`;
      d.innerHTML = `<span class="cap">${esc(k?.label ?? code)}</span><span class="desc">${descHtml(k?.description ?? '')}</span>`;
      r.appendChild(d);
      keyEls.set(code, d);
    }
    if (row.stopButton) {
      const b = document.createElement('button');
      b.id = 'btnStop';
      b.type = 'button';
      b.textContent = `${byCode.get('Digit0')?.description ?? '시나리오 해제'} (0)`;
      r.appendChild(b);
      keyEls.set('Digit0', b);
    }
    rowsRoot.appendChild(r);
  }

  function render(v: SnapshotView, pressed: ReadonlySet<string>, lock: string | null = null): void {
    for (const [code, d] of keyEls) d.classList.toggle('pressed', pressed.has(code));
    // Q/E: 순간 눌림(밝은 배경)과 지속 깜빡이 ON(노란 테두리 + ON 배지)을 따로 표시. 원본은 signal_side
    keyEls.get('KeyQ')?.classList.toggle('signal-on', v.nb.signal === 'left');
    keyEls.get('KeyE')?.classList.toggle('signal-on', v.nb.signal === 'right');
    // 진행 중 시나리오의 숫자 키 표식
    for (const s of cfg.scenarios) {
      keyEls.get(`Digit${s.key}`)?.classList.toggle('scenario-on', v.scenario?.kind === s.kind);
    }
    // 횡방향 시나리오 중 A/D: 눌림은 표시하되 미적용 표시 (명세 3.2)
    const blocked = !!v.scenario?.steeringBlocked;
    const steering = pressed.has('KeyA') || pressed.has('KeyD');
    for (const code of ['KeyA', 'KeyD']) keyEls.get(code)?.classList.toggle('blocked', blocked && pressed.has(code));
    const notes: string[] = [];
    if (lock) notes.push(`<span class="lock">🔒 ${esc(lock)}</span>`);
    if (blocked) notes.push(`<span class="${steering ? 'bad' : 'on'}">시나리오 중 A/D 미적용</span>`);
    // 실제 반영 상태는 서버 held_applied 로 확인 (키 그림은 표시용 — 카드 툴팁)
    notes.push(`서버 반영 held_applied: [${v.inputs.heldApplied.map(esc).join(', ')}]`);
    noteEl.innerHTML = notes.join(' · ');
  }

  return { render };
}
