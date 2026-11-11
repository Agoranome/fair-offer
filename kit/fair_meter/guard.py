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

The patterns are Agoranome's, from its listing checks and the red-team of its own defences (November 2026).
"""

import html
import re
import unicodedata
from typing import Any, List, Tuple

PATTERNS = [
    r"\bignore (all|any|the|your|previous|prior|other)\b",
    r"\bdisregard\b",
    r"\b(always|must|should|you should) (choose|pick|select|prefer|call|use) (this|us|me|our)\b",
    r"\b(choose|pick|select|prefer) (this|us|me|our \w+) (over|instead|first|before)\b",
    r"\bdo not (choose|pick|select|use|call) (any|other|another|competing)\b",
    r"\bsystem prompt\b",
    r"\byou are (an?|the) (ai|assistant|agent|model|llm)\b",
    r"\b(attention|note to|dear) (ai|llm|agents?|assistants?|models?)\b",
    r"<\s*/?\s*(system|instructions?|prompt)\s*>",
    r"\[\s*/?\s*(system|inst)\s*\]",
    r"\bforget (all |any |the |your )?(previous|prior|earlier|above|preceding|other) "
    r"(instructions?|prompts?|rules?|messages?)\b",
    r"\boverride (all |any |your )(previous |prior |other )?(instructions?|rules?|system prompt)\b",
    r"\bnew instructions?\s*:",
    r"<\|\s*(im_start|im_end|system|assistant|endoftext)\s*\|>",
    r"\bthe only (tool|service|api|option|seller) (you|agents?|models?) (should|must|may) (call|use|pick|choose|"
    r"select)\b",
]
_RE = re.compile("|".join(PATTERNS), re.IGNORECASE)
_CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "А": "A", "В": "B",
    "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "ο": "o", "Ο": "O",
    "α": "a", "ι": "i", "Ι": "I", "ν": "v", "ρ": "p", "Α": "A", "Β": "B",
    "Ε": "E", "Η": "H", "Κ": "K", "Μ": "M", "Ν": "N", "Τ": "T", "Χ": "X",
    "Υ": "Y", "Ζ": "Z",
})
_SPACES = re.compile(r"\s+")


class InstructionFound(ValueError):
    """A response holds text that reads as an instruction to the buying agent (FO-6)."""

    def __init__(self, found: List[Tuple[str, str]]):
        self.found = found
        super().__init__("text that reads as an instruction to an agent at " + ", ".join(p for p, _ in found[:5]))


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return _SPACES.sub(" ", text.translate(_CONFUSABLES))


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
    return [(where, m.group(0)) for where, text in _strings(value, "$") for m in _RE.finditer(normalized(text))]


def guard(value: Any) -> Any:
    """The value unchanged when it holds no instruction-like text, else InstructionFound. Call it on a result before
    it is sent and settled: FO-2 says a call that fails is not charged."""
    found = scan(value)
    if found:
        raise InstructionFound(found)
    return value
