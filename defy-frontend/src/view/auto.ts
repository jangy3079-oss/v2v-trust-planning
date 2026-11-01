/*
 * 자동 모드 표시 (명세 1.3, v1.5). Snapshot.auto 가 있을 때만 요소를 만든다 → 수동 세션의 화면·DOM 은 그대로.
 * - 주행 화면 도크: 상태 · 이벤트 종류 · 위험도(무작위면 지금 단계) · 시드, 다음 이벤트까지(wait 일 때만)
 * - manual: 수동 복귀 사유(stopped_reason → text-ko 문구)와 "R 로 자동 복귀"
 * - 키 그림: 운전자 조종 중 서버 inputs.held_applied 를 운전자 키로 따로 표시 (사람 눌림 .pressed 와 구분)
 * - [시나리오 해제 (0)] 툴팁: 운전자 조종 중 누르면 직접 조종으로 바뀐다
 * 값은 서버 값 그대로이며 다시 계산하지 않는다. edge_margin_m 는 강조하지 않는다.
 */
import type { AutoView, ConfigView, SnapshotView } from '../contract-access';
import { esc, scenarioLabel } from './format';
import { AUTO_CUT_IN, AUTO_LEVEL, AUTO_STATE, AUTO_STOP, AUTO_TEXT } from '../ui/text-ko';

const KEY_CODE: Record<string, string> = { W: 'KeyW', A: 'KeyA', S: 'KeyS', D: 'KeyD' };

export const autoEventLabel = (cfg: ConfigView, kind: string | null): string =>
  kind == null ? '—' : kind === 'cut_in' ? AUTO_CUT_IN : scenarioLabel(cfg, kind);

export const autoLevelLabel = (a: AutoView): string =>
  a.levelSetting === 'random' ? AUTO_TEXT.levelRandomNow(AUTO_LEVEL[a.level]) : AUTO_LEVEL[a.level];

export function createAutoView(dock: HTMLElement, keysEl: HTMLElement, cfg: ConfigView) {
  let box: HTMLElement | null = null;
  let stopTitle: string | null = null; // 툴팁을 바꾸기 전 값 (복원용)

  function ensureBox(): HTMLElement {
    if (box) return box;
    box = document.createElement('section');
    box.id = 'autoBox';
    dock.insertBefore(box, keysEl);
    return box;
  }

  function render(v: SnapshotView): void {
    const a = v.auto;
    const driving = !!a && a.state !== 'manual';
    // 운전자 키 표식: 운전자가 조종 중일 때만 (manual 이면 held_applied 는 사람 키)
    const driverKeys = new Set(driving ? v.inputs.heldApplied.map(k => KEY_CODE[k]).filter(Boolean) : []);
    for (const k of keysEl.querySelectorAll<HTMLElement>('.key')) {
      const on = driverKeys.has(k.dataset.code ?? '');
      if (on || k.classList.contains('driver')) k.classList.toggle('driver', on);
    }
    const stop = keysEl.querySelector<HTMLButtonElement>('#btnStop');
    if (stop && (a || stopTitle != null)) {
      if (stopTitle == null) stopTitle = stop.title;
      stop.title = driving ? AUTO_TEXT.stopButtonTip : stopTitle;
    }
    if (!a) { if (box) box.hidden = true; return; }

    const b = ensureBox();
    b.hidden = false;
    const head = `<h3>${esc(AUTO_TEXT.status)} · ${esc(autoLevelLabel(a))} · seed ${esc(a.seed)}</h3>`;
    if (a.state === 'manual') {
      const reason = a.stoppedReason ? AUTO_STOP[a.stoppedReason] : '—';
      b.innerHTML = `${head}<div class="auto-state manual">${esc(AUTO_STATE.manual)} · ${esc(reason)}</div>
        <div class="auto-hint">${esc(AUTO_TEXT.manualBack)}</div>`;
      return;
    }
    const ev = a.state === 'event' || a.state === 'recover' ? ` · ${esc(autoEventLabel(cfg, a.eventKind))}` : '';
    const next = a.state === 'wait' && a.nextEventIn != null ? `<div class="auto-next">${esc(AUTO_TEXT.nextIn(a.nextEventIn))}</div>` : '';
    const keys = `<div class="auto-keys" title="${esc(AUTO_TEXT.driverKeysTip)}">${esc(AUTO_TEXT.driverKeys)}: [${v.inputs.heldApplied.map(esc).join(', ')}]</div>`;
    b.innerHTML = `${head}<div class="auto-state">${esc(AUTO_STATE[a.state])}${ev}</div>${next}${keys}`;
  }

  return { render };
}
