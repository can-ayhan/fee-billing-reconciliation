"""Turn the text of a fee schedule into tariff rows.

This is the only place a language model is used, and all it does is read: its
answer is forced to be a call of one tool whose schema fixes the shape of a
row. The rows are validated and checked for gaps and overlaps here; what fails
is sent back with the reasons for another attempt. Nothing it returns is used
for pricing until it has passed the review queue (review.py). Fees are always
calculated by the SQL functions.

The model is an open model served through the Groq API, whose free tier is
enough for documents of this size.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from feerecon.config import ConfigError, get_settings
from feerecon.extract.checks import Problem, check_rows
from feerecon.extract.schema import TOOL, TOOL_NAME, Extraction, TariffRow

MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """\
You read fee schedules of a securities-services provider and record them as tariff rows \
with the record_tariff_rows tool. The rows are loaded into a pricing table, so they have to say exactly \
what the document says.

Services and their codes:
{services}

How to record:
- One row per band. A fee with a single rate is one band from 0 with no upper limit.
- Bands are continuous: a band ends where the next one starts. "first 500, next 1,500" means 0 to 500 \
and 500 to 2,000. "501 to 2,000" also means the band from 500 to 2,000.
- Write amounts out in full: EUR 10 million is 10000000.
- graduated when each band is charged at its own rate; slab when the rate of the band reached applies \
to the whole volume.
- effective_from is the date the schedule comes into force. Leave effective_to null unless the document gives an end date.
- source_quote is the line of the document the row comes from, copied word for word.
- confidence is your own estimate that the row is exactly right. Lower it when the wording is ambiguous, \
when a rate depends on something the document leaves open, or when you had to infer a limit.
- Do not invent a rate, a band or a service. If the document leaves something open, say so in notes.

Call the tool once, with all rows. If your rows are sent back with problems, \
re-read the document and call the tool again with the complete corrected set."""


@dataclass
class ExtractionResult:
    rows: list[TariffRow] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)   # what was still wrong after the last attempt
    attempts: int = 0
    notes: str = ""
    finish_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return bool(self.rows) and not any(p.blocking for p in self.problems)


def _client():
    import groq

    return groq.Groq()      # reads GROQ_API_KEY


def _feedback(problems: list[Problem]) -> str:
    lines = "\n".join(f"- {p.message}" for p in problems)
    return ("The rows were not accepted:\n" + lines +
            "\nRe-read the document and call record_tariff_rows again with the complete corrected set.")


def _rejected_by_api(exc: Exception) -> str | None:
    """Groq answers 400 'tool_use_failed' when the model's call does not fit the tool schema."""
    body = getattr(exc, "body", None)
    if getattr(exc, "status_code", None) == 400 and "tool_use_failed" in f"{body} {exc}":
        error = body.get("error", body) if isinstance(body, dict) else {}
        return str(error.get("message") or exc)
    return None


def extract_tariff(document: str, services: list[dict], client=None, model: str | None = None,
                   max_attempts: int = MAX_ATTEMPTS) -> ExtractionResult:
    """Run the extract, check, retry loop. `services` are rows of pricing.v_service."""
    model = model or get_settings().extract_model
    if not model:
        raise ConfigError("Set FEERECON_MODEL to the Groq model that should read the document (see .env.example).")
    client = client or _client()

    known = {s["service_code"] for s in services}
    listing = "\n".join(f"- {s['service_code']}: {s['service_name']}" for s in services)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(services=listing)},
        {"role": "user",
         "content": f"<document>\n{document}\n</document>\n\nRecord every fee in this document with the {TOOL_NAME} tool."},
    ]
    result = ExtractionResult()

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=[TOOL],
                tool_choice={"type": "function", "function": {"name": TOOL_NAME}},   # the answer must be this call
                temperature=0,
            )
        except Exception as exc:
            reason = _rejected_by_api(exc)
            if reason is None:
                raise
            result.rows, result.notes = [], ""
            result.problems = [Problem("INVALID_CALL", None, None, f"the call did not fit the tool schema: {reason}")]
            messages.append({"role": "user", "content": _feedback(result.problems)})
            continue

        choice = response.choices[0]
        result.finish_reason = choice.finish_reason
        calls = choice.message.tool_calls or []
        if not calls:
            messages.append({"role": "assistant", "content": choice.message.content or ""})
            messages.append({"role": "user", "content": f"Answer by calling the {TOOL_NAME} tool with the rows."})
            result.problems = [Problem("NO_TOOL_CALL", None, None, "the model answered in text instead of rows")]
            continue

        messages.append({
            "role": "assistant",
            "content": choice.message.content or "",
            "tool_calls": [{"id": call.id, "type": "function",
                            "function": {"name": call.function.name, "arguments": call.function.arguments}}
                           for call in calls],
        })
        try:
            extraction = Extraction.model_validate(json.loads(calls[0].function.arguments))
        except (json.JSONDecodeError, ValidationError) as exc:
            # rows of an earlier attempt belong to a rejected answer, do not keep them
            result.rows, result.notes = [], ""
            errors = exc.errors() if isinstance(exc, ValidationError) else [{"loc": ("arguments",), "msg": str(exc)}]
            result.problems = [
                Problem("INVALID_ROW", None, None, f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}")
                for error in errors
            ]
        else:
            result.rows, result.notes = extraction.rows, extraction.notes
            result.problems = check_rows(extraction.rows, known, document)

        blocking = [p for p in result.problems if p.blocking]
        if not blocking:
            return result

        # every tool call needs a tool message in reply
        messages.append({"role": "tool", "tool_call_id": calls[0].id, "content": _feedback(blocking)})
        messages += [{"role": "tool", "tool_call_id": extra.id, "content": f"Call {TOOL_NAME} once, with all rows."}
                     for extra in calls[1:]]

    return result
