// src/contracts.ts 가 원본 계약 파일과 바이트 단위로 같은지 확인한다 (복사본 수정 방지)
// 원본은 ../demo_v15c/docs/Defy_Tech_Spec_Contracts/ 에 있다. 원본이 없는 PC(클론만 한 팀원)에서는 경고만 하고 넘어간다.
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const original = fileURLToPath(new URL('../../demo_v15c/docs/Defy_Tech_Spec_Contracts/web-demo-contracts.ts', import.meta.url));
const copy = fileURLToPath(new URL('../src/contracts.ts', import.meta.url));

if (!existsSync(original)) {
  console.warn(`[check:contracts] 원본 계약 파일이 없어 비교를 건너뜁니다: ${original}`);
  process.exit(0);
}
if (!readFileSync(original).equals(readFileSync(copy))) {
  console.error(`contracts.ts 가 원본과 다릅니다.\n  원본: ${original}\n  복사본: ${copy}\n원본을 다시 복사하세요.`);
  process.exit(1);
}
console.log('contracts.ts = 원본 web-demo-contracts.ts (동일)');
