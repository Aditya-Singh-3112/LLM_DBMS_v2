"""
Natural-language-to-SQL evaluation against the running stack.

    python -m scripts.eval_agent                       # API at http://localhost:8000
    python -m scripts.eval_agent --api-url http://... --min-accuracy 0.8 --report out.json

Registers a throwaway user, creates a database seeded from evals/data/*.csv,
and asks every question in evals/nl2sql_cases.json through /ask (so the real
LLM, the MCP server and its tools are all exercised). A case passes when the
rows the agent's final query returned equal the rows of the case's
expected_sql: as a multiset, or in order when "ordered" is true. Numbers are
compared with a small tolerance and column names are ignored, so different
but equivalent SQL counts as correct (execution accuracy). Extra columns in
the agent's result are allowed unless --strict is given, as long as some of
its columns reproduce the expected ones.

Model rate limits (503) are retried with backoff rather than scored.

This calls the model and costs tokens, so it is not part of CI. Exits 1 when
accuracy is below --min-accuracy.
"""
import argparse
import itertools
import json
import sys
import time
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "evals" / "data"
CASES_FILE = ROOT / "evals" / "nl2sql_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--cases", type=Path, default=CASES_FILE)
    parser.add_argument("--only", nargs="*", help="run only these case ids")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    parser.add_argument("--report", type=Path, help="write per-case results as JSON")
    parser.add_argument("--strict", action="store_true", help="fail answers that return extra columns")
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]

    with httpx.Client(base_url=args.api_url, timeout=180) as http:
        headers = _sign_up(http)
        database_id = http.post("/databases", json={"name": "nl2sql_eval"}, headers=headers).json()["id"]
        try:
            _seed(http, headers, database_id)
            results = [_run_case(http, headers, database_id, case, args.strict) for case in cases]
        finally:
            http.delete(f"/databases/{database_id}", headers=headers)

    passed = sum(r["passed"] for r in results)
    accuracy = passed / len(results) if results else 0.0
    width = max((len(r["id"]) for r in results), default=10)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['id']:<{width}}  {r['seconds']:5.1f}s  {r.get('error') or ''}")
        if not r["passed"] and r.get("sql"):
            print(f"      agent SQL: {r['sql']}")
    print(f"\nExecution accuracy: {passed}/{len(results)} = {accuracy:.0%}")

    if args.report:
        args.report.write_text(json.dumps({"accuracy": accuracy, "results": results}, indent=2, default=str))
    return 0 if accuracy >= args.min_accuracy else 1


def _sign_up(http: httpx.Client) -> dict:
    email = f"eval-{uuid.uuid4().hex[:10]}@example.com"
    password = uuid.uuid4().hex
    http.post("/auth/register", json={"email": email, "password": password}).raise_for_status()
    response = http.post("/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed(http: httpx.Client, headers: dict, database_id: str) -> None:
    for csv_path in sorted(DATA_DIR.glob("*.csv")):
        response = http.post(
            f"/databases/{database_id}/tables/import",
            files={"file": (csv_path.name, csv_path.read_bytes())},
            headers=headers,
        )
        response.raise_for_status()


def _ask(http: httpx.Client, headers: dict, database_id: str, question: str) -> httpx.Response:
    for attempt in range(4):
        response = http.post(f"/databases/{database_id}/ask", json={"query": question}, headers=headers)
        if response.status_code != 503 or "rate-limited" not in response.text or attempt == 3:
            return response
        time.sleep(20 * (attempt + 1))
    return response


def _run_case(http: httpx.Client, headers: dict, database_id: str, case: dict, strict: bool) -> dict:
    result = {"id": case["id"], "question": case["question"], "passed": False}
    started = time.monotonic()
    try:
        expected = http.post(
            f"/databases/{database_id}/sql", json={"sql": case["expected_sql"]}, headers=headers
        )
        expected.raise_for_status()
        expected_rows = expected.json()["rows"]

        response = _ask(http, headers, database_id, case["question"])
        if response.status_code != 200:
            result["error"] = f"/ask returned {response.status_code}: {response.text[:200]}"
            return result
        body = response.json()
        result["sql"] = body.get("sql")
        result["answer"] = body.get("answer")
        actual_rows = (body.get("result") or {}).get("rows")
        if actual_rows is None:
            result["error"] = "the agent ran no query that returned rows"
            return result

        result["passed"] = _same_rows(
            expected_rows, actual_rows, ordered=case.get("ordered", False), allow_extra_columns=not strict
        )
        if not result["passed"]:
            result["error"] = f"expected {expected_rows[:5]}, got {actual_rows[:5]}"
        return result
    except Exception as e:  # report and keep going
        result["error"] = repr(e)
        return result
    finally:
        result["seconds"] = time.monotonic() - started


def _same_rows(expected: list[list], actual: list[list], ordered: bool, allow_extra_columns: bool = True) -> bool:
    expected_norm = [tuple(_normalize(v) for v in row) for row in expected]
    actual_norm = [tuple(_normalize(v) for v in row) for row in actual]
    if len(expected_norm) != len(actual_norm):
        return False
    if not expected_norm:
        return True

    def equal(a: list[tuple], b: list[tuple]) -> bool:
        return a == b if ordered else Counter(a) == Counter(b)

    width, actual_width = len(expected_norm[0]), len(actual_norm[0])
    if width == actual_width and equal(expected_norm, actual_norm):
        return True
    if not allow_extra_columns or actual_width < width:
        return False
    # Some choice of the agent's columns, in some order, reproduces the expected ones.
    for columns in itertools.permutations(range(actual_width), width):
        if equal(expected_norm, [tuple(row[i] for i in columns) for row in actual_norm]):
            return True
    return False


def _normalize(value):
    """Make 3, 3.0, '3.00' and Decimal('3') compare equal; round to 4 places."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value).strip()
    return str(round(number, 4).normalize()) if number.is_finite() else str(value)


if __name__ == "__main__":
    sys.exit(main())
