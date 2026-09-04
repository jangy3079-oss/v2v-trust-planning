"""단일홉 V2V 압축정보 기반 이상운전 탐지 및 신뢰가중 협력 회피 — 모듈 스켈레톤.

각 서브모듈은 프로젝트 문서 `알고리즘_수식_정식화.md`의 절 번호에 대응한다.
    messaging  -> 2절 (송신: 원본 정보만 브로드캐스트, 단일홉 채널)
    fusion     -> 3절 (수신 및 로컬 융합)
    anomaly    -> 4절 (이상탐지: 다음스텝 예측 + CUSUM 누적통계 -> 신뢰도)
    attention  -> 5절 (신뢰가중 어텐션 융합)
    planning   -> 6절 (신뢰도 기반 강건 회피 계획)
    agent      -> 7절 (Algorithm 1 오케스트레이션)
    datasets   -> 검증용 데이터 로더(UAH-DriveSet) 및 스모크테스트용 합성 시퀀스
"""

from .config import Config

__all__ = ["Config"]
