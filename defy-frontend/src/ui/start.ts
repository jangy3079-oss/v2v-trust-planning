/*
 * 시작 카드 (서버 모드, health.auto_drive_available=true 일 때만. 명세 1.1·1.3).
 * - 자차 계산 엔진(DemoConfig.available_solvers)과 이웃차 조종(직접/자동)을 고른다. 기본은 rule · 직접 조종.
 * - 자동이면 위험도(기본 보통)와 시드(선택, 비우면 서버가 정함). nmpc 에서는 자동을 고를 수 없다 (rule 에서만 검증).
 * - 주행 중 전환은 없다: 설정을 바꾸려면 새 세션, 같은 설정으로 다시는 R.
 * 문구는 text-ko.ts.
 */
import type { AutoDriveLevel, Solver } from '../contracts';
import type { SessionOptions } from '../contract-access';
import { esc } from '../view/format';
import { AUTO_LEVEL, START_TEXT } from './text-ko';

const LEVELS: AutoDriveLevel[] = ['low', 'normal', 'high', 'random'];

export function showStartCard(solvers: readonly Solver[]): Promise<SessionOptions> {
  const root = document.createElement('div');
  root.id = 'startCard';
  const radio = (name: string, value: string, label: string, checked: boolean) =>
    `<label class="start-opt"><input type="radio" name="${name}" value="${esc(value)}"${checked ? ' checked' : ''}><span>${esc(label)}</span></label>`;
  root.innerHTML = `<form class="start-card" novalidate>
    <div class="start-title">${esc(START_TEXT.title)}</div>
    <fieldset><legend>${esc(START_TEXT.solver)}</legend>
      ${solvers.map(s => radio('startSolver', s, START_TEXT.solverName[s], s === 'rule' || solvers.length === 1)).join('')}
    </fieldset>
    <fieldset><legend>${esc(START_TEXT.mode)}</legend>
      ${radio('startMode', 'direct', START_TEXT.direct, true)}
      ${radio('startMode', 'auto', START_TEXT.auto, false)}
      <div class="start-note start-nmpc" hidden>${esc(START_TEXT.autoNmpc)}</div>
    </fieldset>
    <div class="start-auto" hidden>
      <fieldset><legend>${esc(START_TEXT.level)}</legend>
        ${LEVELS.map(l => radio('startLevel', l, AUTO_LEVEL[l], l === 'normal')).join('')}
      </fieldset>
      <label class="start-seed">${esc(START_TEXT.seed)}
        <input id="startSeed" type="text" inputmode="numeric" autocomplete="off" placeholder="${esc(START_TEXT.seedPlaceholder)}">
      </label>
      <div class="start-note start-bad" hidden>${esc(START_TEXT.seedInvalid)}</div>
      <div class="start-note">${esc(START_TEXT.tutorialNote)}</div>
      <div class="start-note">${esc(START_TEXT.randomNote)}</div>
    </div>
    <button type="submit" id="startGo">${esc(START_TEXT.go)}</button>
  </form>`;
  document.body.appendChild(root);

  const form = root.querySelector<HTMLFormElement>('form')!;
  const autoBox = root.querySelector<HTMLElement>('.start-auto')!;
  const nmpcNote = root.querySelector<HTMLElement>('.start-nmpc')!;
  const autoRadio = root.querySelector<HTMLInputElement>('input[name="startMode"][value="auto"]')!;
  const directRadio = root.querySelector<HTMLInputElement>('input[name="startMode"][value="direct"]')!;
  const seedEl = root.querySelector<HTMLInputElement>('#startSeed')!;
  const seedBad = root.querySelector<HTMLElement>('.start-bad')!;
  const goBtn = root.querySelector<HTMLButtonElement>('#startGo')!;
  const checked = (name: string) => root.querySelector<HTMLInputElement>(`input[name="${name}"]:checked`)?.value ?? '';

  // 시드: 비우면 null(서버가 정함), 아니면 0 이상 안전 정수만
  const seedValue = (): number | null | 'bad' => {
    const t = seedEl.value.trim();
    if (!t) return null;
    if (!/^\d+$/.test(t)) return 'bad';
    const n = Number(t);
    return Number.isSafeInteger(n) ? n : 'bad';
  };

  function sync(): void {
    const nmpc = checked('startSolver') === 'nmpc';
    autoRadio.disabled = nmpc;
    if (nmpc && autoRadio.checked) directRadio.checked = true;
    nmpcNote.hidden = !nmpc;
    const auto = autoRadio.checked;
    autoBox.hidden = !auto;
    const bad = auto && seedValue() === 'bad';
    seedBad.hidden = !bad;
    goBtn.disabled = bad;
  }
  form.addEventListener('change', sync);
  seedEl.addEventListener('input', sync);
  sync();
  goBtn.focus();

  return new Promise(resolve => {
    form.addEventListener('submit', e => {
      e.preventDefault();
      sync();
      if (goBtn.disabled) return;
      const solver = solvers.find(x => x === checked('startSolver')) ?? 'rule';
      const seed = seedValue();
      const auto = autoRadio.checked && !autoRadio.disabled
        ? { level: LEVELS.find(x => x === checked('startLevel')) ?? 'normal', seed: seed === 'bad' ? null : seed }
        : null;
      root.remove(); // 포커스가 카드에 남지 않게 (운전 키가 버튼으로 가지 않음)
      resolve({ solver, auto });
    });
  });
}
