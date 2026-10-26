"""A local model as a second opinion, used only where judgement is the problem.

The scorer refuses in two situations: nothing clears the threshold, and two
different domains sit within a whisker of each other. Both are judgement calls
rather than arithmetic, and judgement is what a model is actually good at. The
earlier failures came from asking a small model to count, quote and plan, which
it cannot do reliably; choosing between two URLs it can be shown is a different
task entirely.

The model is boxed in so it cannot repeat those failures:

  It never generates a URL. It picks an index from a list it was handed, and
  anything that is not a valid index is treated as "cannot tell".

  It only sees candidates the scorer already shortlisted, so a blocked
  aggregator never reaches it.

  It can always answer "none", and that answer is respected rather than
  retried until it agrees.

  Its pick is recorded with its reason and a lower confidence than a scored
  match, so a later audit can tell the two apart.

When no model is running, every call returns "cannot tell" and the behaviour is
exactly what it was before.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_ENDPOINT = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODEL = "qwen3:4b"

PROMPT = """You are checking which website belongs to one specific organisation.

Organisation: {name}
{place}
Candidate websites:
{options}

Answer with the number of the website that is the organisation's own official
site. Answer 0 if none of them is, or if two of them could equally be it.

Rules:
- A parent group's site is not the answer when the organisation is one branch
  or college within it. Answer 0 in that case.
- A directory, ranking site, news article or social page is never the answer.
- Do not explain. Reply with a single number and nothing else.

Number:"""


@dataclass
class Verdict:
    index: int | None          # 1-based pick, or None for "cannot tell"
    raw: str = ""
    error: str = ""

    @property
    def decided(self) -> bool:
        return self.index is not None


def ask(
    name: str,
    urls: list[str],
    location: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = 45.0,
) -> Verdict:
    """Ask the local model to pick one candidate. Never raises."""
    if not urls:
        return Verdict(None, error="no candidates")
    shortlist = urls[:6]  # a longer list invites a sloppier answer

    body = json.dumps({
        "model": model,
        "prompt": PROMPT.format(
            name=name,
            place=f"Located in: {location}\n" if location else "",
            options="\n".join(f"{i}. {u}" for i, u in enumerate(shortlist, 1)),
        ),
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 8},
    }).encode()

    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.load(response).get("response", "")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
        return Verdict(None, error=f"{type(error).__name__}")

    # Take the first number in the reply. A model that pads its answer with
    # reasoning still gets read correctly; one that answers nothing numeric is
    # treated as undecided rather than guessed at.
    match = re.search(r"\d+", answer.strip())
    if not match:
        return Verdict(None, raw=answer[:80], error="no number in reply")

    choice = int(match.group())
    if choice == 0 or choice > len(shortlist):
        return Verdict(None, raw=answer[:80])
    return Verdict(choice, raw=answer[:80])


def break_tie(
    name: str,
    scored: list,
    location: str | None = None,
    max_candidates: int = 4,
    **kwargs,
):
    """Choose among already-scored candidates. Returns the Candidate or None.

    `scored` is the scorer's own output, highest first, so anything blocked has
    already been dropped. Only the top few are offered: past the top handful the
    scores are too low for the question to be worth asking.
    """
    shortlist = [c for c in scored[:max_candidates]]
    if len(shortlist) < 2:
        return None, Verdict(None, error="nothing to choose between")

    verdict = ask(name, [c.url for c in shortlist], location, **kwargs)
    if not verdict.decided:
        return None, verdict
    return shortlist[verdict.index - 1], verdict


def available(endpoint: str = DEFAULT_ENDPOINT, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """Is a model actually reachable? Used to report, not to decide."""
    tags = endpoint.replace("/api/generate", "/api/tags")
    try:
        with urllib.request.urlopen(tags, timeout=5) as response:
            names = [m.get("name", "") for m in json.load(response).get("models", [])]
    except Exception as error:
        return False, f"no model service at {tags} ({type(error).__name__})"
    if not any(n.split(":")[0] == model.split(":")[0] for n in names):
        return False, f"{model} not installed; found {', '.join(names) or 'nothing'}"
    return True, f"{model} available"


def self_test() -> None:
    """Parsing checks with canned replies. No model needed."""
    import types

    cases = [
        ("2", 2, "plain number"),
        ("2\n", 2, "trailing newline"),
        ("The answer is 2.", 2, "padded with words"),
        ("0", None, "model says none"),
        ("9", None, "out of range"),
        ("I am not sure", None, "no number"),
        ("", None, "empty reply"),
    ]
    urls = ["https://a.test/", "https://b.test/", "https://c.test/"]
    failures = 0
    for reply, expected, label in cases:
        verdict = _parse_for_test(reply, urls)
        ok = verdict.index == expected
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label:22} reply={reply!r:20} -> {verdict.index}")

    # A single candidate is never sent to the model.
    fake = [types.SimpleNamespace(url="https://only.test/", score=1.0)]
    pick, verdict = break_tie("X", fake)
    ok = pick is None and not verdict.decided
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} single candidate skipped")

    print(f"\n{len(cases) + 1 - failures}/{len(cases) + 1} passed")
    reachable, note = available()
    print(f"model check: {note}")


def _parse_for_test(answer: str, urls: list[str]) -> Verdict:
    match = re.search(r"\d+", answer.strip())
    if not match:
        return Verdict(None, raw=answer[:80], error="no number in reply")
    choice = int(match.group())
    if choice == 0 or choice > len(urls):
        return Verdict(None, raw=answer[:80])
    return Verdict(choice, raw=answer[:80])


if __name__ == "__main__":
    self_test()
