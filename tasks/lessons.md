# Lessons

A running log of patterns to avoid, written down after a user correction so I
can review them at the start of future sessions. Per CLAUDE.md §8.

---

## 2026-04-24 — Don't jump to infrastructure when a local solution fits

**Pattern.** When a feature description names a hard-sounding property
("long-lived", "durable", "survives reinstall", "cross-device"), I reach for
infrastructure that satisfies the property literally — cloud DBs, sync layers,
identity systems — without first asking what the smallest local solution
actually accomplishes.

**Concrete instance.** "Memory should be stored in a long-living store
since it's device+child dependent and not be destroyed with a reinstall" →
I designed Firestore + GCS + a `child_id` system + admin cascade-delete
routes + a four-phase rollout. The user pushed back twice. The right design
was a markdown file at `~/.myra/memory.md`, read into the system prompt at
session start. ~50 lines of Python instead of ~400. Strictly better on
privacy, complexity, and reviewability.

**Why I drifted.**
- I read "survives reinstall" as "must survive any wipe", when in practice
  the realistic scenario is `pip install` / `git pull`, which the
  filesystem already handles.
- I conflated *memory* (human-curated facts) with *mastery tracking*
  (per-word SR state). Different access patterns, different consumers.
  Bundling them justified a DB.
- I optimised for hypothetical multi-device futures that aren't on the
  roadmap.

**Rule for next time.**
Before designing storage for a "memory" / "preferences" / "profile" feature:

1. **Name the consumer.** If it's an LLM, the simplest representation is
   the one the LLM eats natively (markdown text, not rows). Don't add a
   formatting layer between storage and prompt unless asked.
2. **Name the writer.** If it's a human (parent, child, admin), the
   simplest storage is one a human can read and edit with `cat` / `vim`.
   Privacy + audit + delete come for free.
3. **Separate concerns.** "Remember she has a brother" and "she got 'water'
   wrong 3 times" are different features even if they sound related.
   Don't unify them in one schema.
4. **Map "survives X" to the actual scenarios.** App reinstall ≠ OS
   reflash ≠ device replacement. Pick the cheapest mechanism that covers
   the scenarios that will actually happen, then document the gap on the
   ones that won't.
5. **Start with the file, not the database.** Reach for SQLite only when a
   query pattern (lookup-by-key, filter-by-date, range scan) genuinely
   needs it. A markdown file or JSON blob is the right default for any
   data the LLM consumes whole.

**Cost of repeating this mistake.** ~3 review cycles in this session
before we converged. The plan-persistent-memory.md doc was rewritten
twice. Real cost: user trust that the first answer is the right one.
