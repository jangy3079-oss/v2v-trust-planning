// Python 브리지를 띄우고 줄 단위 JSON 요청/응답을 중계하는 워커. 메인 스레드는 Atomics.wait 로 동기 호출한다.
import { parentPort, workerData } from 'node:worker_threads';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';

const { py, script, cwd, signal, port } = workerData;
const child = spawn(py, ['-X', 'utf8', '-I', '-B', script], { cwd, stdio: ['pipe', 'pipe', 'inherit'] });const waiting = new Map();
let nextId = 1;
let dead = null;
createInterface({ input: child.stdout }).on('line', line => {
  let r; try { r = JSON.parse(line); } catch { return; }
  const w = waiting.get(r.id); if (!w) return;
  waiting.delete(r.id);
  w(r);
});
function finish(reason) {
  dead = reason;
  for (const w of waiting.values()) w({ ok: false, error: reason });
  waiting.clear();
}
child.on('error', e => finish(`python 을 시작하지 못했습니다: ${e.message}`));
child.on('exit', c => finish(`python 브리지가 종료되었습니다 (code ${c})`));
const flag = new Int32Array(signal);
port.on('message', msg => {
  const reply = r => { port.postMessage(r); Atomics.store(flag, 0, 1); Atomics.notify(flag, 0); };
  if (dead) return reply({ ok: false, error: dead });
  const id = nextId++;
  waiting.set(id, reply);
  child.stdin.write(JSON.stringify({ ...msg, id }) + '\n');
});
parentPort.postMessage('ready');
