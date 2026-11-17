r"""Measure how often table discovery finds the right table, before changing its ranking.

The generated-question audit (questions/Generated_Question_Audit.csv) says which
official table each sample question was written from. This tool asks the same
search Sage uses (utils.hybrid_discovery.retrieve) every question and reports:

  hit@1  - the right table is the first candidate
  hit@5  - it is among the first five (what the model is normally shown)
  hit@10 - it is among the first ten
  MRR    - mean of 1/rank (higher is better; 0 when not found)

HOW FAR TO TRUST THE NUMBERS. The sample questions were WRITTEN FROM the official
table titles, and the search rewards words shared with a title, so these scores flatter
the search. Two things make the measurement honest:

  * every run also reports the questions that share FEW words with their table's
    title ("low title overlap"): the closest thing in the audit to a question a
    visitor would type;
  * --cases FILE measures your own questions instead: a CSV with the columns
    "question" and "table_id" (optionally "table_title"), written by people who did
    not look at the titles. Keep that file out of any tuning of the ranking: it is
    the held-out set. See questions/held_out_cases.example.csv.

Questions are asked as written and with one typing mistake added. With
--compare-with, the same questions are also asked of another Sage copy (for
example your backup) using the same index, so you can see whether a change helped.

Run from the Sage folder, on a machine with the real index (data/):

    python tools/evaluate_discovery.py                              # 200 questions
    python tools/evaluate_discovery.py --limit 1000                 # every sample question
    python tools/evaluate_discovery.py --compare-with C:\Backup\Sage  # this copy vs a backup
    python tools/evaluate_discovery.py --keyword-only               # no OpenAI calls
    python tools/evaluate_discovery.py --cases questions/held_out_cases.csv   # your own set

Each question costs one small embedding call per run (well under one US cent
for 1,000 calls with text-embedding-3-small).
Nothing is written to the database. The report goes to reports/discovery_eval_*.json.
"""

import argparse
import csv
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


AUDIT = ROOT / "questions" / "Generated_Question_Audit.csv"
#: A question sharing less than this share of its table title's words is "low overlap".
LOW_OVERLAP = 0.5
_FILLER = frozenset(
    "a an and are as at by for from how in is its of on or over the to was were what with "
    "singapore annual quarterly monthly total number".split()
)


def _read_cases(path) -> list[dict]:
    """Rows of a cases file: question, table_id and (when present) table_title."""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [
            {
                "question": row["question"].strip(),
                "table_id": row["table_id"].strip(),
                "table_title": (row.get("table_title") or "").strip(),
            }
            for row in csv.DictReader(handle)
            if (row.get("question") or "").strip() and (row.get("table_id") or "").strip()
        ]


def load_cases(limit: int, seed: int, path=None) -> list[tuple[str, str]]:
    """(question, expected table ID) pairs, a fixed random sample of ``limit``.

    From the generated-question audit, or from ``path`` (your own held-out questions).
    """
    rows = [(row["question"], row["table_id"]) for row in _read_cases(path or AUDIT)]
    random.Random(seed).shuffle(rows)
    return rows[:limit]


def _words(text: str) -> set:
    """Content words of a title or question (lower case, plural "s" dropped, filler removed)."""
    found = set()
    for word in "".join(ch if ch.isalnum() else " " for ch in text.casefold()).split():
        word = word[:-1] if len(word) > 3 and word.endswith("s") else word
        if len(word) > 2 and word not in _FILLER:
            found.add(word)
    return found


def title_overlap(question: str, title: str) -> float | None:
    """Share of the table title's content words that the question repeats (None: no title)."""
    wanted = _words(title)
    return len(wanted & _words(question)) / len(wanted) if wanted else None


def low_overlap_positions(cases, path=None) -> list[int]:
    """Positions in ``cases`` of questions that share few words with their table's title."""
    titles = {(row["question"], row["table_id"]): row["table_title"]
              for row in _read_cases(path or AUDIT)}
    chosen = []
    for position, (question, expected) in enumerate(cases):
        share = title_overlap(question, titles.get((question, expected), ""))
        if share is not None and share < LOW_OVERLAP:
            chosen.append(position)
    return chosen


def with_typo(question: str, seed: int) -> str:
    """The question with one letter of its longest word swapped for a neighbour (a typical slip)."""
    words = question.split()
    longest = max(range(len(words)), key=lambda i: (len(words[i]), -i))
    word = words[longest]
    if len(word) < 5:
        return question
    position = random.Random(seed).randrange(1, len(word) - 1)
    words[longest] = word[:position] + word[position + 1] + word[position] + word[position + 2 :]
    return " ".join(words)


def rank_of(expected: str, found: list[dict]) -> int | None:
    """1-based position of the expected table among the candidates, or None."""
    ids = [str(item.get("table_id") or "").upper() for item in found]
    return ids.index(expected.upper()) + 1 if expected.upper() in ids else None


def summarise(ranks: list) -> dict:
    """hit@1/5/10 and MRR for a list of ranks (None = not found)."""
    total = len(ranks) or 1
    return {
        "questions": len(ranks),
        "hit@1": round(sum(1 for r in ranks if r == 1) / total, 3),
        "hit@5": round(sum(1 for r in ranks if r and r <= 5) / total, 3),
        "hit@10": round(sum(1 for r in ranks if r and r <= 10) / total, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / total, 3),
    }


def run(cases, typos: bool) -> list:
    """Ranks for every case with the copy of Sage on sys.path (optionally with typing mistakes)."""
    from utils import hybrid_discovery

    ranks = []
    for number, (question, expected) in enumerate(cases):
        asked = with_typo(question, number) if typos else question
        found = hybrid_discovery.retrieve(asked, top_k=10)
        ranks.append(rank_of(expected, found))
    return ranks


def measure(cases, typo_runs) -> dict:
    """{label: ranks} for this process's copy of Sage."""
    return {("with typos" if typos else "as written"): run(cases, typos) for typos in typo_runs}


def _ranks_from_other_copy(other, args) -> dict:
    """Run the same measurement inside another Sage copy, using THIS copy's index and questions."""
    env = dict(
        os.environ,
        SAGE_KB_SQLITE=os.environ.get(
            "SAGE_KB_SQLITE", str(ROOT / "data" / "knowledge_base.sqlite3")
        ),
        SAGE_CHROMA_DIR=os.environ.get("SAGE_CHROMA_DIR", str(ROOT / "data" / "chroma_singstat")),
    )
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--tree",
        str(other),
        "--json-only",
        "--limit",
        str(args.limit),
        "--seed",
        str(args.seed),
    ]
    command += ["--cases", str(Path(args.cases).resolve())] if args.cases else []
    command += ["--keyword-only"] if args.keyword_only else []
    command += ["--no-typos"] if args.no_typos else []
    done = subprocess.run(
        command, env=env, capture_output=True, text=True, check=True, cwd=str(other)
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def main(argv=None) -> int:
    """Measure this copy (and optionally another) and print a comparison table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--keyword-only", action="store_true", help="switch semantic search off (no OpenAI calls)"
    )
    parser.add_argument("--no-typos", action="store_true", help="skip the typing-mistake run")
    parser.add_argument(
        "--compare-with", help="another Sage folder (e.g. your backup) to measure too"
    )
    parser.add_argument(
        "--cases", help="CSV of your own questions (columns: question, table_id[, table_title])"
    )
    parser.add_argument("--tree", help=argparse.SUPPRESS)  # internal: which copy to import
    parser.add_argument("--json-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.tree:
        sys.path.insert(0, str(Path(args.tree).resolve()))
    if args.keyword_only:
        os.environ["SAGE_CHROMA_SEARCH"] = "0"
    cases = load_cases(args.limit, args.seed, args.cases)
    typo_runs = [False] if args.no_typos else [False, True]
    if args.json_only:
        print(json.dumps(measure(cases, typo_runs)))
        return 0
    copies = {"this copy": measure(cases, typo_runs)}
    low_overlap = low_overlap_positions(cases, args.cases)
    if not args.cases:
        print("Note: these questions were written from the table titles; trust the "
              "'low title overlap' rows, or measure your own questions with --cases.")
    if args.compare_with:
        copies["other copy"] = _ranks_from_other_copy(Path(args.compare_with).resolve(), args)
    results = {}
    for copy_name, runs in copies.items():
        for label, ranks in runs.items():
            name = f"{copy_name}, {label}"
            results[name] = summarise(ranks)
            print(f"{name:<44} {results[name]}", flush=True)
            # The same run, counting only questions that do not repeat their table's title.
            hard = [ranks[position] for position in low_overlap if position < len(ranks)]
            if hard:
                name += ", low title overlap"
                results[name] = summarise(hard)
                print(f"{name:<44} {results[name]}", flush=True)
    changed = []
    if "other copy" in copies:
        mine, theirs = copies["this copy"]["as written"], copies["other copy"]["as written"]
        changed = [
            {"question": q, "expected": e, "this copy": mine[i], "other copy": theirs[i]}
            for i, (q, e) in enumerate(cases)
            if mine[i] != theirs[i]
        ]
        print(f"\n{len(changed)} questions ranked differently by the two copies.")
    report = ROOT / "reports" / f"discovery_eval_{datetime.now(timezone.utc):%Y%m%d-%H%M}.json"
    report.parent.mkdir(exist_ok=True)
    report.write_text(
        json.dumps(
            {"settings": vars(args), "results": results, "questions_ranked_differently": changed},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Details in {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
