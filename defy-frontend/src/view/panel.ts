/*
 * 오른쪽 패널 (DOM). 스크롤 없는 한 화면 + 탭.
 *  항상 보임: 상태 줄(시간·연결·준비·단계) → 신뢰도 카드(τ 게이지 2 + 경보 배지, 상세는 접기)
 *            → (그래프: chart.ts) → [탭] → 자차 반응 요약 5줄 · 키(keys.ts)
 *  탭: 점수·누적량 / 컨텍스트·자차 / 시나리오·로그
 * 고정 뼈대(제목·접기·탭 버튼)는 index.html 에 있고, 여기서는 값만 다시 그린다 (접기·탭 상태 유지).
 */
import type { ConfigView, EventView, SnapshotView } from '../contract-access';
import { esc, fmt, fmtSigned, kmh, LABEL, ratio, scenarioLabel, yn } from './format';
import { arcGauge, barGauge, lamp } from './gauges';
import { PAPER } from './theme';
import type { ProvenanceMetrics, Reading } from './provenance';
import { ERROR_TEXT } from '../ui/text-ko';

export interface PanelElements {
  status: HTMLElement;
  trustBody: HTMLElement;
  trustRefs: HTMLElement;
  trustLegend: HTMLElement;
  egoSummary: HTMLElement;
  acc: HTMLElement;
  score: HTMLElement;
  context: HTMLElement;
  egoMore: HTMLElement;
  scenario: HTMLElement;
  scenarioMore: HTMLElement;
  log: HTMLElement;
  sumScore: HTMLElement;
  sumContext: HTMLElement;
  sumScenario: HTMLElement;
  provSource: HTMLElement;
  provNote: HTMLElement;
  provEvidenceNote: HTMLElement;
  provEvidence: HTMLElement;
  provConn1: HTMLElement;
  provConn2: HTMLElement;
  sumProvenance: HTMLElement;
}

const SOURCE_LABEL = {
  sample: '데이터 출처: 샘플 (로컬 재생, 서버 미연결)',
  static: '데이터 출처: 샘플 (정적 프레임, 서버 미연결)',
  server: '데이터 출처: 서버 WebSocket',
} as const;

// 서버 전용 값 표시: 미연결 / 아직 없음 / 값
function reading<T>(r: Reading<T>, show: (v: T) => string): string {
  if (r.state === 'disconnected') return '<span class="prov-local">— (서버 미연결)</span>';
  if (r.state === 'none') return '—';
  return show(r.value);
}

const COLOR = PAPER;
const LOG_LINES = 5;

// 서버 값이 아닌 화면 쪽 상태
export interface LocalStatus {
  stale: boolean;
  link: { text: string; bad: boolean } | null; // 서버 모드의 WebSocket 연결 상태 (샘플 모드는 null)
}

type Row = [string, string];
const kv = (rows: Row[], cls = ''): string =>
  `<dl class="kv ${cls}">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;

export function createPanel(els: PanelElements, cfg: ConfigView) {
  // 게이지 눈금 설명 (값은 설정 thresholds 그대로)
  els.trustLegend.innerHTML =
    `<span><i class="ref-alarm"></i>${cfg.tauAlarmRef} 경보 기준</span><span><i class="ref-rec"></i>${cfg.tauRecoveryRef} 회복 참고</span>`;

  // 1. 상태 줄: 시간 · 연결 · 준비 · 단계 (지연·오류는 생길 때만)
  function renderStatus(v: SnapshotView, local: LocalStatus): void {
    const ready = !!v.model?.active;
    const items: Row[] = [
      ['시간', `sim ${fmt(v.simTime, 1)} s · sample ${v.sampleTime == null ? '—' : fmt(v.sampleTime, 1) + ' s'}`],
      ['연결', local.link
        ? `<span class="${local.link.bad ? 'bad' : ''}">${esc(local.link.text)}</span>`
        : v.runtime.connected ? '연결됨' : '<span class="warn">서버 미연결 · 샘플</span>'],
      ['준비', ready
        ? '탐지 활성'
        : `<span class="warn">${LABEL.modelStatus[v.modelStatus]} · 남은 ${v.warmupRemaining}표본 (약 10초)</span>`],
      ['단계', LABEL.phase[v.phase]],
    ];
    // 명세 4.2: 250ms 이상 새 상태가 없으면 마지막 상태 유지 + 지연 표시
    if (local.stale) items.push(['수신', '<span class="bad">지연 — 마지막 상태 유지</span>']);
    if (v.runtime.error) items.push(['오류', `<span class="bad">${esc(ERROR_TEXT[v.runtime.error.code]?.title ?? '서버 오류')} (${esc(v.runtime.error.code)})</span>`]);
    els.status.innerHTML = items.map(([k, val]) => `<div class="item"><span class="k">${k}</span>${val}</div>`).join('');
  }

  // 2. 신뢰도 카드: τ 게이지 2개 + 경보 배지. tau_a2·tau_rule·gru/r1/r2 는 "상세" 접기
  function renderTrust(v: SnapshotView): void {
    const m = v.model;
    const ready = !!m?.active;
    const prep = ready ? '' : `준비 전 · 남은 ${v.warmupRemaining}`;
    const ticks = [
      { at: cfg.tauAlarmRef, color: COLOR.alarmRef },
      { at: cfg.tauRecoveryRef, color: COLOR.recoveryRef },
    ];
    const dial = (kind: 'det' | 'eff') => {
      // 명세 4.1: 모델 출력 전에는 초기 기준 1.0 을 보여주되 "준비 전"으로 표시
      const value = m ? (kind === 'det' ? m.tauDet : m.tauEff) : 1.0;
      const alarm = m ? (kind === 'det' ? m.alarmDet : m.alarmEff) : false;
      const title = kind === 'det' ? 'τ 탐지 (tau_det)' : 'τ 반응 (tau_eff)';
      // 준비 전 · 남은 표본은 같은 카드의 탐지·반응 배지에 표시 (높이 절약)
      const tags = alarm ? `<span class="tag alarm-${kind}">${kind === 'det' ? '탐지 경보' : '반응 경보'}</span>` : '<span class="tag">경보 없음</span>';
      return `<div class="dial">
        <div class="dial-title">${title}</div>
        ${arcGauge({ value, min: 0, max: 1, color: COLOR[kind], center: fmt(value), ticks, alarm })}
        ${m ? '' : '<div class="dial-sub">출력 전 · 초기 기준값</div>'}
        <div class="tags">${tags}</div>
      </div>`;
    };
    els.trustBody.innerHTML = `<div class="trust-grid">${dial('det')}${dial('eff')}
      <div class="lamps">
        ${lamp('탐지 경보', !!m?.alarmDet, 'det', prep)}
        ${lamp('반응 경보', !!m?.alarmEff, 'eff', prep)}
        ${lamp('A2 (참고)', !!m?.alarmA2, 'a2')}
        ${lamp('의도 구간', !!m?.intentWindow, 'intent')}
        ${lamp(`충돌 ${v.ec.collisionCount}`, v.ec.hit, 'hit', v.ec.hit ? '현재 표본' : '')}
      </div></div>`;
    els.trustRefs.innerHTML = `
      <div class="ref-row"><span>tau_a2 (참고 순간값)</span><b>${fmt(m?.tauA2)}</b></div>
      <div class="ref-row"><span>tau_rule</span><b>${fmt(m?.tauRule)}</b></div>
      <div class="ref-row"><span>tau_gru</span><b>${fmt(m?.tauGru)}</b></div>
      <div class="ref-row"><span>tau_r1</span><b>${fmt(m?.tauR1)}</b></div>
      <div class="ref-row"><span>tau_r2</span><b>${fmt(m?.tauR2)}</b></div>`;
  }

  // 4. 자차 반응 요약 5줄
  function renderEgoSummary(v: SnapshotView): void {
    const ec = v.ec;
    // 주행 화면 왼쪽 아래 도크(좁은 폭)에 들어가도록 값 사이 공백을 줄인다
    els.egoSummary.innerHTML = `<h3>자차 반응</h3>${kv([
      ['상태/단계', `${LABEL.egoState[ec.state]}/stage ${ec.stage}`],
      ['속도/v_ref', `${kmh(v.ego.v)}/${kmh(ec.vRef)} km/h`],
      ['reacted/engaged', `${yn(ec.reacted)}/${yn(ec.engaged)}`],
      ['차체 간격', `${fmt(ec.gap, 1)} m`],
      ['충돌 횟수', ec.collisionCount ? `<span class="bad">${ec.collisionCount}</span>` : '0'],
    ])}`;
  }

  // 탭 1: 점수·누적량
  function renderScoreTab(v: SnapshotView): void {
    const m = v.model;
    els.acc.innerHTML = `<div class="acc" title="누적량 S/h · S_eff/h, 흰 선 = 임계 1.0">
      ${barGauge({ label: 'S / h', value: m ? m.S / m.h : null, max: 2, threshold: 1.0, color: COLOR.det })}
      ${barGauge({ label: 'S_eff / h', value: m ? m.Seff / m.h : null, max: 2, threshold: 1.0, color: COLOR.eff })}
      <div class="acc-flags">${lamp('clipped', !!m?.clipped, 'flag')}${lamp('hold_on', !!m?.holdOn, 'flag')}</div>
    </div>`;
    const r2Text = !m ? '—' : m.R2ttb == null ? '예측 2초 내 없음' : ratio(m.R2ttb, m.Tc);
    els.score.innerHTML = `${kv([
      ['e / θ', m ? ratio(m.e, m.theta) : '—'],
      ['R1_S / h_d', m ? ratio(m.R1S, m.hD) : '—'],
      ['e_long / θ', m ? ratio(m.eLong, m.theta) : '—'],
      ['R2_ttb / T_c', r2Text],
      ['e_lat / θ', m ? ratio(m.eLat, m.theta) : '—'],
      ['R2 활성', m ? (m.r2Enabled ? '활성' : '비활성') : '—'],
      ['r_a', m ? `${fmt(m.rA)} m/s²` : '—'],
      ['R2 보정', m ? (m.r2CalibOk ? 'OK' : '미보정') : '—'],
    ], 'two')}`;
    els.sumScore.textContent = `· S_eff/h ${m ? fmt(m.Seff / m.h) : '—'}`;
  }

  // 탭 2: 컨텍스트·자차 (요약에 없는 자차 항목 + nmpc)
  function renderContextTab(v: SnapshotView): void {
    const m = v.model, ec = v.ec;
    const dObs = v.obs ? v.obs.dObs : null;
    els.context.innerHTML = `${kv([
      ['intent_window', m ? yn(m.intentWindow) : '—'],
      ['zone', m ? LABEL.zone[m.zone] : '—'],
      ['dx', m ? `${fmtSigned(m.dx)} m` : '—'],
      ['g', m ? yn(m.g) : '—'],
      ['d_obs', dObs == null ? '—' : `${fmtSigned(dObs, 2)} m`],
      ['경계까지', dObs == null ? '—' : `${fmt(cfg.innerEdges[1] - Math.abs(dObs))} m`],
      ['이웃 깜빡이', v.nb.signal === 'off' ? '끔' : `<span class="on">${v.nb.signal === 'left' ? '왼쪽' : '오른쪽'}</span>`],
    ])}`;
    const rows: Row[] = [
      ['모드 / 엔진', `${esc(ec.controlMode)} / ${esc(ec.solver)}`],
      ['target_y', `${fmtSigned(ec.targetY)} m`],
      ['정책 target_y', `${fmtSigned(ec.policyTargetY)} m`],
      ['최소 간격', `${fmt(ec.gapMin, 1)} m`],
      ['다음 명령 a/δ', `${fmt(ec.cmdNextA)} / ${fmt(ec.cmdNextSteer, 3)}`],
      ['TTC 종방향', ec.ttc == null ? '—' : `${fmt(ec.ttc, 1)} s`],
      ['적용 명령 a/δ', `${fmt(ec.cmdAppliedA)} / ${fmt(ec.cmdAppliedSteer, 3)}`],
      ['TTC 정책(종·횡)', ec.policyTtc == null ? '—' : `${fmt(ec.policyTtc, 1)} s`],
      ['실제 a/δ', `${fmt(v.ego.a)} / ${fmt(v.ego.steer, 3)}`],
      ['실제 횡가속', v.plantEgo ? `${fmt(v.plantEgo.aLat)} m/s²` : '—'],
    ];
    if (ec.solver === 'nmpc' && ec.nmpc) {
      rows.push(
        ['NMPC 풀이', `${fmt(ec.nmpc.solveMs, 1)} ms`],
        ['NMPC fallback', `${ec.nmpc.fallbackNow ? '<span class="bad">현재</span>' : '없음'} · 누적 ${ec.nmpc.fallbackCount}`],
        ['NMPC d_safe', `${fmt(ec.nmpc.dSafe, 1)} m`],
        ['NMPC 오버라이드', yn(ec.nmpcOverride)],
      );
    }
    // 단위·NMPC 계획 안내는 툴팁 (한 열 7줄 이하로 맞춤)
    els.egoMore.title = `a: m/s² · δ: rad${ec.solver === 'nmpc' && ec.nmpc?.predicted.length ? ' · NMPC 예측 궤적은 도로에 "NMPC 계획 (실제 경로 아님)"으로 표시' : ''}`;
    els.egoMore.innerHTML = kv(rows, 'two');
    els.sumContext.textContent = `· zone ${m ? LABEL.zone[m.zone] : '—'}`;
  }

  // 탭 3: 시나리오·로그
  function renderScenarioTab(v: SnapshotView): void {
    const sc = v.scenario, rep = v.report;
    const params = sc
      ? Object.entries(sc.params).filter(([k]) => k !== 'kind').map(([k, val]) => `${esc(k)}=${esc(val)}`).join(', ')
      : '—';
    const delay = (d: number | null) => (d == null ? '미관측' : `${fmt(d, 1)} s`);
    els.scenario.innerHTML = `${kv([
      ['현재', sc ? `<span class="on">${esc(scenarioLabel(cfg, sc.kind))}</span> (${esc(sc.kind)})` : '없음'],
      ['강도', params],
      ['경과', sc ? `${fmt(sc.elapsed, 1)} s${sc.autoEnd != null ? ` / 자동 종료 ${sc.autoEnd} s` : ''}` : '—'],
      ['조향 입력', sc ? (sc.steeringBlocked ? '<span class="on">A/D 미적용</span>' : 'A/D 적용') : '—'],
    ])}`;
    els.scenarioMore.innerHTML = `${kv([
      ['다음 위빙', `${v.nextWeave.amp} m · ${v.nextWeave.period} s`],
      ['최근 주입', rep ? `${esc(scenarioLabel(cfg, rep.kind))} @ ${fmt(rep.startSampleTime, 1)} s` : '없음'],
      ['탐지 / 반응 지연', rep ? `${delay(rep.detectionDelay)} / ${delay(rep.reactionDelay)}` : '—'],
      ['경계 통과 / A2 지연', rep ? `${delay(rep.boundaryDelay)} / ${delay(rep.a2Delay)}` : '—'],
    ])}`;
    els.sumScenario.textContent = `· ${sc ? scenarioLabel(cfg, sc.kind) : '없음'}`;
  }

  function renderLog(events: readonly EventView[]): void {
    const rows = [...events].slice(-LOG_LINES).reverse()
      .map(e => `<li title="${esc(e.message)}"><span class="t">${e.sampleTime == null ? '—' : fmt(e.sampleTime, 1) + 's'}</span>${esc(e.message)}</li>`)
      .join('');
    els.log.innerHTML = `<h4>최근 로그</h4><ul class="log">${rows || '<li class="empty">이벤트 없음</li>'}</ul>`;
  }

  // 탭 4: 데이터 근거. ● = 로컬 측정 (서버가 아니면 "로컬 샘플 기준")
  function renderProvenance(m: ProvenanceMetrics): void {
    const local = m.source !== 'server';
    els.provSource.textContent = SOURCE_LABEL[m.source];
    els.provSource.className = `prov-source ${local ? 'sample' : 'server'}`;
    const L = (label: string) => (local ? `● ${label}` : label);
    const keysText = (k: readonly string[]) => `[${k.join(', ')}]`;
    const seq = m.seq;
    // 증거: 서버만 줄 수 있는 값. 샘플 모드는 모두 "서버 미연결" (보낸 held 만 로컬 사실)
    els.provEvidenceNote.textContent = local ? '— 서버 미연결: 샘플에서는 증거가 없습니다' : '';
    els.provEvidenceNote.className = local ? 'bad' : '';
    els.provEvidence.innerHTML = kv([
      ['입력→반영', reading(m.inputLatencyMs, v => `${Math.round(v)} ms`) + (m.lastAppliedSeq.state === 'ok' && m.lastAppliedSeq.value != null ? ` (#${m.lastAppliedSeq.value})` : '')],
      [L('보낸 held'), m.sentHeld ? `${keysText(m.sentHeld)} #${m.sentSeq}` : '— (보낸 입력 없음)'],
      ['서버 held_applied', reading(m.heldApplied, keysText)],
      ['server_time − 내 시계', reading(m.clockOffsetMs, v => `${v > 0 ? '+' : ''}${Math.round(v)} ms`)],
      ['RTT (ping/pong)', reading(m.rttMs, v => `${Math.round(v)} ms`)],
    ]);
    // 연결 상태: 수신 흐름·순서·세션
    els.provConn1.innerHTML = kv([
      [L('수신 속도'), `${m.recvHz.toFixed(1)} Hz`],
      [L('마지막 수신 후'), m.sinceLastMs == null ? '—' : `${Math.round(m.sinceLastMs)} ms`],
      [L('sample_tick · frame'), seq.lastTick == null ? '—' : `${seq.lastTick} · ${seq.lastFrame}`],
      [L('frame 중복/역행'), `${seq.frameDup} / ${seq.frameBack}${seq.frameDup + seq.frameBack === 0 && seq.received > 1 ? ' (단조)' : ''}`],
      [L('tick 건너뜀/역행'), `${seq.tickSkip} / ${seq.tickBack}`],
      [L('렌더 fps'), `${m.renderFps.toFixed(0)}`],
    ]);
    els.provConn2.innerHTML = kv([
      ['session / protocol', m.sessionId.state === 'ok' && m.protocolVersion.state === 'ok' ? `${esc(m.sessionId.value)} · ${esc(m.protocolVersion.value)}` : reading(m.sessionId, esc)],
      ['generation', reading(m.generation, String)],
      ['input_connection_id', reading(m.inputConnectionId, v => (v == null ? '— (입력 없음)' : esc(v)))],
      ['runtime.step_ms', reading(m.stepMs, v => (v == null ? '— (step 전)' : `${fmt(v, 2)} ms`))],
      ['runtime.lag_ms', reading(m.lagMs, v => `${fmt(v, 0)} ms`)],
    ]);
    // 머리: 샘플은 로컬 측정 표시, 서버는 연결 상태·재연결 횟수·마지막 복구 결과
    if (local) {
      els.provNote.textContent = `● = 로컬 샘플 기준 측정${m.localGeneration != null ? ` · 샘플 세대 ${m.localGeneration}` : ''}`;
      els.provNote.className = 'prov-local';
    } else {
      const rec = m.recovered ? ` · 마지막 복구: 표본 ${m.recovered.samples}, 이벤트 ${m.recovered.events}${m.recovered.truncated ? ' (보존 범위 밖 truncated)' : ''}` : '';
      els.provNote.textContent = `연결: ${m.link?.text ?? '—'} · 재연결 ${m.reconnects}회${rec}`;
      els.provNote.className = m.link?.bad ? 'prov-local bad' : 'prov-local';
    }
    els.sumProvenance.textContent = `· ${m.source === 'server' ? '서버' : '샘플'} · ${m.recvHz.toFixed(1)} Hz`;
  }

  function render(v: SnapshotView, local: LocalStatus): void {
    renderStatus(v, local);
    renderTrust(v);
    renderEgoSummary(v);
    renderScoreTab(v);
    renderContextTab(v);
    renderScenarioTab(v);
  }

  return { render, renderLog, renderProvenance };
}
