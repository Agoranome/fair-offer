# Copyright 2026 Kamkar Foundation, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FO-6, data, never instructions: a seller's own check on what it is about to send.

A response carries data for the buying agent, never instructions aimed at it. Data from elsewhere (a scraped page,
a third party's text) can carry them without the seller meaning it. `scan` finds text that reads as an instruction
to an agent in every string of a response, matched as a model reads it: character references decoded,
compatibility forms folded, invisible characters dropped and look-alike letters made Latin. It is a set of fixed
patterns, not a model: it misses what they don't cover and is meant as a guard, not a proof.

The scanner is the Agoranome buyer guard's own (`fair_meter/instructions.py`, a byte-for-byte copy of
agoranome_policy/instructions.py, ruleset instructions-2), so a seller checks its response the way the buyer's guard
will read it. tests/test_instructions_copy.py fails when the copy is edited here and not upstream.
"""

from typing import Any, List, Tuple

from fair_meter.instructions import RULESET, SCANNER_PATTERNS, instruction_like, normalized

PATTERNS = SCANNER_PATTERNS
__all__ = ["InstructionFound", "PATTERNS", "RULESET", "guard", "normalized", "scan"]


class InstructionFound(ValueError):
    """A response holds text that reads as an instruction to the buying agent (FO-6)."""

    def __init__(self, found: List[Tuple[str, str]]):
        self.found = found
        super().__init__("text that reads as an instruction to an agent at " + ", ".join(p for p, _ in found[:5]))


def _strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(str(key), f"{path}.{key}#key")
            yield from _strings(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            yield from _strings(item, f"{path}[{i}]")


def scan(value: Any) -> List[Tuple[str, str]]:
    """(where, phrase) for each instruction-like phrase in the strings of a JSON value, keys included."""
    return [(where, phrase) for where, text in _strings(value, "$") for phrase in instruction_like(text)]


def guard(value: Any) -> Any:
    """The value unchanged when it holds no instruction-like text, else InstructionFound. Call it on a result before
    it is sent and settled: FO-2 says a call that fails is not charged."""
    found = scan(value)
    if found:
        raise InstructionFound(found)
    return value
