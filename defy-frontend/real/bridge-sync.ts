/* 15c Python 브리지에 동기로 요청한다 (워커 + Atomics.wait). 한 사람이 쓰는 시연용이라 스텝 동안 이벤트 루프가 잠시 멈춰도 된다. */
import { Worker, MessageChannel, receiveMessageOnPort } from 'node:worker_threads';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const PY = process.env['DEFY_PYTHON'] ?? 'python3';
const TIMEOUT_MS = Number(process.env['DEFY_BRIDGE_TIMEOUT_MS'] ?? 60000);

const signal = new SharedArrayBuffer(4);
const flag = new Int32Array(signal);
const { port1, port2 } = new MessageChannel();
const worker = new Worker(join(here, 'bridge-worker.mjs'), {
  workerData: { py: PY, script: join(here, 'bridge.py'), cwd: resolve(here, '..', '..'), signal, port: port2 },
  transferList: [port2],
});
worker.unref();

export class BridgeError extends Error {}

export function callBridge<T = any>(msg: Record<string, unknown>): T {
  Atomics.store(flag, 0, 0);
  port1.postMessage(msg);
  const r = Atomics.wait(flag, 0, 0, TIMEOUT_MS);
  if (r === 'timed-out') throw new BridgeError('브리지 응답 시간 초과');
  const got = receiveMessageOnPort(port1)?.message as { ok: boolean; data?: T; error?: string } | undefined;
  if (!got) throw new BridgeError('브리지 응답 없음');
  if (!got.ok) throw new BridgeError(got.error ?? '브리지 오류');
  return got.data as T;
}
