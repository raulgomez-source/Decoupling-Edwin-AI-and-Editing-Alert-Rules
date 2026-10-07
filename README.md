# Decoupling Edwin AI and Editing Alert Rules

The goal is to take Edwin AI out of the alerting path for the `your-portal` LogicMonitor portal.
Alert rules are pointed straight at the right escalation chains, and then Edwin is turned off.

| Phase | What happens | Where |
|---|---|---|
| 1. Update alert rules | Set the escalation chain and interval for the 155 in-scope rules from a CSV | Steps 1–6 below (scripted) |
| 2. Turn off Edwin AI | Disable Edwin actions, rules and models, then stop event ingestion from the portal | Step 7 below and [`docs/edwin-decommission.md`](docs/edwin-decommission.md) (manual) |

The script updates the **escalation chain** (`escalatingChainId`) and **escalation interval**
(`escalationInterval`) of LogicMonitor alert rules using values from a CSV:

- **Default mode is DRY RUN.** Nothing changes unless you add `--apply`.
- Only those two fields are changed (HTTP PATCH). No rules are created or deleted.
- It is safe to run more than once: rules that already match the CSV are skipped ("no changes needed").

## Folder contents

| Path | Purpose |
|---|---|
| `scripts/update_alert_rules_csv.py` | The script |
| `scripts/README.md` | Detailed usage guide: dry run, real run, CSV format, troubleshooting |
| `input/alert_rule_escalations.csv` | Target values for the 155 in-scope rules. **Not committed**, so put it here before running |
| `input/alert_rule_escalations.example.csv` | Format example with placeholder rules. Shows the columns and row types; it can't change real rules |
| `input/alert_rules_out_of_scope.txt` | 7 rules the client excluded. Not in the CSV; do not touch |
| `config/lm_config.template.json` | Config template. Copy to `lm_config.json` and fill in credentials |
| `docs/edwin-decommission.md` | Final step: turn off Edwin AI and stop event ingestion |
| `logs/` | Save your dry-run and apply output here (contents are git-ignored) |
| `requirements.txt` | Python dependencies |

## Prerequisites

- Windows PowerShell, Python 3.9+
- A LogicMonitor API token (Access ID + Access Key) on `your-portal` for a user that can
  **view and manage alert rules** and **view escalation chains**

## 1. One-time setup

Steps 1–6 are the quick version. For more detail (CSV format, how to read the output,
troubleshooting), see [`scripts/README.md`](scripts/README.md).

Run from the root of this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item config\lm_config.template.json config\lm_config.json
notepad config\lm_config.json   # set portal to your-portal, paste access_id and access_key
```

> `config\lm_config.json` contains secrets. Don't email it, commit it, or zip it.
> `.gitignore` already excludes it.

Optional config keys: `page_size` (default 1000) and `endpoints` (`alert_rules`,
`escalation_chains`). Only set them if the defaults don't work.

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

## 7. Final step: turn off Edwin AI

Do this only after step 6 passes, a soak period agreed with the client is over, and the client
has signed off. This step is manual. Follow [`docs/edwin-decommission.md`](docs/edwin-decommission.md);
in short:

1. **Record** the current Edwin configuration so it can be restored.
2. **Turn off Edwin actions** (for example, ServiceNow ticket creation). This removes duplicate incidents.
3. **Turn off Edwin rules** (correlation, enrichment, suppression, routing).
4. **Turn off Edwin models** (correlation/clustering).
5. **Stop event ingestion** from the `your-portal` portal into Edwin (recommended). Close any
   open Edwin insights first, because once ingestion stops, the tickets Edwin opened won't auto-resolve.

Disable, don't delete. To roll back, re-enable in reverse order.

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
