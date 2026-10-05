# BLACKHORSE BRAIN BLUEPRINT — memory without eating RAM

Constraint that shapes everything: N3540, 4 GB RAM, already swapping.
Rule: **the brain's memory lives on disk. RAM holds only a tiny working slice,
only while it's reasoning, and then it's gone.**

The brain (opencode) is *remote* — thinking costs us ~0 local RAM. Our job is
to feed it a small, relevant slice of a huge memory, and to write back what it
learns. That's the whole game.

---

## 1. Principles
1. **Disk-first.** Every memory is a file (JSONL / SQLite). RAM is a cache, never
   the store. Kill the process → memory survives.
2. **Bounded working set.** Any prompt we build is capped (target <= ~24 KB).
   No "read the whole log into memory" ever.
3. **Stream, don't load.** Read logs by *tailing* the last N lines, not
   `read_text().splitlines()` on a 400 MB file.
4. **Compress upward, not downward.** Raw detail stays cold (gzipped); the
   *digest* is what stays hot. Summaries shrink with age.
5. **Retrieve on demand.** Pull only the few memories relevant to *this*
   decision (time window + tags + full-text search), not everything.
6. **Human approves identity-changing memory.** Procedural memory (rules) only
   changes when you say so. The brain can't rewrite its own risk posture.

---

## 2. The five memory types

| Type | Answers | Store | Size in RAM |
|------|---------|-------|-------------|
| **Working** | "what's happening right now?" | built per-cycle, discarded | <1 MB, transient |
| **Episodic** | "what happened?" | append-only JSONL (raw) | 0 (streamed) |
| **Semantic** | "what have we learned?" | SQLite + digested JSON | ~2-4 MB cache |
| **Procedural** | "what are we allowed to do?" | playbook.json + heart policies | <100 KB |
| **Narrative** | "what did you and I talk about?" | console transcript + rolling summary | tail only |

Every cycle the brain gets a **packet** (working memory) assembled from the other
four. It never loads them whole.

---

## 3. File layout (all on disk, git-ignored where private)

```
blackhorse/
  logs/
    observations.jsonl     # episodic: market behavior each cycle (append-only)
    decisions.jsonl        # episodic: propose -> gate -> act -> outcome
    trades.jsonl           # episodic: fills, fees, P&L
    conversations.jsonl    # narrative: your chat with the brain
  memory/
    brain.db               # SQLite: FTS5 index + compact tables (semantic)
    digest_daily.md        # rolling summary the brain wrote
    digest_weekly.md
    lessons.json           # semantic: distilled do/don't (brain proposes, you approve)
    patterns.json          # semantic: per-regime stats (win rate, edge)
    cold/
      observations-2026-09.jsonl.gz   # gzipped cold episodic
  config/
    playbook.json          # procedural: YOUR rules (strength, sizing, HTF pull)
  trade/
    treasury.json          # capital object (the "100")
```

---

## 4. Write path (cheap, append-only, zero RAM growth)
```
cycle event  -> append one JSON line to logs/<stream>.jsonl   (O(1), tiny)
```
No loading. No indexing on the hot path. Ever.

Only *periodically* (see 6) do we index/consolidate — off the critical path.

## 5. Read path (build the packet, bounded)
```
build_packet():
  1. working: capital, open positions, today P&L, latest signal, latest verdict
  2. episodic: last K observation lines (K ~ 20) via tail
  3. semantic: top R relevant lessons + this regime's stats (SQLite query, LIMIT R)
  4. procedural: playbook.json (small, always)
  -> cap total at ~24 KB, newest/nearest first
```
The brain sees a **summary of memory**, not memory. That's how a 4 GB box has
"years" of memory.

## 6. Consolidation (the only thing that "thinks about memory")
Triggered on a schedule (nightly) or every N new episodes, and it's budgeted
(one opencode call, off-peak):
1. Read yesterday's raw episodic (streamed, chunked).
2. Brain writes `digest_daily.md` (few hundred words) — the story of the day.
3. Extract **lessons/patterns** -> propose updates to `lessons.json` / `patterns.json`
   (status=f"pending" until you approve).
4. Move the raw file to `cold/*.jsonl.gz` (gzip; ~10-20x smaller).
5. Weekly: compress the dailies into `digest_weekly.md`, drop old dailies.
Result: unbounded history, bounded *hot* memory.

## 7. Retrieval without embeddings (keeps RAM flat)
- **SQLite FTS5** (built into Python's sqlite3) = full-text search, near-zero RAM.
  Query: "times BTC falling and advisor buy was null" -> find episodes -> fetch the
  lines that follow -> feed the outcome back. This is how the n/a hypothesis gets
  tested empirically, not argued.
- **Structured filters**: time window, pair, regime, action, outcome.
- **No vector DB by default.** Embeddings need a model loaded in RAM — skip it on
  this box. (Optional later: a tiny external embedding service, not local.)
- Tiny cache: `PRAGMA cache_size = -2000` (~2 MB), `mmap_size` modest.

## 8. RAM budget (why this fits)
| Component | RAM |
|-----------|-----|
| Working packet | <1 MB |
| SQLite page cache | ~2-4 MB (tunable) |
| opencode thinking | 0 local (remote) |
| Local qwen fallback | 0 (OFF by default; if on, 397 MB — accept or avoid) |
| Python process overhead | ~30-60 MB |
| **Brain memory total** | **~3-6 MB** |
Memory can grow forever on disk and RAM stays flat. That's the answer to "without
eating up memory lol."

## 9. Forgetting (not optional)
- Raw episodic: keep 7 days hot, then gzip to cold, then delete after 90 days.
- Digests: keep daily ~8 weeks, weekly forever (tiny).
- Lessons/patterns: never auto-delete; supersede with new versions + timestamp.
- Working memory: cleared every cycle. Always.

## 10. How memory changes a decision
Today: "signal says HOLD." With memory: *"signal HOLD; in the last 30 instances of
falling + buy n/a on 240m, price kept falling 71% of the time — so the null
CONFIRMS the downtrend; last time I bought that dip on this pair it hit my stop."*
Same numbers, different competence. That's a brain with memory.

## 11. Build order (memory-first)
1. `trade/treasury.py`   — the capital object (the "100"), append-only ledger.
2. `trade/observer.py`   — write observations.jsonl (episodic) each cycle.
3. `memory/index.py`     — SQLite + FTS5 over the streams (read-only queries).
4. `memory/consolidate.py`— nightly digest + lesson proposal (budgeted).
5. `trade/decider.py`    — consume packet (working + retrieved) -> action.
6. `heart` gate          — procedural memory enforced; unlocks autonomy.
7. `brain_console.py`    — stateful chat over conversations.jsonl + digest.
8. `validate/`           — fee-honest backtest; earns live capital.

## 12. Non-negotiables
- No process ever loads a whole log into RAM.
- The prompt is always capped; if it doesn't fit, summarize, don't expand.
- Procedural memory edits require your approval.
- null/0/empty advisor price = "missing", stored distinctly, never coerced.
- Every memory write is append-only; corrections are new records, not edits.
