from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.conversation_memory import (
    _preserves_question_intent,
    rewrite_retrieval_query,
)

CASES_PATH = Path(__file__).with_name("memory_gold.json")


@dataclass(frozen=True)
class MemoryEvalResult:
    case_id: str
    passed: bool
    rewritten: str
    missing_terms: tuple[str, ...]
    intent_preserved: bool


def run_memory_eval() -> list[MemoryEvalResult]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    results: list[MemoryEvalResult] = []
    for case in cases:
        rewritten, _changed = rewrite_retrieval_query(
            str(case["question"]),
            history=list(case.get("history") or []),
            summary=str(case.get("summary") or ""),
        )
        required = tuple(str(term) for term in case.get("required_terms") or [])
        missing = tuple(
            term
            for term in required
            if term.lower() not in rewritten.lower()
        )
        intent_preserved = _preserves_question_intent(
            str(case["question"]),
            rewritten,
        )
        results.append(
            MemoryEvalResult(
                case_id=str(case["id"]),
                passed=not missing and intent_preserved,
                rewritten=rewritten,
                missing_terms=missing,
                intent_preserved=intent_preserved,
            )
        )
    return results


def main() -> None:
    results = run_memory_eval()
    passed = sum(1 for result in results if result.passed)
    for result in results:
        print(
            f"[{result.case_id}] passed={result.passed} "
            f"intent={result.intent_preserved} "
            f"missing={list(result.missing_terms)} "
            f"query={result.rewritten}"
        )
    print(f"Memory query rewrite: {passed}/{len(results)} passed")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
