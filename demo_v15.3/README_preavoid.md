# demo_v15c — 누적기를 v14b(B14b)로 되돌림, 키 입력 완화는 유지 (합성 환경 · 직선 도로 · 시뮬레이션, 신호 = 가정)

> **합성 환경 · 직선 도로 · 시뮬레이션.** 실제 도로 성능이 아니다. 방향지시등 신호는 가정이다.
> **변경·판정 기준 고정 시각: 2026-10-28 15:41.** 1~3절은 코드 수정 전에 고정했다. 결과는 4절 이후에 추가만 한다.
> **출발점:** `demo_v15b/` 전체 복사(private·logs·results 제외). v15b README는 `README_v15b.md`로 보존했다.
> `demo_v15b`, `demo_v15a`, `demo_v15`, `demo_v13`, `scratch_*`, `v2v_trust_planning/src`는 수정하지 않는다.

## 0. 왜 (사용자 요청)

- v15b는 키 입력 완화와 누적기 v14 B-W 교체를 함께 했다.
- 사용자는 "W 크기를 바꿨으니 누적기는 v14b로 되돌린다"를 선택했다. 키 입력 완화만으로 조작 중 신뢰도 저하가 충분히 줄었는지 보려는 것이다.

## 1. 바뀌는 것

| 구성 | v15b | **v15c** |
|---|---|---|
| 누적기 | v14 B-W (ν 0.295θ, T_r 2 s, 홀드 없음) | **v14b B14b**: ν p90(0.0225θ), T_hold 3 s, T_r 20 s, cap 2θ, 동결. ν·h는 `scratch_v14b/results/eval_v14b.json` 그대로(= v15/v15a) |
| k_drain 기본 | 1.0 | **1.3**(v15/v15a와 같음). 손잡이 1.0~2.0, 1.0이 아니면 "미검증 설정" |
| 키 입력 | W +1.5, S −3, 2 s 램프, 조향 램프 0.05 | **그대로** |
| rule 백엔드 | 접근 중일 때만 감속(v15a) | **그대로** |
| τ_eff, 탐지기 GRU·R1·R2 | | **그대로** |

- `accum.py`는 `demo_v15a/accum.py`를 그대로 복사했다(= v15).
- `app.py`는 표시 문구만 v14b로 되돌린다.

## 2. 검증 (헤드리스, 시드 0~19, 20회; `validate_v15c.py`) — 판정은 v15b README 3절과 같게 고정

- **3.1 직접 조작(주 판정):** M1(20 s마다 W 2 s, 15 s마다 A/D 0.5 s)과 M2(20 s마다 W 2 s → S 1 s)다.
  - 판정: 탐지 경보 시간 비율 중앙 ≤ 5% **그리고** τ 탐지 < 0.8 시간 비율 중앙 ≤ 10%.
  - **새로 보고:** τ < 0.8 표본과 경보 표본을 성분별로 나눈다(누적기 / R1 / R2). v15b에서 M1이 R1·R2 때문에 미충족이었으므로, 누적기 몫을 따로 보기 위해서다.
- **3.2 회복:** 40 m 앞 급제동 뒤 τ 탐지 ≥ 0.8까지의 시간. 판정은 중앙 ≤ 10 s다. 급가속은 보고만 한다.
  - **사전 예상:** v14b 누적기는 검증 세트에서 k_drain 1.3일 때 급제동 뒤 약 41 s였으므로 미충족이 예상된다. 그래도 판정을 바꾸지 않는다.
- **3.3:** 시나리오 1~7을 같은 정의로 돌린다.
  - 횡방향 회귀는 k_drain 1.0의 v15c τ 탐지와 **v14b 데모(`scratch_v14b/app_v14b.py` τ14b)**의 최대 차 < 1e-9다(v15와 같은 기준).
  - 비교 대상: 이전 v15b(같은 시드), v14b 데모 반사실(시나리오 6).
- **3.4 단위 테스트(`test_v15c.py`):**
  - e 분해
  - k_drain 1.0 = `accum_v14b.trace`
  - S_eff ≤ S. 단, v15 테스트의 e′ 생성 버그를 고친 판을 쓴다(v15b README 6절).
- 예상과 다른 결과도 그대로 보고한다.

## 3. 하지 않는 것
h 재보정, 재학습, 새 이상 유형, NMPC 수정, 판정 완화, 실제 도로 일반화 문구.

---

# 결과 (실행 뒤 추가, 2026-10-28; 1~3절 기준은 바꾸지 않음)

## 4. 요약 (`results/validate_v15c.md`, 시드 0~19, 20회)

| 항목 | **v15c** (v14b 누적기 + 완화 키) | v15b (B-W + 완화 키) | v15a (v14b + 거친 키) | 판정 |
|---|---|---|---|---|
| M2 W/S만: 경보 시간 / τ < 0.8 시간 (중앙) | **0% / 0%** | 0% / 0% | 95% / 95% | **충족** |
| M1 W + A/D: 경보 시간 / τ < 0.8 시간 | **55.5% / 66.1%** | 7.9% / 31.9% | 99% / 99% | **미충족** |
| 급제동 뒤 τ 회복 중앙 (p90) | **43.5 s** (49.9) | 4.1 s | 43.5 s | **미충족** (≤ 10 s, 사전 예상대로) |
| 급가속 뒤 회복 중앙 | 21.3 s | 2.1 s | 21.3 s | 보고 |
| 정지 확인 (rule) | 0/20 정지 | 0/20 | 0/20 | 충족 |
| 6d W/S 정상 가감속 탐지 경보 사건 | 0 (v14b 반사실 40) | 0 | 40 | 충족 |
| 횡방향 회귀 (v14b 데모) | 최대 차 ≤ 1.1e-16, 60/60 | — | — | 충족 |

1. **W/S 조작으로 신뢰도가 떨어지는 문제는 키 입력 완화만으로 해결됐다.** M2에서 경보 0%다. 누적기를 v14b로 되돌려도 마찬가지다.
2. **A/D 조향을 섞으면 v14b 누적기가 다시 쌓인다.** M1 경보 55.5%의 성분은 다음과 같다.
   - τ < 0.8 표본의 최소 성분: **누적기 87%**, R1 6%, R2 7%
   - 경보 표본: 누적기 경로 99%
   - v15b(B-W)에서는 같은 패턴에서 R1·R2가 주원인이었고 누적기는 2%였다.
   - 조향 입력의 e_lat가 v14b의 낮은 ν(0.0225θ)를 넘으며 쌓인다(v15a 탐색에서 조향 램프 0.05일 때 e p99 0.28θ 측정).
   - M1 패턴은 차를 차로 중심에서 1 m가량 벗어나게 한다(v15b README 5절). 그래서 이 경보 일부는 실제 횡 표류에 대한 반응이다.
3. **실제 이상 뒤 회복은 v14b 누적기 성질 그대로 길다.** 급제동 뒤 43.5 s다. 사전 예상대로이고 판정은 미충족이다.
4. **W/S로 직접 하는 급정거·급가속은 여전히 거의 탐지되지 않는다(시드 0~4, 보고만).**

   | 조작 | 탐지 경보 run | 최소 τ 중앙 |
   |---|---|---|
   | 앞 40 m, S 3 s 누름 | 0/5 | 0.84 |
   | 앞 40 m, S 6 s 누름 | 1/5 | — |
   | 뒤 15 m, W 3 s 누름 | 0/5 | — |
   | 뒤 15 m, W 6 s 누름 | 0/5 | — |
   | 키 4 급제동 | 5/5 | — |
   | 키 5 급가속 | 5/5 | — |

   - 원인은 누적기가 아니라 v15b의 키 완화다. W 최대 +1.5, S 최대 −3 m/s²에 2 s 램프라서 보통 가감속이다.
5. 시나리오 1~4, 6, 7, τ_eff ≥ τ(위반 0/360,000)는 v15a와 같은 양상이다. 전체는 `results/validate_v15c.md`에 있다.
6. 단위 테스트를 통과했다(`results/test_v15c.json`): e 분해 2e-16, k_drain 1.0 = accum_v14b.trace 51/51 ×4, S_eff ≤ S(잘림 전 S′ > S 0.11~0.92%, v15b README 6절 정정 수치와 같음).

## 5. 알려진 한계

- **A/D 조향을 쓰면 신뢰도가 다시 내려간다(M1 미충족).** 원인은 v14b 누적기다. 이것을 피하려면 조향 입력을 더 줄이거나(차로 유지 보조 등), 누적기 문턱을 올려야 한다(= v15b의 B-W).
- **이상 뒤 회복이 길다**(급제동 뒤 약 44 s). k_drain 손잡이로 줄일 수 있다(검증 세트 2.0에서 약 27 s, v15 README 10절).
- **W/S로 급정거·급가속을 직접 만들 수 없다.** 키 4·5를 쓴다. "3 s 이상 누르면 급정거·급가속" 기능은 넣지 않았다(제안만).
- 시나리오 1·2 미충족, 뒤·옆 rule 반응 감소, 신호 켠 채 이상의 사각지대는 이전 버전과 같다.
- 합성 환경 · 직선 도로 · 시뮬레이션이다.

## 6. 파일

| 파일 | v15b 대비 |
|---|---|
| `accum.py` | `demo_v15a/accum.py`로 되돌림(v14b B14b, k_drain 기본 1.3) |
| `app.py` | 표시 문구만 v14b로(홀드 3 s 표시), 기본 스크립트 `demo_v15c.json` |
| `test_v15c.py` | `demo_v15a/test_v15.py` + e′ 생성 버그 수정 |
| `validate_v15c.py` | v15b 검증 + 성분 분해, 회귀 대상 v14b 데모, 이전 비교 v15b |
| `sim.py`(키 완화), `ego_controller.py`(v15a rule), `detector.py` | v15b와 같음 |
| `README_v15b.md`, `README_v15a.md`, `README_v15.md` | 이전 README 보존 |

- 수정 없음: `demo_v15b`, `demo_v15a`, `demo_v15`, `demo_v13`, `scratch_*`, `v2v_trust_planning/src`.

## 7. 실행

명령은 한 줄씩 붙여 넣는다. 줄 끝에 `#` 주석을 붙이지 않는다.

```
cd "/Users/mac/Documents/Claude/Projects/논문 만들기/Drive/demo_v15c"
/opt/anaconda3/bin/python -I -B app.py --backend rule
/opt/anaconda3/bin/python -I -B app.py --headless
/opt/anaconda3/bin/python -I -B test_v15c.py
/opt/anaconda3/bin/python -I -B validate_v15c.py --workers 8
```

| 명령 | 하는 일 | 시간 |
|---|---|---|
| `app.py --backend rule` | **창을 띄워 직접 조작** | — |
| `app.py --headless` | `demo_v15c.json`(급제동 → 급가속)을 창 없이 실행 | — |
| `test_v15c.py` | 단위 테스트 | 약 1분 |
| `validate_v15c.py --workers 8` | 전체 검증 | 약 5분 |

## 8. git 명령 (실행하지 않음)

```
cd "/Users/mac/Documents/Claude/Projects/논문 만들기/Drive/v2v_trust_planning"
git switch -c demo-v15c
mkdir -p demo/v15c
rsync -a --exclude private/ --exclude cache/ --exclude logs/ --exclude __pycache__/ --exclude '*.pt' --exclude '*.pkl' ../demo_v15c/ demo/v15c/
git add demo/v15c
git status --short demo/v15c
git commit -m "demo_v15c: revert accumulator to v14b B14b while keeping gentler key inputs (synthetic straight road, simulation only; turn signal assumed)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin demo-v15c
```
