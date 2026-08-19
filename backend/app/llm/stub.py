"""
The provider that answers when there is no key — and says so.

Open decision 2 (`roadmap.md`) has been open since 2026-08-03: no AI provider has
been chosen and no key exists in this repo. Everything else about Ask and the SDR
can be built and tested anyway, and this is what stands where the model goes.

## Not the mock it replaces

`dashboard/src/lib/mock/ask-answers.ts` matched a question against nine regexes
and returned **invented numbers** — the same category as the report's
`1,287 visitors` and `/agents`' `fired: 488`, both of which this repo deleted
rather than kept. It also only worked in demo mode, so a real activation asked a
question and got nothing.

This matches a question to a catalogue entry and then *runs the real query
against the real graph*. Every figure it returns is measured. The keyword match
is the only guess it makes, and when it cannot make one it says so and lists what
it can answer instead of reaching for the nearest entry.

## Why it is a registered provider and not an `if` somewhere

Because the difference has to be visible. It is selected the same way a real
provider is, it implements the same interface, and it reports
`capabilities()["reasons"] = False` — which is what every surface renders as the
answer's *basis*. An operator who cannot tell a model's answer from a keyword
match cannot judge either of them, and a system that quietly degraded from one to
the other would be presenting the weaker as the stronger.

## It reports zero tokens, and that is a real number

`app/cost.py` refuses to invent a cost per action, on the grounds that a default
is a number made up on a client's behalf. The same applies here: no call was
made, nothing was spent, and zero is the measurement rather than a placeholder.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm import register
from app.llm.base import Completion, LlmProvider
from app.llm.catalogue import CATALOGUE, menu

log = logging.getLogger(__name__)

#: Words that carry no signal about which question is being asked. Removed before
#: scoring so "how many people came" and "people" do not score alike on "how".
NOISE = frozenset(
    """a an and are as at by did do does for from get give had has have how i in
    is it list many me most much of on or our show that the their there these
    this to us was were what when where which who whose why will with you your""".split()
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in NOISE}


@register
class DeterministicProvider(LlmProvider):
    """Keyword → catalogue entry. No network, no key, no invented figures."""

    provider = "deterministic"

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {"reasons": False, "streams": False}

    @classmethod
    def parse_secret(cls, raw: str) -> dict[str, Any]:
        """Takes no credential, and refuses one rather than ignoring it.

        An admin storing a key against this provider has misunderstood what it
        is, and silently accepting the key would leave them believing a model is
        answering their operators' questions.
        """
        raise ValueError(
            "the deterministic provider takes no credential — it is what answers "
            "when no AI provider is configured. Store your key against the "
            "provider you actually use."
        )

    async def complete(self, prompt: str, *, max_tokens: int = 512) -> Completion:
        """Pick a catalogue entry from the question inside the prompt.

        The prompt is the one `app/llm/prompts.py` builds for a real provider, so
        this reads the question back out of it rather than being handed a
        different input — one path through the system, whichever provider is on
        the end of it.
        """
        question = _question_from(prompt)
        choice = self.route(question)
        return Completion(text=json.dumps(choice))

    def route(self, question: str) -> dict[str, Any]:
        """`{"query": …, "params": …}`, or `{"query": None, "reason": …}`.

        Scored on how much of an entry's own vocabulary the question uses —
        its name, what it asks, and its example phrasings. A tie or a zero is a
        refusal: the whole point of this file is that it does not reach for the
        nearest entry when it has not understood.
        """
        asked = _words(question)
        if not asked:
            return {"query": None, "reason": "that question is empty"}

        scores: dict[str, int] = {}
        for name, entry in CATALOGUE.items():
            vocabulary = _words(
                " ".join((name.replace("_", " "), entry.asks, *entry.examples))
            )
            scores[name] = len(asked & vocabulary)

        best = max(scores.values())
        if best == 0:
            return {
                "query": None,
                "reason": (
                    "I could not match that to anything I can measure. Without an "
                    "AI provider configured I match questions by their wording, "
                    "so try one of the examples"
                ),
            }

        winners = [name for name, score in scores.items() if score == best]
        if len(winners) > 1:
            return {
                "query": None,
                "reason": (
                    "that could be asking about "
                    + " or ".join(sorted(winners))
                    + ", and I would rather ask than guess"
                ),
            }

        return {"query": winners[0], "params": _numbers_in(question, winners[0])}

    async def healthcheck(self) -> tuple[bool, str]:
        return True, (
            "the deterministic provider is always available — it makes no calls. "
            f"It can answer {len(CATALOGUE)} kinds of question."
        )


def _question_from(prompt: str) -> str:
    """The operator's question, back out of the assembled prompt.

    `prompts.py` marks it, precisely so this can find it without a second code
    path. A prompt with no marker is treated as the question itself, which is
    what a caller doing something unusual would expect.
    """
    marker = "QUESTION:"
    return prompt.rsplit(marker, 1)[-1].strip() if marker in prompt else prompt.strip()


def _numbers_in(question: str, name: str) -> dict[str, Any]:
    """Fill an entry's numeric parameter from a number in the question.

    "the busiest 10 minutes" should mean ten minutes. Only ever fills a parameter
    the entry declares, and only from a number the person actually typed — the
    catalogue validates the result either way.
    """
    entry = CATALOGUE[name]
    numeric = [p for p in entry.params if p.type is int]
    if not numeric:
        return {}
    found = re.findall(r"\b(\d{1,4})\b", question)
    return {numeric[0].name: int(found[0])} if found else {}


__all__ = ["DeterministicProvider", "menu"]
