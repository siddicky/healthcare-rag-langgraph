<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# tests/fixtures

## Purpose
Small, hand-maintained JSON fixtures shared across multiple test modules where the
data itself (not the test logic) is the thing worth keeping in one place and
diffing over time — a design-system prop contract and a `__ref` acceptance table.

## Key Files
| File | Description |
|------|-------------|
| `calendar_change_card_contract.json` | The prop contract for the `CalendarChangeCard` schedule-change interrupt card (`interrupt_props`: `eventLabel`, `fromLabel`, `toLabel`, `reason`; `post_decision_props`: `status`), mirrored from the (untracked) Nymble Health Design System repo so this suite stays self-contained; update alongside the real design prompt when the card's props change |
| `catalog_data_refs.json` | Acceptance table for the backend's `__ref` data-reference shape: valid root/nested pointers accepted, a bare literal value rejected, missing/non-string `pointer` rejected — consumed by `tests/test_catalog_data_ref_fixture.py` |

## For AI Agents

### Working In This Directory
- `calendar_change_card_contract.json` is a mirror of an external, untracked design-system file — when the real `CalendarChangeCard.prompt.md` prop contract changes, update this fixture in the same change, not later; there is no automated sync.
- `catalog_data_refs.json` rows each need an `id`, `value`, and boolean `accepted` — add a new row (not a new file) when extending `__ref` acceptance coverage so `test_catalog_data_ref_fixture.py` picks it up automatically.

### Testing Requirements
These fixtures are consumed by tests elsewhere, not run directly:
```
uv run pytest tests/test_catalog_data_ref_fixture.py -q
```
(the calendar-card fixture is read by whichever test asserts the interrupt card's prop contract — grep `calendar_change_card_contract` before assuming it's unused).

### Common Patterns
- Keep each fixture file scoped to exactly one contract/table; don't merge unrelated fixtures into one JSON file for convenience — the point of this directory is that each file is independently diffable against its external source of truth.

## Dependencies

### Internal
- Consumed by `tests/test_catalog_data_ref_fixture.py` and by coach-agent generative-UI tests under `tests/agent/`

### External
- None (static JSON only)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
