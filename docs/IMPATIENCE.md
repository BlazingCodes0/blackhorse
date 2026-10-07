# USER IMPATIENCE — data + the philosophy it implies

Date: 2026-10-07. Collected from this session's owner messages. The point is not to
mock — it is to make a reusable training artifact: when an owner shows these
patterns, the system's response should be the same each time.

## Field data — raw quotes

| Timestamp (UTC) | Signal | Quote | Observable |
|---|---|---|---|
| 2026-10-06 ~21:00 | floor-fearing | "why is it not buy… it's ripe for buy" | wanted the bot to fire on a range BUY label, ignored entry-eligibility |
| 2026-10-06 ~21:20 | gate-auditing | "the improving and weaking of spread… be smart" | wanted noise-vs-trend spread calls |
| 2026-10-06 23:30 | drift-questioning | "i somehow dont trust these readout vs yours" | distrust between preview vs bot readouts |
| 2026-10-07 00:30 | UI-impatience | "i dont see any bottom watch armed anywhere" | armed=false at that snapshot, no line printed |
| 2026-10-07 01:10 | crash | "i clicked clear brain budget and it crashed" | Qt thread violation surfaced as the correlational click |
| 2026-10-07 01:40 | format-demand | "i said you answer me what did you do" | wanted a concrete changelog, not explanation |
| 2026-10-07 02:00 | tempo-pressure | "SO WHAT ARE THEY WAIT FOR NOW" / "SO WHAT IS THERE TO WAIT" / "I'M BITING MY FINGERS OFF" | waiting feels like waste |

## Pattern classification

- **M1 Floor hunger**: when price touches a labeled floor/low, they want a trade
  immediately.
- **M2 I cannot see the bot’s state**: distrust when stdout/UI says nothing
  (missing 'armed' text, parse-error JSON, raw dict dumps).
- **M3 Wants receipts**: "what did you do" — concrete diffs, not reasoning.
- **M4 Waiting feels painful**; equates "no fills" with "system broken".

## Philosophy (draft to show users)

We do not trade because a zone is labeled BUY; we trade because the same edge that
made the label profitable in the forward test is still present right now. Every
gate (RSI, spread-not-against, score ≥ 0.60, entry-eligible, Heart caps) is the
edge protecting the capital from us.

Impatience data says the *failure mode* is silence. Fix silence with visibility:
always show the scenario label, the gate booleans, and bottom-watch state even
when nothing fires. When the gates are red, the honest output is
"waiting, and this is the exact gate blocking it," not a fill and not silence.

If a user insists on trading *now*, the lever is the proven-edge bar and the
threshold set — not bypassing them. Changing the rules to satisfy impatience
is how $70 becomes $0.
