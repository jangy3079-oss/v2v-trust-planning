"""5절: 신뢰가중 어텐션 융합 (신뢰도의 이중 역할 ②).

4절에서 산출된 신뢰도 tau_j를 어텐션 가중치로 재사용해 주변 장면(scene) 표현을 만든다.
"""

from typing import Callable, Dict, Optional, Tuple

import numpy as np


def default_relevance(x_j: np.ndarray) -> float:
    """h_psi의 기본(휴리스틱) 구현.

    스켈레톤 단계에서는 상수 1.0을 반환해 신뢰도만으로 가중치가 결정되게 한다.
    실제 학습 단계에서는 상대거리·상대차선 등을 반영하는 nn.Module로 교체할 것
    (거리가 가까울수록/같은 차선일수록 관련도가 높다는 식의 사전지식을 반영).
    """
    return 1.0


def trust_weighted_attention(
    features: Dict[str, np.ndarray],
    trust: Dict[str, float],
    relevance_fn: Callable[[np.ndarray], float] = default_relevance,
) -> Tuple[Dict[str, float], Optional[np.ndarray]]:
    """alpha_j = softmax_j(tau_j * h_psi(x_j)), z_i = sum_j alpha_j * x_j.

    Args:
        features: {neighbor_id: x_j^(i)(t)} (3절 local_fusion 결과).
        trust: {neighbor_id: tau_j^(i)(t)} (4절 결과).
        relevance_fn: h_psi. 학습되지 않은 스켈레톤 단계에서는 default_relevance 사용.

    Returns:
        alpha: {neighbor_id: 가중치}.
        z: 융합된 장면 표현, shape (feature_dim,). 이웃이 없으면 None.
    """
    if not features:
        return {}, None

    ids = list(features.keys())
    scores = np.array([trust[j] * relevance_fn(features[j]) for j in ids])
    scores = scores - scores.max()  # 수치안정화(overflow 방지)
    exp_scores = np.exp(scores)
    weights = exp_scores / exp_scores.sum()

    alpha = {j: float(w) for j, w in zip(ids, weights)}
    z = sum(alpha[j] * features[j] for j in ids)
    return alpha, z
