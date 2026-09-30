# Start Here (for Antigravity / Cursor)

Two independent Python projects. Do them in this order.

## 1. `notion-engine/` — migrate and finish (smaller job, sellable sooner)

Read `notion-engine/HANDOFF.md`. The code exists but targets an outdated
Notion API. Migrate it, add views and formulas through the new APIs, and
prove it with 3 live builds.

## 2. `sticker-engine/` — fix, then build

Read `sticker-engine/HANDOFF.md` (v2). Phase 0 fixes code that went stale
(the Imagen image API it was built on was shut down on 2026-08-17). Then
build the stubbed modules in the order the file lists.

## Rules for both

- Verify every model ID, API field, and price against the live docs on
  build day. These handoffs were written 2026-09-30 and mark what was
  verified then; nothing here is guaranteed to still be current.
- Never commit `.env`, `output/`, or `data/`.
- When a doc and the code disagree, the HANDOFF.md in that project wins.
- Report back with the items listed at the end of each HANDOFF.md.
