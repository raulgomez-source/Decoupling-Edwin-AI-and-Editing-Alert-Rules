# Alert Rule Escalation Update

Updates the **escalation chain** (`escalatingChainId`) and **escalation interval**
(`escalationInterval`) of LogicMonitor alert rules on the `your-portal` portal, using values from a CSV.

- **Default mode is DRY RUN.** Nothing changes unless you add `--apply`.
- Only those two fields are changed (HTTP PATCH). No rules are created or deleted.
- It is safe to run more than once: rules that already match the CSV are skipped ("no changes needed").

## Folder contents

| Path | Purpose |
|---|---|
| `MESSAGE_TO_TEAMMATE.md` | Context and request for the person running the update |
| `scripts/update_alert_rules_csv.py` | The script |
| `input/alert_rule_escalations.csv` | Target values for the 155 in-scope rules |
| `input/alert_rules_out_of_scope.txt` | 7 rules the client excluded. Not in the CSV; do not touch |
| `config/lm_config.template.json` | Config template. Copy to `lm_config.json` and fill in credentials |
| `reference/alert_rule_refresh_summary.md` | Background: how the CSV was built and validated (paths there refer to the LM-Tools repo) |
| `logs/` | Save your dry-run and apply output here |
| `requirements.txt` | Python dependencies |

## Prerequisites

- Windows PowerShell, Python 3.9+
- A LogicMonitor API token (Access ID + Access Key) on `your-portal` for a user that can
  **view and manage alert rules** and **view escalation chains**

## 1. One-time setup

Run from the root of this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

New-Item -ItemType Directory -Force logs | Out-Null
Copy-Item config\lm_config.template.json config\lm_config.json
notepad config\lm_config.json   # paste access_id and access_key
```

> `config\lm_config.json` contains secrets. Don't email it, commit it, or zip it.

## 2. Connectivity check (read-only)

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --smoke-test
```

Expected: `Smoke test: 161 alert rules, 61 escalation chains.` (or similar), followed by 5 sample rules.
An HTTP 401/403 error means the credentials or permissions are wrong.

## 3. Dry run (read-only, required)

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  2>&1 | Tee-Object -FilePath logs\dry_run_$(Get-Date -Format yyyyMMdd_HHmm).log
```

What the output means:

```
Mode: DRY RUN
...
Loaded 161 alert rules.
Loaded 61 escalation chains.

[row 2] rule id=30 name='Sev 1 Circuit Down' matched by id=30
  planned changes:
    escalatingChainId: 2 -> 17          <- current value -> new value
  DRY RUN: no API update sent
...
[row 156] rule id=3 name='Critical' matched by id=3
  no changes needed                     <- already correct, will be skipped

Summary
  rows seen:          155
  rows with changes:  107               <- rules that WILL be updated on --apply
  rows unchanged:     48
  rules updated:      0                 <- always 0 in dry run
  rows skipped:       0
  warnings:           0
  errors:             0
```

**Go / no-go before applying:**

| Check | Expected (Sep 17 baseline) |
|---|---|
| `Mode` | `DRY RUN` |
| `rows seen` | 155 |
| `rows with changes` | ~107 (lower is fine if someone already fixed some rules) |
| `warnings` | **0**. A warning means a rule ID now points to a different name, or a chain ID's name doesn't match the CSV. Stop and escalate. |
| `errors` | **0**. An error means a rule was not found or a CSV value is invalid. Stop and escalate. |

Keep the dry-run log. It records every rule's **previous** chain and interval, which is what we'd
use to roll back.

## 4. Pilot apply (one rule)

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --apply --limit 1 `
  2>&1 | Tee-Object -FilePath logs\apply_pilot_$(Get-Date -Format yyyyMMdd_HHmm).log
```

This updates only the first CSV row (rule 30, *Sev 1 Circuit Down*, which moves to chain 17
*Sev 1 SN Incident TH Circuit*). Check it in the portal under **Settings > Alert Rules**.

## 5. Full apply

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --apply `
  2>&1 | Tee-Object -FilePath logs\apply_$(Get-Date -Format yyyyMMdd_HHmm).log
```

Each changed rule prints `UPDATED`. The Summary line `rules updated` should equal the dry run's
`rows with changes` (minus the one pilot rule).

## 6. Verify

Run the dry run (step 3) again. Expected: `rows with changes: 0`, `errors: 0`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Finished, but one or more rows failed (see `ERROR` lines). Smoke test: no rules returned |
| 2 | Fatal: bad config, missing CSV columns, authentication failure, etc. |

## How the script works

1. Reads the CSV and detects the columns: `id`, `name`, `escalatingChainId`, `escalationInterval`,
   and the optional `escalatingChain` (chain name, used only for validation).
2. Downloads all alert rules and escalation chains from the portal (read-only).
3. For each row, it finds the rule **by ID**. If the CSV name differs from the live name, it warns
   and still uses the ID match. If the ID doesn't exist, it falls back to the name.
4. Compares the live chain and interval with the CSV values and prints the differences.
5. With `--apply` only, sends `PATCH /setting/alert/rules/{id}` with just the fields that differ.

## All options

| Option | Description |
|---|---|
| `--config` (required) | JSON with `portal`, `access_id`, `access_key` |
| `--csv` (required) | Target CSV |
| `--apply` | Actually PATCH. Omit for dry run |
| `--limit N` | Process only the first N data rows |
| `--smoke-test` | List counts plus 5 sample rules, then exit |
| `--timeout-seconds` | HTTP timeout per request (default 60) |
| `--id-column`, `--name-column`, `--chain-id-column`, `--interval-column`, `--chain-name-column` | Override column names if the CSV headers differ |

## Out of scope (client request)

These 7 rules are **not** in the CSV and must be left alone (do not edit or delete):
EXAMPLE Out-of-scope rule 1, EXAMPLE Out-of-scope rule 2, EXAMPLE Out-of-scope rule 3,
EXAMPLE Out-of-scope rule 4, EXAMPLE Out-of-scope rule 5, EXAMPLE Out-of-scope rule 6,
EXAMPLE Out-of-scope rule 7.

## Rollback

The script has no automatic rollback. The dry-run log taken right before `--apply` shows the
previous value of every changed field (the left side of each `old -> new` line). Most changes are
from chain `2` (`NoEscalation`). To roll back, build a CSV with the old values and run the same
script with `--apply`.
