/*
 * 샘플 재생기 — 서버 대신 Snapshot 을 10Hz 로 "도착"시키는 가짜 서버.
 * 안내(phase=tutorial) 중에는 표본을 만들지 않는다 (명세 1: 안내 중 시간 정지).
 * 안내 완료 뒤에만 재생을 시작한다. 타임라인 끝에 도달하면 새 generation 으로 처음부터 반복한다.
 * 키 입력(운전·깜빡이·시나리오·R)은 재생에 영향을 주지 않는다. 정지(pause)/재개(resume)만 따른다.
 * 서버가 붙으면 이 모듈 대신 WebSocket 수신부가 같은 onSnapshot 을 호출한다.
 */
import type { DemoEvent, PauseReason, Snapshot, TutorialStep } from '../contracts';
import { event, initialSnapshot, withPhase } from './fixtures';
import { buildTimeline, TIMELINE_TICKS, type TimelineFrame } from './sample-timeline';

export interface SampleSink {
  onSnapshot(snapshot: Snapshot, events: DemoEvent[]): void;
}

const PERIOD_MS = 100;   // 10Hz
const MAX_CATCH_UP = 5;  // 명세 4.2: 최대 5 스텝까지 순서대로 따라잡음

type PlayerPhase = 'tutorial' | 'running' | 'paused' | 'stopped';

export function createSamplePlayer(sink: SampleSink) {
  let generation = 0;
  let timeline: TimelineFrame[] = [];
  let next = 0;                  // 다음에 보낼 타임라인 인덱스
  let phase: PlayerPhase = 'stopped';
  let resumePhase: 'tutorial' | 'running' = 'running';
  let tutorialStep: TutorialStep = 0;
  const LAST_STEP: TutorialStep = 3;
  let timer: ReturnType<typeof setInterval> | null = null;
  let due = 0;
  let last: Snapshot | null = null;   // 마지막으로 보낸 snapshot (현재 generation)
  let eventSeq = 0;

  // frame_seq·event_seq 는 generation 안에서 보낸 순서대로 매긴다 (상태 변경 snapshot 포함, 단조 증가)
  function emit(s: Snapshot, events: DemoEvent[]): void {
    const out = { ...s, frame_seq: last ? last.frame_seq + 1 : 0 };
    last = out;
    sink.onSnapshot(out, events.map(e => ({ ...e, event_seq: ++eventSeq })));
  }

  function newGeneration(): void {
    generation += 1;
    timeline = buildTimeline(generation, 1); // event_seq 는 emit 에서 다시 매김
    next = 0;
    last = null;
    eventSeq = 0;
  }

  function stopClock(): void {
    if (timer != null) clearInterval(timer);
    timer = null;
  }
  function startClock(): void {
    stopClock();
    due = performance.now() + PERIOD_MS; // 밀린 wall time 은 폐기 (명세 4.2)
    timer = setInterval(tick, 10);
  }

  function tick(): void {
    const now = performance.now();
    let n = 0;
    while (now >= due && n < MAX_CATCH_UP) {
      if (next >= TIMELINE_TICKS) {
        // 샘플 반복: 새 generation 으로 바로 실행 상태에서 다시 시작 (실제 서버의 reset 과는 다름)
        newGeneration();
        emit(initialSnapshot(generation, 'running'), [
          event(generation, 1, -1, 'session.reset', '샘플 타임라인 반복 — 새 세대 (샘플)'),
        ]);
      } else {
        const f = timeline[next++]!;
        emit(f.snapshot, f.events);
      }
      due += PERIOD_MS;
      n++;
    }
    if (now >= due) due = now + PERIOD_MS; // 밀린 시간은 폐기 (dt 를 늘리지 않음)
  }

  // 같은 표본에 대한 상태 변경 snapshot (새 물리 표본 없음)
  function emitStateChange(p: Snapshot['phase'], reason: PauseReason | null, resume: 'tutorial' | 'running' | null, ev: DemoEvent): void {
    if (!last) return;
    emit(withPhase(last, last.frame_seq + 1, p, reason, resume), [ev]);
  }

  function enterTutorial(kind: 'session.created' | 'session.reset', message: string): void {
    stopClock();
    newGeneration();
    phase = 'tutorial';
    tutorialStep = 0;
    emit(initialSnapshot(generation, 'tutorial', 0, 0), [event(generation, 0, -1, kind, message)]);
  }

  // 안내 완료 → running. 이 시점부터 10Hz 재생
  function completeTutorial(): void {
    phase = 'running';
    emit(initialSnapshot(generation, 'running'), [event(generation, 0, -1, 'tutorial.completed', '안내 완료 · 실시간 시작 (샘플)')]);
    startClock();
  }

  return {
    // 첫 진입: 안내 0단계 초기 화면만 보낸다 (재생 없음)
    start(): void { enterTutorial('session.created', '세션 생성 (샘플)'); },
    // session.reset 에 해당: 새 generation, 안내 0단계, 재생 정지 (명세 8.1)
    reset(): void { enterTutorial('session.reset', 'R 초기화 — 새 세대 · 안내 0단계 (샘플)'); },
    // tutorial.advance 에 해당. 반환: 결과 (서버라면 ack 또는 error)
    tutorialAdvance(expected: TutorialStep): 'ok' | 'INVALID_STATE' | 'TUTORIAL_STEP_MISMATCH' {
      if (phase !== 'tutorial') return 'INVALID_STATE';
      if (expected !== tutorialStep) return 'TUTORIAL_STEP_MISMATCH';
      if (tutorialStep >= LAST_STEP) { completeTutorial(); return 'ok'; }
      tutorialStep = (tutorialStep + 1) as TutorialStep;
      emit(initialSnapshot(generation, 'tutorial', 0, tutorialStep), [event(generation, 0, -1, 'tutorial.advanced', `안내 ${tutorialStep + 1}/4 (샘플)`)]);
      return 'ok';
    },
    // session.pause 에 해당: 시간 정지 + phase=paused snapshot
    pause(reason: PauseReason): void {
      if (phase !== 'running' && phase !== 'tutorial') return;
      resumePhase = phase;
      phase = 'paused';
      stopClock();
      const tick = last?.sample_tick ?? -1;
      emitStateChange('paused', reason, resumePhase, event(generation, 0, tick, 'session.paused', '정지 — 창 포커스 잃음 (샘플)'));
    },
    // session.resume 에 해당: 정지 전 phase 로 복귀. running 이면 재생 재개
    resume(): void {
      if (phase !== 'paused') return;
      phase = resumePhase;
      const tick = last?.sample_tick ?? -1;
      emitStateChange(phase, null, null, event(generation, 0, tick, 'session.resumed', '재개 (샘플)'));
      if (phase === 'running') startClock();
    },
    stop(): void {
      stopClock();
      phase = 'stopped';
    },
    get phase(): PlayerPhase { return phase; },
  };
}
