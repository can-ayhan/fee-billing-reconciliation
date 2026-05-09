"""What an extracted tariff row has to look like.

Two layers enforce it. The model's answer is forced to be a call of one tool
whose JSON schema describes a row, so it cannot answer in any other shape.
Pydantic then validates the values the schema cannot express (positive rates,
bands that end after they start, real dates).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

TOOL_NAME = "record_tariff_rows"


class TariffRow(BaseModel):
    service_code: str
    basis: Literal["count", "amount"]
    band_method: Literal["graduated", "slab"]
    unit: Literal["per_item", "bps", "bps_annual"]
    tier_from: Decimal = Field(ge=0)
    tier_to: Decimal | None
    rate: Decimal = Field(gt=0)
    effective_from: dt.date
    effective_to: dt.date | None
    confidence: float = Field(ge=0, le=1)
    source_quote: str = Field(min_length=3)

    @model_validator(mode="after")
    def _ranges(self) -> "TariffRow":
        if self.tier_to is not None and self.tier_to <= self.tier_from:
            raise ValueError(f"band ends at {self.tier_to}, which is not above its start {self.tier_from}")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(f"valid until {self.effective_to}, before it starts on {self.effective_from}")
        return self

    @property
    def version(self) -> tuple[str, dt.date]:
        return self.service_code, self.effective_from


class Extraction(BaseModel):
    rows: list[TariffRow]
    notes: str = ""


_ROW_PROPERTIES = {
    "service_code": {"type": "string", "description": "One of the service codes listed in the instructions."},
    "basis": {"type": "string", "enum": ["count", "amount"],
              "description": "What the bands measure: number of items, or a value in EUR."},
    "band_method": {"type": "string", "enum": ["graduated", "slab"],
                    "description": "graduated: each band charges its own rate for the part of the volume inside it. "
                                   "slab: the rate of the band reached applies to the whole volume."},
    "unit": {"type": "string", "enum": ["per_item", "bps", "bps_annual"],
             "description": "per_item: EUR per item. bps: basis points of the value. bps_annual: basis points per annum."},
    "tier_from": {"type": "number", "description": "Lower limit of the band, 0 for the first band."},
    "tier_to": {"type": ["number", "null"], "description": "Upper limit of the band, null when there is none."},
    "rate": {"type": "number", "description": "The rate exactly as the document states it, in the unit above."},
    "effective_from": {"type": "string", "description": "First day the rate applies, YYYY-MM-DD."},
    "effective_to": {"type": ["string", "null"], "description": "Last day the rate applies, null if the document gives none."},
    "confidence": {"type": "number", "description": "0 to 1. How sure you are that this row is exactly what the document says."},
    "source_quote": {"type": "string", "description": "The words of the document this row was read from, copied verbatim."},
}

TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Record the fee schedule rows read from the document. Call it once with every band of every service. "
            "One row per band. Record only what the document states; never fill in a rate it does not give."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": _ROW_PROPERTIES,
                        "required": list(_ROW_PROPERTIES),
                        "additionalProperties": False,
                    },
                },
                "notes": {"type": "string",
                          "description": "Anything in the document that affects pricing and does not fit a row, or an empty string."},
            },
            "required": ["rows", "notes"],
            "additionalProperties": False,
        },
    },
}
