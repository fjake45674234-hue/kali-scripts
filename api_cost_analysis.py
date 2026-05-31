#!/usr/bin/env python3
"""
Daily xAI Grok API cost analysis.
Parses agent.log, computes cost, saves markdown report to Obsidian,
and appends a rolling cost.json for trend tracking.
"""

import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Pricing: xAI grok-4.20-0309-non-reasoning (USD per 1M tokens) ────────────
PRICE_INPUT   = 3.00   # uncached input
PRICE_CACHED  = 0.75   # cached input (context cache hit)
PRICE_OUTPUT  = 15.00  # output tokens

# ── Paths ─────────────────────────────────────────────────────────────────────
HERMES_HOME   = Path.home() / ".hermes"
LOG_FILE      = HERMES_HOME / "logs" / "agent.log"
OBSIDIAN_DIR  = Path.home() / "Obsidian-Vault" / "Daily-Briefings"
COST_LEDGER   = HERMES_HOME / "logs" / "api_cost_ledger.json"

# ── Regexes ───────────────────────────────────────────────────────────────────
RE_CALL = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+ INFO \[([^\]]+)\] run_agent: "
    r"API call #\d+: model=(\S+) provider=\S+ "
    r"in=(\d+) out=(\d+) total=\d+ latency=([\d.]+)s "
    r"cache=(\d+)/\d+ \((\d+)%\)"
)
RE_TURN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) \S+ INFO \[([^\]]+)\] run_agent: "
    r"conversation turn: session=\S+ model=\S+ provider=\S+ platform=(\S+)"
)


def parse_log(target_date: str) -> dict:
    """Return aggregated token counts for target_date."""
    totals = {
        "calls": 0,
        "in_tokens": 0,
        "cached_tokens": 0,
        "out_tokens": 0,
        "total_latency": 0.0,
        "by_session": defaultdict(lambda: {"calls": 0, "in": 0, "cached": 0, "out": 0, "platform": "?"}),
        "by_hour": defaultdict(lambda: {"calls": 0, "in": 0, "out": 0}),
    }
    session_platforms: dict[str, str] = {}

    if not LOG_FILE.exists():
        return totals

    with LOG_FILE.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.startswith(target_date):
                continue

            # Index platform per session
            m = RE_TURN.match(line)
            if m:
                session_platforms[m.group(2)] = m.group(3)
                continue

            m = RE_CALL.match(line)
            if not m:
                continue

            _date, time_str, session, model, in_tok, out_tok, latency, cached_tok, cache_pct = m.groups()
            in_tok, out_tok, cached_tok = int(in_tok), int(out_tok), int(cached_tok)
            latency = float(latency)
            hour = time_str[:2]

            totals["calls"] += 1
            totals["in_tokens"] += in_tok
            totals["cached_tokens"] += cached_tok
            totals["out_tokens"] += out_tok
            totals["total_latency"] += latency

            sess = totals["by_session"][session]
            sess["calls"] += 1
            sess["in"] += in_tok
            sess["cached"] += cached_tok
            sess["out"] += out_tok
            if session in session_platforms:
                sess["platform"] = session_platforms[session]

            bh = totals["by_hour"][hour]
            bh["calls"] += 1
            bh["in"] += in_tok
            bh["out"] += out_tok

    return totals


def compute_cost(in_tok: int, cached_tok: int, out_tok: int) -> float:
    uncached = max(in_tok - cached_tok, 0)
    return (
        uncached    * PRICE_INPUT   / 1_000_000
        + cached_tok  * PRICE_CACHED  / 1_000_000
        + out_tok     * PRICE_OUTPUT  / 1_000_000
    )


def load_ledger() -> list:
    if COST_LEDGER.exists():
        return json.loads(COST_LEDGER.read_text())
    return []


def save_ledger(ledger: list) -> None:
    COST_LEDGER.write_text(json.dumps(ledger, indent=2))


def reduction_tips(totals: dict, cost: float, prev_7d: list) -> list[str]:
    tips = []
    calls = totals["calls"]
    if calls == 0:
        return ["No API calls recorded today."]

    cache_pct = totals["cached_tokens"] / totals["in_tokens"] * 100 if totals["in_tokens"] else 0
    avg_in = totals["in_tokens"] / calls
    avg_out = totals["out_tokens"] / calls

    if cache_pct < 50:
        tips.append(
            f"**Low cache hit rate ({cache_pct:.0f}%)** — enable or extend context caching. "
            "Target >80% to cut input costs by ~75%."
        )
    if cache_pct >= 80:
        tips.append(f"Cache hit rate is excellent ({cache_pct:.0f}%) — good job.")

    if avg_in > 30_000:
        tips.append(
            f"**High avg input context ({avg_in:,.0f} tok/call)** — consider summarising "
            "long history before injecting into context, or truncating tool output."
        )

    out_ratio = totals["out_tokens"] / totals["in_tokens"] if totals["in_tokens"] else 0
    if out_ratio > 0.3:
        tips.append(
            f"**Output-heavy ratio ({out_ratio:.2f})** — output tokens cost 5× input. "
            "Use concise system prompts that instruct shorter replies where appropriate."
        )

    if calls > 50:
        tips.append(
            f"**{calls} API calls today** — consider batching short tasks into single "
            "agent turns to reduce per-call overhead."
        )

    if prev_7d:
        avg_7d = sum(r["cost_usd"] for r in prev_7d) / len(prev_7d)
        if cost > avg_7d * 1.3:
            tips.append(
                f"**Cost spike** — today (${cost:.4f}) is {cost/avg_7d:.1f}× the 7-day "
                f"average (${avg_7d:.4f}). Review high-call sessions above."
            )

    if not tips:
        tips.append("Spending looks healthy — no specific reduction needed today.")
    return tips


def peak_hour(by_hour: dict) -> str:
    if not by_hour:
        return "N/A"
    peak = max(by_hour, key=lambda h: by_hour[h]["calls"])
    return f"{peak}:00 ({by_hour[peak]['calls']} calls)"


def build_report(target_date: str, totals: dict, cost: float, prev_7d: list) -> str:
    calls = totals["calls"]
    in_tok = totals["in_tokens"]
    cached = totals["cached_tokens"]
    out_tok = totals["out_tokens"]
    cache_pct = cached / in_tok * 100 if in_tok else 0
    avg_lat = totals["total_latency"] / calls if calls else 0

    uncached_cost = max(in_tok - cached, 0) * PRICE_INPUT / 1_000_000
    cached_cost   = cached  * PRICE_CACHED  / 1_000_000
    output_cost   = out_tok * PRICE_OUTPUT  / 1_000_000
    saved_by_cache = cached * (PRICE_INPUT - PRICE_CACHED) / 1_000_000

    tips = reduction_tips(totals, cost, prev_7d)

    # 7-day trend table
    trend_rows = ""
    for r in prev_7d[-6:]:
        trend_rows += f"| {r['date']} | {r['calls']} | {r['in_tokens']:,} | {r['out_tokens']:,} | ${r['cost_usd']:.4f} |\n"
    trend_rows += f"| **{target_date}** | **{calls}** | **{in_tok:,}** | **{out_tok:,}** | **${cost:.4f}** |\n"

    # Top sessions by cost
    session_costs = []
    for sid, s in totals["by_session"].items():
        sc = compute_cost(s["in"], s["cached"], s["out"])
        session_costs.append((sc, sid, s))
    session_costs.sort(reverse=True)

    top_sessions = ""
    for sc, sid, s in session_costs[:5]:
        short_id = sid[-12:]
        top_sessions += (
            f"| `{short_id}` | {s['platform']} | {s['calls']} | "
            f"{s['in']:,} | {s['cached']:,} | {s['out']:,} | ${sc:.4f} |\n"
        )

    tips_md = "\n".join(f"- {t}" for t in tips)

    return f"""---
date: {target_date}
tags: [api-cost, grok, daily-analysis]
---

# API Cost Analysis — {target_date}

> Model: `xai/grok-4.20-0309-non-reasoning` via LiteLLM proxy

## Summary

| Metric | Value |
|--------|-------|
| Total API calls | {calls} |
| Input tokens (total) | {in_tok:,} |
| ↳ Cached (cheaper) | {cached:,} ({cache_pct:.1f}%) |
| ↳ Uncached | {max(in_tok - cached, 0):,} |
| Output tokens | {out_tok:,} |
| Avg latency | {avg_lat:.2f}s |
| Peak hour | {peak_hour(totals["by_hour"])} |

## Cost Breakdown

| Component | Tokens | Rate | Cost |
|-----------|--------|------|------|
| Uncached input | {max(in_tok - cached, 0):,} | $3.00/1M | ${uncached_cost:.4f} |
| Cached input | {cached:,} | $0.75/1M | ${cached_cost:.4f} |
| Output | {out_tok:,} | $15.00/1M | ${output_cost:.4f} |
| **Total** | | | **${cost:.4f}** |
| Saved by caching | {cached:,} | | **${saved_by_cache:.4f}** |

## 7-Day Trend

| Date | Calls | Input tok | Output tok | Cost |
|------|-------|-----------|------------|------|
{trend_rows}
## Top Sessions by Cost

| Session | Platform | Calls | Input | Cached | Output | Cost |
|---------|----------|-------|-------|--------|--------|------|
{top_sessions if top_sessions else "| — | No sessions | | | | | |\n"}
## Cost Reduction Recommendations

{tips_md}

---
*Generated by `/home/kali/scripts/api_cost_analysis.py` · Pricing: $3/$0.75/$15 per 1M tokens (in/cached/out)*
"""


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
    print(f"Analysing {target_date}...")

    totals = parse_log(target_date)
    cost = compute_cost(totals["in_tokens"], totals["cached_tokens"], totals["out_tokens"])

    # Update ledger
    ledger = load_ledger()
    ledger = [r for r in ledger if r["date"] != target_date]  # remove existing entry
    ledger.append({
        "date": target_date,
        "calls": totals["calls"],
        "in_tokens": totals["in_tokens"],
        "cached_tokens": totals["cached_tokens"],
        "out_tokens": totals["out_tokens"],
        "cost_usd": round(cost, 6),
    })
    ledger.sort(key=lambda r: r["date"])
    save_ledger(ledger)

    prev_7d = [r for r in ledger if r["date"] < target_date][-7:]

    report = build_report(target_date, totals, cost, prev_7d)

    OBSIDIAN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OBSIDIAN_DIR / f"API-Cost-{target_date}.md"
    out_path.write_text(report)

    print(f"Cost today: ${cost:.4f}")
    print(f"Calls: {totals['calls']}  Input: {totals['in_tokens']:,}  Output: {totals['out_tokens']:,}")
    cache_pct = totals["cached_tokens"] / totals["in_tokens"] * 100 if totals["in_tokens"] else 0
    print(f"Cache hit: {cache_pct:.1f}%  Saved by cache: ${totals['cached_tokens'] * (3.00 - 0.75) / 1_000_000:.4f}")
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
