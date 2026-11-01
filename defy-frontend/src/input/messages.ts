/*
 * 클라이언트 → 서버 메시지 생성 (contracts.ts 의 ClientMessage 형식 그대로).
 * 반환 타입을 InputMessage / CommandMessage 로 선언해 필드 이름·형식을 타입 검사로 고정한다.
 * client_seq 는 연결마다 1 부터 단조 증가 (명세 6.1-3).
 */
import type { CommandBody, CommandMessage, DriveKey, HelloMessage, InputMessage, PingMessage } from '../contracts';

// 샘플 모드(서버 없음)에서 ready 의 connection_id 대신 쓰는 자리표시자
export const LOCAL_CONNECTION_ID = 'local-preview (서버 미연결)';

// 연결 직후 첫 메시지 (envelope 없음). 토큰은 이 메시지에만 싣고 로그에 남기지 않는다
export function helloMessage(sessionToken: string): HelloMessage {
  return { protocol_version: 'v1', type: 'hello', session_token: sessionToken };
}

const DRIVE_ORDER: DriveKey[] = ['W', 'A', 'S', 'D'];
export const orderDriveKeys = (keys: Iterable<DriveKey>): DriveKey[] => {
  const set = new Set(keys); // 중복 제거 (명세 6.2: 중복 원소는 오류)
  return DRIVE_ORDER.filter(k => set.has(k));
};

// request_id 용 UUID v4. randomUUID 는 보안 컨텍스트(https·localhost)에서만 있으므로 대체 경로를 둔다
function uuid(): string {
  const c: Crypto = globalThis.crypto;
  if (typeof c.randomUUID === 'function') return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

// connection_id 는 연결마다 바뀔 수 있으므로 매번 읽는다. 연결이 바뀌면 client_seq 를 1 부터 다시 센다
export function createMessageFactory(getConnectionId: () => string) {
  let clientSeq = 0;
  let conn: string | null = null;
  const next = (): { connection_id: string; client_seq: number } => {
    const c = getConnectionId();
    if (c !== conn) { conn = c; clientSeq = 0; }
    return { connection_id: c, client_seq: ++clientSeq };
  };
  return {
    inputState(generation: number, held: Iterable<DriveKey>, tapped: Iterable<DriveKey>): InputMessage {
      return {
        protocol_version: 'v1',
        type: 'input.state',
        ...next(),
        generation,
        held: orderDriveKeys(held),
        tapped: orderDriveKeys(tapped),
      };
    },
    command(generation: number, body: CommandBody): CommandMessage {
      return {
        protocol_version: 'v1',
        type: 'command',
        ...next(),
        generation,
        request_id: uuid(),
        ...body,
      };
    },
    // 재시도: request_id·action·payload·generation 은 그대로, connection_id·client_seq 만 현재 연결 기준
    resend(m: CommandMessage): CommandMessage {
      return { ...m, ...next() };
    },
    ping(clientTimeMs: number): PingMessage {
      return { protocol_version: 'v1', type: 'ping', ...next(), client_time_ms: clientTimeMs };
    },
  };
}
