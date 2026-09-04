# v2v_trust_planning — 단일홉 V2V 압축정보 기반 이상운전 탐지 및 신뢰가중 협력 회피

단일홉 V2V 압축정보 기반 이상운전 탐지 및 신뢰가중 협력 회피 알고리즘의 구현체.
수식/의사코드 원문은 프로젝트 문서 `알고리즘_수식_정식화.md`를 참고. 각 소스 파일 상단
docstring에 대응 절 번호를 명시해뒀다.

## 구조

```
src/v2v_trust_planning/
├── config.py      # 1절, 전역 하이퍼파라미터
├── messaging.py   # 2절, BSMMessage + 단일홉 채널 (재전달 없음)
├── fusion.py      # 3절, 로컬 융합 g(.) — 안개 시 센서/V2V 가중치 자동 재조정
├── anomaly.py     # 4절, 다음스텝 예측기 f_phi(GRU) + CUSUM 누적통계 -> 신뢰도 tau
├── attention.py   # 5절, 신뢰가중 어텐션 융합 (tau의 이중역할 ②)
├── planning.py    # 6절, 동적 안전마진 + TrustAwareNMPC(CasADi Opti + IPOPT,
│                  #      Kinematic Bicycle Model, soft collision constraint)
├── agent.py       # 7절, Algorithm 1 전체 오케스트레이션(EgoVehicleAgent.step)
├── datasets.py    # UAH-DriveSet 로더 + 스모크테스트용 합성 정상/난폭/졸음/미숙운전 시퀀스
├── scenarios.py   # 벤치마크용 시나리오 생성기 (4대 시나리오: reckless/drowsy/novice/fog)
└── baselines.py   # 비교군: Zhang et al.(closed-set 분류기), CARMEN+ 스타일
                   #         즉시임계값 배제, Fixed-Margin(신뢰도 미반영)

scripts/
├── train_predictor.py              # 정상데이터만으로 f_phi 자기지도 학습
├── smoke_test.py                    # 전체 파이프라인 배선 확인 (졸음 시나리오 tau 변화)
├── calibrate_nu.py                  # CUSUM nu(정상 허용 미세오차) 재산정
├── check_novice_scenario.py         # 미숙운전 시나리오 tau 변화 확인 (축1 open-set 배선 증거)
├── diagnose_cusum_onset.py          # 문제 A 진단: CUSUM sub-threshold 탐지 실험(A-1/A-2)
├── diagnose_jerk_trace.py           # 문제 B 진단: 저크 급증 원인(액추에이터 포화) 분석
├── diagnose_ab_and_seeds.py         # 슬랙 가중치 리밸런싱 30-seed A/B 검증
├── compare_baseline_vs_proposed.py  # Proposed vs 베이스라인 비교 실행기
└── tune_and_benchmark.py            # 논문용 최종 벤치마크(5개 모델 × 4개 시나리오 × 30-seed)

tests/
├── test_pipeline_smoke.py
└── test_baselines_and_scenarios.py

reports/
└── <YYYY-Www>.md   # 주간 진행상황 보고
```

## 검증 상태 (2026-09-04 기준, 9차 검증 완료)

Antigravity(다른 IDE)에서 진행한 실험 결과를 이 프로젝트의 Claude 세션에서 매 라운드
독립적으로 재검증하는 절차를 거쳤다(상세 이력은 프로젝트 문서
`NMPC_벤치마크_검증_평가.md`의 1차~9차 검증 참고).

- **CUSUM 누적통계 기반 이상탐지의 우위 (§4.2 핵심 주장) — 확정.** 목표 오차
  e_t≈0.0153(순간임계값 θ_inst=0.020 미만, CUSUM 임계값 nu=0.011 초과), 지속시간 8.9초
  조건에서 순간임계값 기반 탐지율 0%(0/30) vs CUSUM 누적 탐지율(τ≤0.7/0.8/0.9) 100%(30/30).
  raw 실행 로그(seed 3000~3029)까지 직접 검산 완료. 단, τ≤0.5/0.6 기준은 이 지속시간
  내에서 미도달 — 논문에는 "τ≤0.7 이상 기준 100% 탐지"로 스코프를 정확히 한정해 서술할 것.
- **저크(jerk) 급증 트레이드오프 — 확정, 해결.** `weight_slack: 10000.0→1000.0`,
  `weight_delta_control: 2.5→10.0`로 리밸런싱하여 4개 시나리오 전체에서 안전마진(min_dist)
  희생 없이 저크 63~81% 감소를 30-seed A/B로 확인·채택(`config.py`에 반영 완료).
- **신뢰도의 계획단계 통합 (축3) — 실증 완료.** placeholder였던 MPC 솔버를 CasADi Opti +
  IPOPT 기반 `TrustAwareNMPC`로 교체 완료. 신뢰도 tau 하락이 실제 제어 출력(회피 여유거리
  확대)으로 이어짐을 Proposed vs Fixed-Margin의 min_dist 격차(14.5m vs 5.6~5.7m)로 확인.

## 남은 TODO

- `attention.default_relevance()` — 상수 1.0 반환. 실제로는 상대거리/차선 등을 반영하는
  학습 가능한 `h_psi`로 교체할 것.
- `datasets.load_uah_driveset_session()` — 컬럼 인덱스는 공식 reader 기준으로 확인했으나,
  실제 다운로드한 폴더 구조에 맞춰 경로 탐색은 조정 필요. 정상데이터를 synthetic →
  UAH-DriveSet/highD 등 실데이터로 교체 후 nu 재산정.
- `agent.EgoVehicleAgent.step()`의 `rel_speed_proxy` — 상대속도를 가속도 크기로 근사한
  임시 대리값. 실제로는 GPS 기반 상대속도를 따로 계산해 넣을 것.
- 미숙운전 시나리오를 CARLA/SUMO 정식 시뮬레이션으로 재구성 (현재는 수동 조정된 synthetic).

## 실행

```bash
pip install -r requirements.txt
PYTHONPATH=src python scripts/train_predictor.py         # 정상데이터만으로 f_phi 사전학습
PYTHONPATH=src python scripts/smoke_test.py                # 졸음 시나리오에서 tau 변화 확인
PYTHONPATH=src python scripts/tune_and_benchmark.py        # 논문용 최종 벤치마크 실행
PYTHONPATH=src pytest tests/                                # 배선 확인 테스트
```

## 설계 근거 요약

- **단일홉만 사용**: 멀티홉 릴레이는 이중계산(double-counting) 문제 때문에 폐기. 안개 시에도
  단일홉 통신범위(~300m)가 광학센서 가시거리보다 훨씬 넓어 충분.
- **원본 정보만 브로드캐스트**: 각 차량은 자기 압축 운동정보(가속도+헤딩변화율)와, 선택적으로
  자신이 직접 관측한 이웃 신뢰도 판단만 내보낸다. 받은 정보를 그대로 릴레이하지 않는다.
- **신뢰도의 이중 역할**: 같은 값 tau가 (1) 이상운전 탐지 결과이자 (2) 어텐션 융합 가중치로
  동시에 쓰인다.
- **재구성이 아닌 예측 기반 이상탐지**: 오토인코더 재구성오차는 이상치도 우연히 낮은 오차로
  나올 수 있다는 신뢰성 문제가 보고되어 있어, 다음스텝 예측 오차를 사용. 정상데이터만으로
  자기지도(self-supervised) 학습 가능(이상치 라벨 불필요). 예측기는 경량 GRU(hidden_dim=32,
  1층, 약 3.6K 파라미터) — 이웃 차량마다 매 스텝 실시간 추론이 가능하도록 최소 용량으로 설계.
- **CUSUM 누적통계**: 순간 오차 단일 판정은 졸음운전처럼 서서히 누적되는 이상을 놓칠 수
  있어(deceptive OOD), 누적 통계량으로 급성/만성 이상을 모두 포착.
- **원인 무관(open-set) 일반화**: f_phi는 이상치 라벨 없이 정상데이터만으로 학습되므로
  "특정 위험행동 라벨"이 아니라 "정상 전이패턴으로부터의 이탈"을 잰다. 그 결과 음주/졸음뿐
  아니라 학습 시 전혀 정의하지 않은 미숙운전(초보운전)까지 같은 메커니즘으로 잡아냄.
