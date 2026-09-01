from __future__ import annotations


def levenshtein_distance(left: list[str] | str, right: list[str] | str) -> int:
    """Compute Levenshtein edit distance over strings or token lists."""

    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_item in enumerate(left, start=1):
        current = [i]
        for j, right_item in enumerate(right, start=1):
            substitution = previous[j - 1] + (left_item != right_item)
            insertion = current[j - 1] + 1
            deletion = previous[j] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def edit_similarity(left: list[str] | str, right: list[str] | str) -> float:
    """Normalized edit similarity in [0, 1]."""

    denominator = max(len(left), len(right))
    if denominator == 0:
        return 1.0
    distance = levenshtein_distance(left, right)
    return max(0.0, 1.0 - distance / denominator)


def best_one_to_one_matching(scores: list[list[float]]) -> list[tuple[int, int, float]]:
    """Return max-score one-to-one matches for a rectangular score matrix."""

    if not scores or not scores[0]:
        return []

    pred_count = len(scores)
    gt_count = len(scores[0])
    if pred_count <= gt_count:
        assignment = _hungarian_min_cost([[-score for score in row] for row in scores])
        return [
            (pred_idx, gt_idx, scores[pred_idx][gt_idx])
            for pred_idx, gt_idx in enumerate(assignment)
        ]

    transposed = [
        [scores[pred_idx][gt_idx] for pred_idx in range(pred_count)]
        for gt_idx in range(gt_count)
    ]
    assignment = _hungarian_min_cost([[-score for score in row] for row in transposed])
    matches = [
        (pred_idx, gt_idx, scores[pred_idx][gt_idx])
        for gt_idx, pred_idx in enumerate(assignment)
    ]
    return sorted(matches)


def best_partial_one_to_one_matching(
    scores: list[list[float]],
    *,
    minimum_score: float,
) -> list[tuple[int, int, float]]:
    """Return a maximum-weight partial assignment above a score floor.

    Dummy rows and columns allow either side to remain unmatched.  Ineligible
    real pairs receive a negative weight, while dummy assignments have weight
    zero, so a low-similarity pair is never forced merely because the two
    inventories have the same size.
    """

    if not scores or not scores[0]:
        return []

    pred_count = len(scores)
    gt_count = len(scores[0])
    if any(len(row) != gt_count for row in scores):
        raise ValueError("score matrix must be rectangular")

    size = pred_count + gt_count
    lowest_score = min(min(row) for row in scores)
    forbidden_score = min(lowest_score, 0.0, minimum_score) - 1.0
    augmented_scores = [
        [
            (
                scores[pred_idx][gt_idx]
                if pred_idx < pred_count
                and gt_idx < gt_count
                and scores[pred_idx][gt_idx] >= minimum_score
                else forbidden_score
                if pred_idx < pred_count and gt_idx < gt_count
                else 0.0
            )
            for gt_idx in range(size)
        ]
        for pred_idx in range(size)
    ]
    augmented_matches = best_one_to_one_matching(augmented_scores)
    return sorted(
        (
            pred_idx,
            gt_idx,
            scores[pred_idx][gt_idx],
        )
        for pred_idx, gt_idx, _ in augmented_matches
        if pred_idx < pred_count
        and gt_idx < gt_count
        and scores[pred_idx][gt_idx] >= minimum_score
    )


def _hungarian_min_cost(costs: list[list[float]]) -> list[int]:
    """Hungarian algorithm for rows <= columns. Returns assigned column per row."""

    row_count = len(costs)
    col_count = len(costs[0])
    if row_count > col_count:
        raise ValueError("Hungarian implementation requires rows <= columns")

    u = [0.0] * (row_count + 1)
    v = [0.0] * (col_count + 1)
    p = [0] * (col_count + 1)
    way = [0] * (col_count + 1)

    for row in range(1, row_count + 1):
        p[0] = row
        col_0 = 0
        minv = [float("inf")] * (col_count + 1)
        used = [False] * (col_count + 1)
        while True:
            used[col_0] = True
            row_0 = p[col_0]
            delta = float("inf")
            col_1 = 0
            for col in range(1, col_count + 1):
                if used[col]:
                    continue
                current = costs[row_0 - 1][col - 1] - u[row_0] - v[col]
                if current < minv[col]:
                    minv[col] = current
                    way[col] = col_0
                if minv[col] < delta:
                    delta = minv[col]
                    col_1 = col
            for col in range(col_count + 1):
                if used[col]:
                    u[p[col]] += delta
                    v[col] -= delta
                else:
                    minv[col] -= delta
            col_0 = col_1
            if p[col_0] == 0:
                break

        while True:
            col_1 = way[col_0]
            p[col_0] = p[col_1]
            col_0 = col_1
            if col_0 == 0:
                break

    assignment = [-1] * row_count
    for col in range(1, col_count + 1):
        if p[col] != 0:
            assignment[p[col] - 1] = col - 1
    return assignment
