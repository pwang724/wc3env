"""TypeSafe HTTP transport and response validation; no game or benchmark imports."""

from __future__ import annotations

import http.client
import json
import math
import time

MAX_CHOICES = 255


def choice_question(instructions, criteria):
    """Encode an offered menu; the caller owns its meaning and candidate selection."""
    if not 1 <= len(criteria) <= MAX_CHOICES:
        raise ValueError(f"Jev requires 1..{MAX_CHOICES} choices per question; received {len(criteria)}")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def evaluate(payload: dict, api_key: str) -> dict:
    for question in payload["questions"].values():
        if question["type"] == "choice":
            choice_question(question["instructions"], question["criteria"])
    # Fixed official host; HTTPSConnection does not follow redirects with credentials.
    connection = http.client.HTTPSConnection("api.typesafe.ai", timeout=30)
    started = time.perf_counter()
    try:
        connection.request(
            "POST",
            "/v1/systemone",
            json.dumps(payload).encode("utf-8"),
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8").replace(api_key, "[REDACTED]")
        record = {
            "status": response.status,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "request_id": response.getheader("x-typesafe-request-id"),
        }
        try:
            record["response"] = json.loads(body)
        except json.JSONDecodeError:
            record["response"] = body
        return record
    finally:
        connection.close()


def validate_choice_response(record):
    """Validate actionable answers, recording displayed-probability discrepancies.

    Always retain the API's returned choice. A 0.01 mismatch in displayed
    probabilities is a diagnostic, not a reason to discard the entire battle.
    """
    payload, response = record["request"], record["response"]

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(record["status"] == 200, f"TypeSafe HTTP {record['status']}: {response}")
    require(isinstance(response["model"], str), "Missing model identifier")
    require(set(response["answers"]) == set(payload["questions"]), "Answer IDs differ from question IDs")
    warnings = []
    for name, q in payload["questions"].items():
        answer = response["answers"][name]
        require(answer["type"] == q["type"] == "choice", f"{name}: wrong answer type")
        require(answer["choice"] in q["criteria"], f"{name}: unknown choice")
        probabilities = answer["probabilities"]
        require(set(probabilities) == set(q["criteria"]), f"{name}: unexpected probability keys")
        require(
            all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()),
            f"{name}: invalid probabilities",
        )
        require(abs(sum(probabilities.values()) - 1) < 0.011, f"{name}: probabilities do not sum to one")
        require(
            type(answer["confidence"]) in (int, float) and 0 <= answer["confidence"] <= 1, f"{name}: invalid confidence"
        )
        gap = max(probabilities.values()) - probabilities[answer["choice"]]
        if gap > 0.001:
            warnings.append(
                {"question": name, "returned_choice": answer["choice"], "displayed_probability_gap": round(gap, 6)}
            )
    require(
        all(type(response["usage"][f]) is int and response["usage"][f] >= 0 for f in ("input_tokens", "output_tokens")),
        "Invalid token usage",
    )
    record["probability_warnings"] = warnings
    record["contract_valid"] = True


def timed_call(payload, key, record):
    record["request"] = payload
    record["request_bytes"] = len(json.dumps(payload).encode())
    started = time.perf_counter()
    try:
        record.update(evaluate(payload, key))
    finally:
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    validate_choice_response(record)
    return record["response"]
