# v2v_trust_planning — 모듈 스켈레톤

단일홉 V2V 압축정보 기반 이상운전 탐지 및 신뢰가중 협력 회피 알고리즘의 코드 골격.
수식/의사코드 원문은 프로젝트 문서 `알고리즘_수식_정식화.md`를 참고. 각 소스 파일 상단
docstring에 대응 절 번호를 명시해뒀다.

## 구조

```
src/v2v_trust_planning/
├── config.py      # 1절, 전역 하이퍼파라미터
├── messaging.py   # 2절, BSMMessage + 단일홉 채널 (재전달 없음)
├── fusion.py      # 3절, 로컬 융합 g(.) — 안개 시 센서/V2V 가중치 자동 재조정
├── anomaly.py     # 4절, 다음스텝 예측기 f_phi + CUSUM 누적통계 -> 신뢰도 tau
├── attention.py   # 5절, 신뢰가중 어텐션 융합 (tau의 이중역할 ②)
├── planning.py    # 6절, 동적 안전마진 + MPC 목적함수 스켈레톤
├── agent.py       # 7절, Algorithm 1 전체 오케스트레이션(EgoVehicleAgent.step)
└── datasets.py    # UAH-DriveSet 로더 + 스모크테스트용 합성 정상/난폭/졸음 시퀀스

scripts/
├── train_predictor.py         # 정상데이터만으로 f_phi 자기지도 학습
├── smoke_test.py               # 전체 파이프라인 배선 확인 (졸음 시나리오에서 tau 변화 관찰)
├── calibrate_nu.py             # CUSUM nu(정상 허용 미세오차) 재산정
└── check_novice_scenario.py    # 미숙운전(초보운전) 시나리오에서 tau 변화 확인 (축1 open-set 주장의 배선 증거)

tests/
└── test_pipeline_smoke.py
```

## 지금 단계에서 스켈레톤인 부분 (TODO 명시)

- `planning.SafeMPCPlanner.solve()` — 수치미분 하강법으로 목적함수만 근사 최소화하는
  자리표시자. 실제 구현에서는 cvxpy/casadi/acados 등으로 교체하고 `d_safe` 제약을
  명시적 부등식으로 넣을 것.
- `attention.default_relevance()` — 상수 1.0 반환. 실제로는 상대거리/차선 등을 반영하는
  학습 가능한 `h_psi`로 교체할 것.
- `datasets.load_uah_driveset_session()` — 컬럼 인덱스는 공식 reader 기준으로 확인했으나,
  실제 다운로드한 폴더 구조에 맞춰 경로 탐색은 조정 필요.
- `agent.EgoVehicleAgent.step()`의 `rel_speed_proxy` — 상대속도를 가속도 크기로 근사한
  임시 대리값. 실제로는 GPS 기반 상대속도를 따로 계산해 넣을 것.

## 실행

```bash
pip install -r requirements.txt
PYTHONPATH=src python scripts/train_predictor.py   # 정상데이터만으로 f_phi 사전학습
PYTHONPATH=src python scripts/smoke_test.py          # 졸음 시나리오에서 tau 변화 확인
PYTHONPATH=src pytest tests/                          # 배선 확인 테스트
```

## 설계 근거 요약 (대화에서 확정된 결정들)

- **단일홉만 사용**: 멀티홉 릴레이는 이중계산(double-counting) 문제 때문에 폐기. 안개 시에도
  단일홉 통신범위(~300m)가 광학센서 가시거리보다 훨씬 넓어 충분.
- **원본 정보만 브로드캐스트**: 각 차량은 자기 압축 운동정보(가속도+헤딩변화율)와, 선택적으로
  자신이 직접 관측한 이웃 신뢰도 판단만 내보낸다. 받은 정보를 그대로 릴레이하지 않는다.
- **신뢰도의 이중 역할**: 같은 값 tau가 (1) 이상운전 탐지 결과이자 (2) 어텐션 융합 가중치로
  동시에 쓰인다.
- **재구성이 아닌 예측 기반 이상탐지**: 오토인코더 재구성오차는 이상치도 우연히 낮은 오차로
  나올 수 있다는 신뢰성 문제가 보고되어 있어, 다음스텝 예측 오차를 사용. 정상데이터만으로
  자기지도 학습 가능(이상치 라벨 불필요).
- **CUSUM 누적통계**: 순간 오차 단일 판정은 졸음운전처럼 서서히 누적되는 이상을 놓칠 수
  있어(deceptive OOD), 누적 통계량으로 급성/만성 이상을 모두 포착.
- **원인 무관(open-set) 일반화**: f_phi는 이상치 라벨 없이 정상데이터만으로 학습되므로
  "특정 위험행동 라벨"이 아니라 "정상 전이패턴으로부터의 이탈"을 잰다. 그 결과 음주/졸음뿐
  아니라 학습 시 전혀 정의하지 않은 미숙운전(초보운전)까지 같은 메커니즘으로 잡아냄
  (`scripts/check_novice_scenario.py`로 배선 확인 — 상세 근거는 프로젝트 문서
  `차별점_검증_및_관련연구.md` 축1 참고).
