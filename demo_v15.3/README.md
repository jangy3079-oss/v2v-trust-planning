# demo_v15c — 공통 차량 모델 + 횡·종방향 회피

**합성·직선·시뮬레이션. 실제 도로 성능이 아니다.** 직선 3차로·이웃 1대·신호는 관측 가능하다는 가정.

2026-10-28: 사용자가 1단계 진단과 제안 방향을 승인했다. 아래 설계/평가를 **구현 전에 고정**한다. 결과를 보고 정책·임계값을 튜닝하지 않는다. 구현 오류 수정은 변경 이력에 추가한다.
기준은 사용자가 정정한 **기존 demo_v15c**다. 이전 README와 실행 코드는 README_preavoid.md, *_preavoid.py로 보존했다. [진단](DIAGNOSIS_PHASE1.md).

## 회피 모드 단일화 (2026-10-29, 사용자 결정)

비교용 `off`(직진)·`lateral`(횡만, 가속 0) 모드를 삭제했다. 자차 회피는 **종+횡(combined) 하나**이고 엔진만 rule/nmpc(B 키) 중에서 고른다.
- ego_controller.py: `MODES=('combined',)`. off/lateral 분기와 `next_backend` 삭제. CLI `--backend`는 combined / rule / nmpc(엔진 별칭)만 받는다.
- app.py: C 키 모드 전환 삭제, Session 기본 backend=combined, HUD·도움말 문구 정리.
- validate_avoidance.py·report_avoidance.py: rule-combined, nmpc-combined(+ v15b 비교)만 집계한다. 모드 삭제로 combined 동작은 바뀌지 않았다. 재실행 4건(rule 3, nmpc 1)이 저장된 궤적과 풀이 시간 외 차이 0이었고, 그래서 기존 결과로 보고서를 다시 만들었다. 이전 off/lateral 원시 run은 private/avoidance에 남아 있지만 집계에서 제외한다.
- 아래 기록의 off/lateral 수치는 당시 비교 결과로만 남긴다.
- 알려진 영향: 예전 탐지 검증 스크립트 `validate_v15c.py`는 `backend="off"` 작업이 있어 지금은 그대로 실행되지 않는다(참고 자료, 재실행 대상 아님).

## v15c-fix2 (2026-10-29, 사용자 결정): 자차만 조향 한계 상향 + 종·횡 연동 회피

사용자 지적: 회피가 너무 느려 거의 못 피한다, 종·횡이 유기적으로 함께 움직였으면 한다. 결정: **이웃 차량 한계는 그대로, 자차 한계만 올린다.**
fix1과 마찬가지로 결과를 본 뒤의 수정이다. 새 값(EGO_RAMP 0.3, GAP_MIN 2 m, CLEAR_Y 2.1 m, REAR_ESCAPE_A 3)은 seed 0–2 빠른 실행을 보며 정했고, 800회 재검증은 한 번만 돌렸다.

| 파일 | 변경 |
|---|---|
| vehicle.py | `Vehicle(state, ramp=RAMP)`: 요가속 한계를 인스턴스별로 둠. `EGO_RAMP=0.3 rad/s²`(합성 설정값, 실측 차량값 아님). 이웃은 기본값 0.05 그대로라 이웃 궤적은 비트 단위로 같다. 클래스·step·나머지 한계(+3/−8, 횡가속 8, 지연 0.3 s)는 공통 |
| sim.py | 자차만 `Vehicle(..., ramp=EGO_RAMP)` |
| planning_demo.py, ego_controller.py | 참조 설계·추종 변화율 제한을 EGO_RAMP 기준으로. 25 m/s 차선변경 약 6 s → 약 3.6 s |
| planning_demo.py | 종·횡 연동: TTC≤4이고 옆 차로가 비면 즉시 차선변경(이전: 3≤TTC≤4 창). 이동 전이면 반대쪽 차로도 후보. 전방 같은 차로 위협의 제동 = 횡 참조가 이웃 차체를 벗어날 때까지(중심 간 2.1 m) 범퍼 간격 2 m를 지키는 최소 등감속 + 이웃 관측 감속(a_obs), 단계 상한 0/−5/−8 안. 측면 진입(\|dy\|>1.8)은 기존 단계 제동. 횡으로 벗어나면 제동 해제·속도 회복 |
| ego_controller.py | 뒤차 단독 위협 TTC≤4: 제동 대신 +3 m/s² 가속 |
| app.py | 정책에 이웃 관측 가속 `a_obs` 전달 |
| test_avoidance.py | 실행 가능 참조 테스트는 자차 한계로. TTC 단계 경계 검사는 유지하되 제동은 "단계 상한 이내"로 검사(고정값 → 필요 감속). 이웃 0.05·자차 0.3 검사 추가 |
| report_avoidance.py | Astra → fix1 → fix2 3단 비교 |

**재검증 (800회, 합성·직선·시뮬레이션):**

| 조합 | 충돌 run/160 fix1→fix2 | 복귀 완료 fix1→fix2 | 급제동 15/25/40 m 자차 최저속도 중앙값 fix1→fix2 [m/s] |
|---|---|---|---|
| rule-combined | 20→20 | 120→140 | 6.4→11.0 / 13.4→15.2 / 15.5→24.9 |
| nmpc-combined | 20→20 | 120→140 | 3.1→7.5 / 10.8→11.7 / 11.9→19.9 |
| rule-lateral | 80→40 | 120→112 | (가속 없음) |
| nmpc-lateral | 80→40 | 120→113 | (가속 없음) |

- 도로 이탈 0/800, 최대 \|Y\| 3.64 m. 정상 \|a_lat\| 최대 2.17 m/s²(4 기준 위반 0). 가속·변화율·슬립 한계 위반 0.
- combined 충돌 20/160은 전부 rear_accel이다. seed0 기준 반응 경보 24.1 s가 충돌 23.5 s보다 늦다. 제어기가 반응하기 전에 충돌하며, 탐지기는 수정 금지라 그대로 둔다. 뒤차 가속 회피 로직은 경보가 늦어 이 시나리오에서는 효과가 없다.
- 횡 회피만 쓰는 모드는 brake25/40을 피하게 됐지만 brake15(15 m)는 여전히 20/20 충돌이다.
- 이전 결과: `results/avoidance_fix1_ego_ramp005/`, `private/avoidance_fix1_ego_ramp005/`. 이전 소스: `private/before_ego_ramp/`.

## v15c-fix (2026-10-28, 사용자 지시): 차선 무시 회피·화면 줌 수정

사용자 지적: (1) 자차가 차선을 무시하고 회피한다, (2) 시작부터 이웃이 안 보인다(줌된 것처럼). 유지 요청: 차량 움직임(공통 Vehicle 플랜트)이 실제 차량 같다.
**이 절의 변경은 사전 고정 설계가 아니라 실패 결과를 본 뒤의 수정이다.** 새 상수(ETA, TRACK_W/Z, NMPC_HYST)는 seed 0–2 빠른 실행을 보며 정했고, 아래 800회 재검증은 그 뒤 한 번만 돌렸다(재검증 결과로 다시 바꾸지 않음).

**원인 (로그 근거, brake25 seed0 rule-combined):**
- 플랜트 조향 변화율 상한 = L·R_RAMP/v = 0.0054 rad/s(25 m/s). 기존 추종기(ω=1, ζ=1)와 4초 참조는 요가속 ≈0.13 rad/s²(상한 0.05의 2.6배)을 요구 → 명령 δ 0.1~0.4 rad, 실제 δ는 0.005 rad/s로 따라감 → 변화율 포화 진동이 커지며 발산(Y −9 → +21 → −15 m …, 최대 |Y| 241 m).
- 횡목표에 차선 개념이 ±0.5/±3.5 상수로만 있고, 추종 실패 시 차선·도로에 묶는 장치가 없었음.
- NMPC: 내부 모델은 δ ±0.5 rad 즉시 적용·변화율 무제한 → 계획이 0.2 s에 0.75 m 되돌아가는 식으로 플랜트가 실행 불가. 매 스텝 현재 상태에서 재계획되므로 회피 전 정상 주행에서도 1e-5 m 잡음이 지수 성장해 도로 밖으로 나감 → 원본의 |Y|≤5.25 하드 제약이 infeasible → fallback −3 m/s² 반복 → 정지.
- 줌: app.py SX(종방향 px/m)를 10→26으로 올려 전방 시야가 약 24 m로 줄었음 → 25/40 m 앞 이웃이 화면 밖.

**수정 (vehicle.py·sim.py·탐지기·누적기·Config·NMPC 가중치는 그대로):**

| 파일 | 변경 |
|---|---|
| planning_demo.py | 횡참조를 **거리 기반** 5차 y(x)로 교체. 시작 y·기울기·곡률은 실제 상태와 연속. 길이 S는 설계 속도에서 요가속 ≤ ETA·R_RAMP(ETA=0.6), 횡가속 ≤ ETA·4 m/s²가 될 때까지 늘림 → 플랜트가 항상 실행 가능. 횡목표는 `snap_lateral`로 {차선 중심, 중심 ±0.5 m}(3차로 안)에만 둠. 차선변경 후보 = 원래 차선 ± 3.5 m(도로 안일 때만). 빈 옆 차선에 안착하면 속도 회복(추월 → 복귀 가능). NMPC 일시 오버라이드 경로를 정책 경로와 분리(`ov`, `ref_at(base=…)`). |
| ego_controller.py | 공통 추종기 `track()`: 앞먹임 = 참조 곡률(액추에이터 지연 0.3 s만큼 미리보기), 되먹임 = 횡오차 2차계 ω=0.4 rad/s, ζ=1, 요레이트 명령 자체를 R_RAMP로 변화율 제한(포화·와인드업 방지). NMPC = **계획기**: 가속은 NMPC 값, 횡은 지평 끝 Y의 정책 경로 대비 편차가 0.25 m를 넘으면 차선 격자에 스냅한 목표로 같은 참조 생성기에 넘기고, 편차가 사라지면 정책 목표로 되돌림. NMPC 조향을 플랜트에 직접 넣지 않음. |
| app.py | 종·횡 동일 척도 유지, 26 → **11 px/m**(전방 ≈55 m, 후방 ≈16 m). |
| test_avoidance.py | `lane_anchored_feasible_reference` 추가: 스냅 결과가 차선선/도로 밖이 아님, 속도 8–30 m/s × 초기상태 3 × 목표 5에서 변화율 클립 0회·최종 오차 <0.05 m·차체 도로 안. |
| report_avoidance.py | Astra 결과로 하드코딩된 판정 문장을 계산값으로 교체, 수정 전/후 표 추가. |
| docs/WEB_DEMO_TECH_SPEC.md, docs/web-demo-contracts.ts | 웹 시연 명세 v1.2로 갱신(척도 11px/m, 차선 고정 규칙, NMPC 계획기, `policy_target_y_m`/`nmpc_override` 필드, 인수 테스트 A21–A23, 소스 해시). v1.1은 `*_v1.1.*`로 보존. |

**재검증 결과 (같은 800회 프로토콜, 8시나리오 × seed 0–19, 합성·직선·시뮬레이션):**

| 조합 | 충돌 run/160 전→후 | 자차 중심 도로 이탈 전→후 | 최대 \|Y\| m 전→후 | 종료 정지 전→후 | 복귀 완료 전→후 |
|---|---|---|---|---|---|
| rule-lateral | 60→80 | 60→0 | 121.22→3.27 | 0→0 | 67→120 |
| rule-combined | 20→20 | 58→0 | 241.17→3.59 | 0→0 | 78→120 |
| nmpc-lateral | 25→80 | 160→0 | 154.57→3.49 | 0→0 | 0→120 |
| nmpc-combined | 12→20 | 160→0 | 53.23→3.98 | 160→0 | 0→120 |

- 도로 이탈은 0으로 줄었다. 최대 |Y| 3.98 m는 바깥 차선 중심 + 0.5 m 비킴이며, 차체 끝은 4.88 m로 도로(5.25 m) 안이다.
- 신규 800회의 정상 |a_lat| 최대값은 0.69 m/s²다. a·δ̇·슬립 한계 위반은 0이다. NMPC fallback은 combined 91회, lateral 2716회(전체 스텝 기준)다.
- **횡 회피만 쓰는 모드는 급제동 시나리오(brake15/25/40) 60/60 run이 모두 충돌한다.** 수정 전 충돌 수가 더 적었던 것은 자차가 도로 밖으로 크게 벗어나서 생긴 결과이고, 회피에 성공한 것으로 볼 수 없다. 플랜트 요가속 한계 안에서는 차선변경이 끝나기 전에 충돌한다.
- combined의 충돌 20/160은 전부 rear_accel(뒤차 급가속)에서 나왔다. 정책상 뒤차 단독 접근에는 제동하지 않는다. 막은 척하지 않는다.

수정 전 결과는 `results/avoidance_astra_before_fix/`(보고서·그림·요약)와 `private/avoidance_astra_before_fix/`(원시 run)에 보존했다. 아래 "2단계/3단계/최종 결과" 절은 Astra 구현 당시 기록이며, 현행 수치는 [AVOIDANCE_REPORT.md](AVOIDANCE_REPORT.md)와 이 절의 결과 표를 따른다. "종·횡 모두 26px/m", "횡추종 wn=1/zeta=1", "차선변경/복귀 4초" 서술은 v15c-fix로 대체됐다.

**알려진 한계 (수정 후):**
- 플랜트의 요가속 상한 0.05 rad/s²(원래 키보드 R_RAMP 값을 Astra가 플랜트 한계로 채택)를 그대로 쓰므로 차선변경이 느리다: 25 m/s에서 약 6 s/155 m, 감속 중이면 더 길다. 그래서 **횡 회피만으로는 급제동 시나리오를 막지 못한다**(rule-lateral brake 충돌). 이 상한이 실제 차량의 비상 조향 능력보다 훨씬 작다는 점은 결정이 필요한 사항이다(아래 질문).
- NMPC 횡 결정은 0.5 m 격자 목표로 양자화되며 NMPC 최적해 궤적을 그대로 실행하지 않는다.
- 뒤차 급가속(rear_accel)은 정책상 제동하지 않고 비킴이 0.5 m뿐이라 충돌이 남는다.

## 보존 범위

- detector.py, accum.py, 모델/θ/ν/h/홀드/기본 k_drain1.3, R1/R2/GRU/τ_eff 식 유지. 같은 관측 입력의 탐지·누적 출력을 회귀 검사한다. 새 물리 궤적에 대한 탐지 시점까지 같다고 주장하지 않는다.
- demo_v15b 및 이전 데모, scratch_*, v2v_trust_planning/src는 읽기 전용. Config/NMPC 가중치 변경 없음. Git 명령만 문서화.
- UAH 원본/파생물, private/, cache/, *.pt는 커밋 제외. Romera, Bergasa, Arroyo, ITSC 2016; 비상업 연구용, 재배포 금지.

## 2단계: 고정 설계

### 공통 Vehicle

두 차량이 같은 Vehicle 클래스의 step(a_cmd,delta_cmd,dt)를 쓴다. [x,y,heading,v] 기구학 자전거, L=2.7m. 물리 입력 +3/−8m/s², 횡가속8m/s², 속도별 |δ|≤atan(L·8/v²). 명령 클립/액추에이터 제한 별도 계수.

기존 수동의 R_RAMP=.05rad/s², R_REL=.3s를 재사용한다. 조향각 변화율은 L·R_RAMP/max(v,1) rad/s로 환산(atan 도함수 상계), .3s 1차 조향 지연 뒤 제한한다. 가속도도 같은 .3s 지연으로 입력 계단을 완화한다. 기존 키 해제 전용 감쇠를 상시 액추에이터로 일반화하므로 기존과 동일 동작이라는 뜻은 아니다.

100Hz(.01s) 서브스텝, 중점 heading 적분. 속도 상승이 조향 변화율과 횡가속 한계를 동시에 위반하게 하면 양의 가속도를 제한하고 기록한다. 속도 하한0. 키보드는 W+1.5/S−3, 2s 램프, R_RAMP/R_MAX, 해제 감쇠 유지. −heading/1s 복원은 키보드 컨트롤러에만 둔다.

배경/drift/stuck/weave도 목표 heading·요레이트 명령으로 변환해 Vehicle.step을 거친다. 적분 후 횡위치 직접 가산 금지. 이 변경으로 합성 이상 응답이 달라짐을 보고한다. 센서 잡음/A2 필터/탐지 보정은 유지한다.

렌더링은 두 차량 heading 회전, 이전/현재10Hz 상태의60FPS 보간(표시 지연 .1초). 보간을 제어/탐지에 사용하지 않는다. 종·횡 모두26px/m로 같은 척도.

### 공통 회피 정책

입력은 τ_eff와 현재 관측 상태·종/횡 접근속도·차선 점유. 시나리오 라벨 입력 금지.

- τ_eff>.7이고 미개입이면 평소 주행. NMPC 자체 충돌 제약은 정상 τ에서도 작동하므로 그 반응을 별도 보고한다.
- 앞/뒤60m 범위. 현재 또는4초 등속-heading 외삽에서 ego 횡차체 구간과 겹칠 수 있을 때 위험 평가. TTC=범퍼 간격/양의 접근속도. 가까운 측방 진입은 횡차체 경계 도달 시간도 사용. 전방 동일 경로 간격<5m는 저속 접근 사각지대 방지를 위해 TTC≤2로 취급.
- 경보+위험: TTC>4는 가속 중지+반대편 .5m 비킴, 2<TTC≤4는 −5m/s²+비킴, TTC≤2는 −8+횡회피. 실제 가속은 공통 액추에이터를 거친 값. **뒤차 단독 접근에는 제동 강제 안 함**(승인 사항).
- 3≤TTC≤4이고 옆 차선이 비면 ±3.5m 차선 중심으로 이동. 이웃 차체가 후보 차선과 겹치고 |dx|<60이면 점유. 다른 이웃 없음은 시뮬레이션 가정. 선택한 횡 목표는 해제까지 유지. 정상 횡명령4m/s², 비상8로 제한하되 실제 과도 초과는 실패로 기록.
- 원래 차선0, 원래 속도25. τ_eff>.7이2초 유지되고 종 간격5m 또는 횡간격 .5m 확보하면 복귀. 완전 추월(dx<−9.5m이고 뒤차가 멀어짐), 전방60m 밖 또는 도로 밖 이탈도 복귀 조건. 뒤차 접근을 추월 완료로 오인하지 않는다.
- 횡 참조는 현재 y/횡속도에서 목표까지 5차 다항식, 시작/끝 횡가속0, 끝 횡속도0. 차선변경/복귀4초(기존 정상4~6초 하한), 차선 내 비킴2초(기존 해제 유지시간). 목표 변경 때 연결 재시작.
- 속도 참조는 위험 단계 감속률로 낮추고 회복은 기존+.5m/s². 규칙 속도 이득.5, 횡추종 wn=1/zeta=1은 기존 값.
- 기동 완료: |y−target|≤.1m, |heading|≤5°가1초 유지. 복귀 완료는 추가로 |v−v_set|≤.5m/s,1초 유지. 미완료/미복귀는 N/A.

### planning_demo.py 변경 범위 (원본 planning.py 수정 없음)

OvertakeStateMachine 서브클래스가 위 복귀/목표 정책을 담당한다. TrustAwareNMPC 서브클래스는 명시적 부드러운 ref_trajectory와 target_velocity를 기존 solve에 전달한다. Config/가중치/최적화 제약은 그대로다.
장애물 예측을 k+1 시점으로 맞춰 기존 .1초 불일치를 제거한다(등속·등요레이트). 실패 시 데모 fallback은 현재 부드러운 횡목표와 정책 제동을 유지하며 무조건 Y=0 복귀를 제거한다. 최종 어댑터가 단계 제동과 횡명령 한계를 적용하고 Vehicle이 실제 한계를 적용한다. 원 NMPC의−5 제약은 유지하고 비상−8은 외부 어댑터에서 적용한다.
**NMPC 예측에는 새 액추에이터 지연/변화율 상태가 없다는 모델 불일치**를 한계로 명시한다.

### 모드/엔진

C: off / lateral / combined(직진 / 횡만 / 횡+종), B: rule / nmpc 엔진. off는 엔진 독립. 비교5조합: off, rule-lateral, rule-combined, nmpc-lateral, nmpc-combined.
lateral은 현재의 횡회피 의도를 새 공통플랜트에서 비교하는 통제군이며 이전과 같은 궤적은 아니다. 기존그대로 v15b는 별도 비교. CLI --backend rule/nmpc는 combined의 해당 엔진으로 호환한다.

## 3단계: 고정 검증

시드0~19, 모든 조합에서 같은 센서 난수. 두 차량 초기25m/s, 주입20초, 종료60초. σmeas=.1, 신호가정ON, k_drain1.3. **주평가 배경OFF**로 초기/주입 간격·속도를 통제;20회 차이는 센서 잡음이며 운전자20명으로 해석하지 않는다.

**합성·직선·시뮬레이션**

| 시나리오 | 초기 중심간격 | 이웃 차선 | 주입/조작 |
|---|---|---|---|
| brake15/25/40 | 15/25/40m | 1 | 키4: −6m/s² 2초 |
| drift | +5m | 2(+3.5m) | 키1: −.2m/s,30초 해제 |
| stuck | +8m | 2 | 키2: −.01rad/s,26초 해제 |
| rear_accel | −15m | 1 | 키5: +3m/s²,1.5초 램프+2초 유지 |
| normal_lc_on/off | +15m | 2 | D20~23초, ON만 E18초/26초 |

8시나리오×20×5=800회. 이전 v15b는 같은8×20 NMPC=160회, 별도 프로세스에서 모듈 충돌 없이 평가. 정상 차선변경의 경보/불필요 회피 비율을 각각 보고한다. 경보 강제 주입 없음.

지표: 충돌run/사건, 최소차체간격, 최대감속/횡가속/저크/조향변화율/요레이트/슬립, 회피 시작~완료~복귀, 풀이ms p50/p90/p99/max, fallback, 명령클립/변화율 제한. 적용입력과 새 명령을 구분.
회전차체4.5×1.8m SAT/폴리곤 거리로100Hz 충돌 검사, 기존 축정렬도 보조 기록. 이전v15b는10Hz 상태간 선형보간 검사라 연속 충돌검사가 정확히 같지 않음을 명시한다.

### 현실감 판정 (변경 금지)

정상 회피의 실제 |a_lat|≤4, 비상≤8m/s²; 비상은 적용입력을 발행한 정책의 TTC≤2 단계. 실제 a는−8~+3, 모든 서브스텝의 속도별 조향 변화율 한계 이하. 속도 방향−동일 시각 중점heading 차이≤5°;10Hz 종료heading 비교값도 별도 보고.
같은명령의 수동/ego 궤적·가속·횡가속·저크 차이≤1e−9. 충돌감소는 관측치이며 안전 보장이나 새로운 통과 기준으로 만들지 않는다. 실패는 그대로 기록.

단위시험: 동일명령, 극한속도 한계, 복원항 분리, 인과성, 복귀, 뒤차 비제동, 차선점유, 고정관측 탐지/누적 회귀, CLI/UI키/보간.
그림4장: (1)x–y와수동동일명령 (2)v/a/a_lat/r/δ/jerk (3)충돌/최소간격 (4)v15b 전후 움직임. 모두 조건 표기. 대표는 **brake25 seed0**, 동일명령은 고정 차선변경 입력이며 성공시드 선택 금지.

## 변경 이력 (추가만)

- 설계 고정: 현재 v15c 기준, 사용자 승인 반영. 진단 자료/원본 보존. 이후 결과로 튜닝하지 않는다.

## 결과·실행법

구현·검증 뒤 추가한다.

### 평가 계수 정의 보충 (반복 실행 전 고정)

정상 차선변경은 20초 이후 alarm_det, alarm_eff, AVOIDING 진입, 실제 움직임(|Y|>.1m 또는 a<−1m/s²)을 각각 집계한다. 단순 경보와 실제 회피를 구분한다. 20초 전 움직임도 별도 기록해 정상 신뢰도에서 NMPC 자체 제약이 개입하는 경우를 숨기지 않는다. 회피/복귀 시각은 실행 시작 기준이고 N/A는 60초 안에 조건 미달이다. 풀이 시간은 병렬 CPU 부하 아래의 관측값이며 실시간 보장이 아니다. 원본 v15b에는 비상 단계 정의가 없어 횡가속을 모두 정상 기준으로 비교한다. 원본 슬립은 전진 Euler 시작 heading, 새 플랜트 슬립은 중점 heading에 대응시킨다.

## 최종 결과 (설계와 임계값 변경 없음)

**구현과 960회 검증은 완료했으나, 자연스러운 회피·복귀 목표에는 미달했다.** 공통 플랜트의 한계와 동일 명령 동등성 통과가 폐루프 안정성을 뜻하지 않는다. 원문 설계는 [DESIGN_FROZEN.md](DESIGN_FROZEN.md)에 별도 보존했다. [전체 검증 표·그림 4장](AVOIDANCE_REPORT.md).

| 신규 비교 조합 | 충돌 run/160 | 자차 중심 도로 이탈/160 | 회피 진입 / 복귀 완료 | 정상 횡가속4 초과/160 |
|---|---|---|---|---|
| off | 80 | 0 | 0 / 0 | 0 |
| rule-lateral | 60 | 60 | 140 / 67 | 1 |
| rule-combined | 20 | 58 | 140 / 78 | 58 |
| nmpc-lateral | 25 | 160 | 64 / 0 | 0 |
| nmpc-combined | 12 | 160 | 63 / 0 | 2 |

- 동일 명령의 수동/자차 궤적 오차 **0**, 허용1e−9 통과. 두 역할이 실제로 같은 Vehicle 클래스·step을 쓴다.
- 신규800회에서 비상 횡가속8, 가속−8~+3, 조향 변화율, 동일 시각 슬립5° 기준 위반은0. 정상 횡가속4 기준은 위 표처럼 실패했다.
- **NMPC combined는160회 모두 도로 밖으로 벗어나고 종료 시 정지했으며 복귀 완료0**이다. 규칙 combined도 최대 횡이탈241.17m가 관측됐다. 충돌 수가 줄었다는 이유로 성공으로 판정하지 않는다.
- 기존 v15b NMPC는 충돌40/160, 최대 횡가속147.95m/s². 기존 누적기·이웃 플랜트도 달라 전후 차이를 회피 정책만의 효과로 해석할 수 없다.
- 규칙 combined의 뒤차 급가속 seed0은 충돌23.5초, 반응 경보24.1초로 경보보다 충돌이 먼저였다. 탐지기·홀드를 바꿔 숨기지 않았다.
- 대표 brake25 seed0: rule-combined의59.9초 Y=−42.54m, NMPC combined의 Y=−34.06m·v=0. 모든 시계열을 보고서에 남겼다.
- 실제 디스플레이의60FPS 달성률을 벤치마크한 것은 아니다. 100Hz 물리 적분·60FPS 표시 보간, 실제 Pygame 루프·키 전환은 dummy 디스플레이에서 검증했다.

### 원인 해석과 남은 한계

기존 횡추종 이득과4초 참조에 새 조향 변화율·지연을 붙인 폐루프가 안정적이지 않았다. NMPC는 액추에이터 상태를 예측하지 않고 도로 경계 하드 제약은 그대로라, 이미 도로 밖으로 나간 상태에서 해를 찾지 못하는 상황과 반복 fallback·정지가 함께 관측된다. 인과 기여도를 분리한 추가 실험 없이 단일 원인으로 단정하지 않는다. 결과 후 이득/참조 시간/임계값을 조정하지 않았다.

정지 부근의 속도별 조향 상한은 약1.52rad까지 커지며 별도 기계적 조향 상한이 없다. v≥0 처리 시 가속도가0으로 전환돼 NMPC combined 최대 저크543.17m/s³가 기록됐다. 이 모델의 물리 한계 통과를 실제 자동차의 현실감 검증으로 주장하지 않는다.

정상 신뢰도에서도 NMPC 자체 충돌 제약과 관측 잡음 때문에 움직임이 생긴다. 정상 차선변경 조건에서 AVOIDING이0이라고 불필요 움직임이0인 것은 아니다. 보고서는 경보·정책 진입·실제 움직임을 따로 집계한다. 충돌 이후에는 충격 모델 없이 적분을 계속하므로 나중의 복귀가 이전 충돌을 취소하지 않는다.

### 원본 planning.py 대비 변경 diff

원본 파일은 변경하지 않았다. 아래는 **서브클래스/어댑터에서 바뀐 동작의 diff**이며 원본에 적용할 패치가 아니다. `DemoStateMachine.update()`를 데모 제어기가 호출하며, NMPC에는 그 결과를 명시적 참조로 전달해 원본 내부 상태머신을 우회한다.

```diff
- 원본 OvertakeStateMachine.step: 신뢰도 + 추월 위치 조건, 횡목표 즉시 변경
+ DemoStateMachine.update: τ_eff + 종/횡 TTC + 이웃 점유, .5m/3.5m 횡목표
+ quintic reference: 현재 y/횡속도에서 목표로 연결
- 회피 후 추월 조건 위주 복귀
+ τ_eff > .7 2초 + 간격 확보, 또는 추월/이탈 시 RETURNING
- 기존 데모 solve(ego, None, obstacles, target_velocity=25)
+ solve(ego, explicit_ref, obstacles, target_velocity=policy.current_v_cmd)
- 장애물 trajectory[:,0] = 현재 위치 (제약은 X[:,1])
+ trajectory[:,0] = .1초 후 위치 (k+1 시점 일치)
- fallback 횡명령 = clip(-heading - .5*y, ±.5)
+ fallback 횡명령 = 현재 부드러운 횡참조 추종
+ 최종 어댑터: TTC 단계 제동, 정상4/비상8 횡명령 제한
- 자차 전진 Euler에 a/요레이트 즉시 적용
+ Vehicle.step(a,delta): 지연·변화율·물리 한계, 중점100Hz 적분
```

Config, `_build_opti`, NMPC weight_*·제약은 그대로다. 원 NMPC 가속−5 하한은 그대로이며 비상−8은 외부 어댑터에서 적용한다. x_pred를 실제 자차 상태에 대입하지 않는다. 키보드 자세 복원은 KeyboardController에만 있다. 두 차량의 동일 모델이 두 컨트롤러의 추종 성능까지 같다는 뜻은 아니다.

### 변경 파일

| 파일 | 변경 목적 |
|---|---|
| vehicle.py | 공통 기구학 플랜트, 액추에이터/한계 계수, 회전 차체 거리 |
| sim.py | 이웃과 자차에 공통 플랜트 적용, 키보드 보조 분리, 주입을 명령으로 변환 |
| planning_demo.py | 상태머신 서브클래스와 NMPC 참조/fallback 어댑터 |
| ego_controller.py | off/lateral/combined × rule/nmpc, 실제 적용 입력 피드백 |
| app.py | 입력 단위 통일, C/B 전환, 보간·동일 축척·적용 지표 HUD |
| test_avoidance.py, test_render_avoidance.py | 동일명령/한계/정책 경계/복귀/보존/실제 UI 루프 검증 |
| validate_avoidance.py, report_avoidance.py | 고정960회 평가, 집계, 그림 생성 |
| avoidance_demo.json | 고정 대표 brake25 seed0 재현 |
| DESIGN_FROZEN.md, AVOIDANCE_REPORT.md, figures/avoidance_*.png, results/avoidance/ | 고정 설계·검증 결과 |
| README_preavoid.md, app_preavoid.py, sim_preavoid.py, ego_controller_preavoid.py | 변경 전 기존v15c 보존. sim/ego_controller 보존본은 새 구현의 상수/기반 의존성으로도 사용 |

`detector.py`, `accum.py`, 모델/보정/홀드, 원본 src, demo_v15b는 보존했다. 이전 `docs/` 웹 명세는 변경 전 기준이며 이번 작업 범위에서 수정하지 않았다. `*_preavoid.py`를 단독 실행하면 같은 폴더의 신규 모듈을 import할 수 있으므로 그대로 이전 버전 실행기로 간주하지 않는다. 이전 동작 평가는 별도 프로세스의 원본demo_v15b로 했다.

### 실행법 (작업 폴더 루트에서)

```bash
# 창: 자차 회피는 종+횡 하나, B = rule/nmpc. 시작은 rule. --lane 1이면 이웃이 자차 차로에서 시작.
/opt/anaconda3/bin/python -I -B demo_v15c/app.py --backend combined --solver rule

# 고정 대표 입력, 헤드리스
/opt/anaconda3/bin/python -I -B demo_v15c/app.py --headless --script demo_v15c/avoidance_demo.json --backend combined --solver rule
/opt/anaconda3/bin/python -I -B demo_v15c/app.py --headless --script demo_v15c/avoidance_demo.json --backend combined --solver nmpc

# 단위 / 실제 UI 루프(dummy) / 전체 반복 / 집계·그림
/opt/anaconda3/bin/python -I -B demo_v15c/test_avoidance.py
/opt/anaconda3/bin/python -I -B demo_v15c/test_render_avoidance.py
/opt/anaconda3/bin/python -I -B demo_v15c/validate_avoidance.py --workers 6
/opt/anaconda3/bin/python -I -B demo_v15c/report_avoidance.py
```

기존 `--backend rule`/`nmpc`도 해당 엔진의combined 별칭으로 동작한다. 새 스크립트의 solver를 CLI에서 명시한다. 기본 예제 demo_v15c.json 및 옛 검증 문서는 변경 전 입력/설명이다. 전체 검증을 다시 실행하면 private 평가 로그·요약을 재생성한다. `--resume`은 기록한 소스/README 해시가 같을 때만 중단 실행을 이어간다(최종 결과를 추가한 현재 README는 최초 평가 manifest와 다르다). 당시 README와 해시는 DESIGN_FROZEN.md 및 private/avoidance/evaluation_manifest_initial.json에 보존했다.

### 실행 중 수정 이력

- 구현 중 UI 함수 인자와 정지 시 실제 적용 가속도 기록을 정정한 뒤 단위시험을 통과시켰다.
- 반복 평가 시작 후 NumPy bool JSON 직렬화 오류를 수정했다. 제어/플랜트/탐지/시나리오 수식에는 변경 없음. 완료 요약이 없는 run만 동일 조건으로 재실행했다. 최초/수정 manifest와 오류 로그 보존.
- 집계 보고서에 도로 이탈·종료 정지·저속 조향 한계를 추가하고 표 안의 기호를 escape했다. 임계값/실행 결과는 변경하지 않았다.

### Git 명령 (실행하지 않음)

아래는 이번 작업 파일만 명시하는 명령이다. UAH 원본·파생 데이터, private/, cache/, *.pt, logs/, 기존 다른 작업의 docs/는 포함하지 않는다. 그림/집계는 이 과제의 합성 시뮬레이션 결과다.

```bash
git add -- demo_v15c/README.md demo_v15c/DESIGN_FROZEN.md demo_v15c/AVOIDANCE_REPORT.md \
  demo_v15c/DIAGNOSIS_PHASE1.md demo_v15c/README_preavoid.md \
  demo_v15c/app.py demo_v15c/sim.py demo_v15c/ego_controller.py \
  demo_v15c/app_preavoid.py demo_v15c/sim_preavoid.py demo_v15c/ego_controller_preavoid.py \
  demo_v15c/vehicle.py demo_v15c/planning_demo.py demo_v15c/avoidance_demo.json \
  demo_v15c/test_avoidance.py demo_v15c/test_render_avoidance.py \
  demo_v15c/validate_avoidance.py demo_v15c/report_avoidance.py \
  demo_v15c/figures/avoidance_1_trajectories.png demo_v15c/figures/avoidance_2_timeseries.png \
  demo_v15c/figures/avoidance_3_outcomes.png demo_v15c/figures/avoidance_4_before_after.png \
  demo_v15c/results/avoidance/summary.json demo_v15c/results/avoidance/scenario_summary.csv \
  demo_v15c/results/avoidance_astra_before_fix/summary.json demo_v15c/results/avoidance_astra_before_fix/scenario_summary.csv \
  demo_v15c/results/avoidance_astra_before_fix/AVOIDANCE_REPORT_astra.md \
  demo_v15c/results/avoidance_fix1_ego_ramp005/summary.json demo_v15c/results/avoidance_fix1_ego_ramp005/scenario_summary.csv \
  demo_v15c/results/avoidance_fix1_ego_ramp005/AVOIDANCE_REPORT_fix1.md
git diff --cached --name-only
git diff --cached --stat
git commit -m "demo_v15c: add shared vehicle plant and avoidance experiments; document tracking failures"
```
