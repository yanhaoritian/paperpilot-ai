from evals.run_memory_eval import run_memory_eval


def test_memory_query_rewrite_gold_cases():
    results = run_memory_eval()
    assert results
    assert all(result.passed for result in results), results
