import type { ConfigView } from '../contract-access';
import type { EgoControl, Phase, Snapshot, Zone } from '../contracts';

export const esc = (s: unknown): string =>
  String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);

export const fmt = (n: number | null | undefined, d = 2): string => (n == null ? '—' : n.toFixed(d));
export const fmtSigned = (n: number | null | undefined, d = 1): string => (n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(d));
export const kmh = (v: number): string => (v * 3.6).toFixed(1); // 명세 2.1: km/h 표시는 ×3.6
export const yn = (b: boolean): string => (b ? '<span class="on">예</span>' : '아니오');
export const ratio = (a: number | null, b: number): string => (a == null || !b ? '—' : (a / b).toFixed(2));

export const LABEL = {
  phase: { tutorial: '안내', running: '실행 중', paused: '일시정지', error: '오류' } satisfies Record<Phase, string>,
  modelStatus: { not_started: '시작 전', warming_up: '준비 중', active: '활성', error: '오류' } satisfies Record<Snapshot['model_status'], string>,
  zone: { front: '앞', rear: '뒤', alongside: '나란히' } satisfies Record<Zone, string>,
  egoState: { NORMAL: '평소', AVOIDING: '회피', RETURNING: '복귀' } satisfies Record<EgoControl['state'], string>,
  laneSide: { 0: '오른쪽', 1: '중앙', 2: '왼쪽' } as Record<number, string>,
};

export const scenarioLabel = (cfg: ConfigView, kind: string): string =>
  cfg.scenarios.find(s => s.kind === kind)?.label ?? kind;
