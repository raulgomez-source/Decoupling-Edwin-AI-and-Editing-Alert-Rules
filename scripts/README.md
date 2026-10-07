# `update_alert_rules_csv.py` – usage guide

This script sets the **escalation chain** (`escalatingChainId`) and **escalation interval**
(`escalationInterval`) on LogicMonitor alert rules, using target values from a CSV.

It runs in one of two modes:

| Mode | How to run it | What it does |
|---|---|---|
| **Dry run** (default) | No `--apply` | Reads the portal, compares it with the CSV and prints what *would* change. Sends no updates. |
| **Apply** | Add `--apply` | Same comparison, then sends a `PATCH` for each rule that differs, changing only the fields that differ. |

Other alert rule fields are never touched, and nothing is created or deleted. You can safely
run it more than once: rules that already match the CSV print `no changes needed` and are skipped.

All commands below run in PowerShell from the **repo root**, not from inside `scripts\`.

---

## 1. Set up (once)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item config\lm_config.template.json config\lm_config.json
notepad config\lm_config.json
```

Fill in the config file:

```json
{
  "portal": "your-portal",
  "access_id": "<LM API access ID>",
  "access_key": "<LM API access key>",
  "api_version": "3"
}
```

| Key | Required | Notes |
|---|---|---|
| `portal` | Yes | The subdomain only (`your-portal`, not the full URL) |
| `access_id`, `access_key` | Yes | An LM API token for a user who can **view and manage alert rules** and **view escalation chains** |
| `api_version` | No | Defaults to `3` |
| `page_size` | No | Defaults to `1000` |
| `endpoints` | No | `{"alert_rules": "...", "escalation_chains": "..."}`. Only set this if the defaults don't work |

> `config\lm_config.json` holds secrets. It's git-ignored, so never force-add it, email it or zip it.

Put the target CSV at `input\alert_rule_escalations.csv` (see [CSV format](#csv-format)).

In each new PowerShell session, reactivate the virtual environment before running the script:
`.\.venv\Scripts\Activate.ps1`

## 2. Connectivity check (read-only)

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --smoke-test
```

Expected output: `Smoke test: <N> alert rules, <M> escalation chains.`, followed by 5 sample rules.
An HTTP 401 or 403 means the credentials or the user's permissions are wrong.

## 3. Dry run (read-only, always do this first)

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  2>&1 | Tee-Object -FilePath logs\dry_run_$(Get-Date -Format yyyyMMdd_HHmm).log
```

`Tee-Object` shows the output on screen and saves it to `logs\` at the same time. **Keep this
log:** it's the only record of each rule's previous values, and you'll need it to roll back.

To preview only the first few rows, add `--limit 5`.

### Reading the output

```
Mode: DRY RUN                               <- confirm this before anything else
...
Loaded 161 alert rules.
Loaded 61 escalation chains.

[row 2] rule id=30 name='Sev 1 Circuit Down' matched by id=30
  planned changes:
    escalatingChainId: 2 -> 17              <- current value -> CSV value
  DRY RUN: no API update sent

[row 156] rule id=3 name='Critical' matched by id=3
  no changes needed                         <- already matches the CSV

Summary
  rows seen:          155
  rows with changes:  107                   <- rules that WILL change with --apply
  rows unchanged:     48
  rules updated:      0                     <- always 0 in a dry run
  rows skipped:       0
  warnings:           0
  errors:             0
```

`[row N]` is the line number in the CSV file. Row 2 is the first data row, after the header.

### Go / no-go checklist

Go ahead with the real run only if **all** of these are true:

- [ ] `Mode: DRY RUN`
- [ ] `rows seen` equals the number of data rows in the CSV (155 for your-portal)
- [ ] `rows with changes` is roughly what you expected (~107 at the Sep 17 baseline; it's fine
      if it's lower because someone already fixed some rules)
- [ ] `warnings: 0`
- [ ] `errors: 0`

If there are any warnings or errors, **stop** and investigate (see [Troubleshooting](#troubleshooting)).

## 4. Run it for real

### 4a. Pilot one rule

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --apply --limit 1 `
  2>&1 | Tee-Object -FilePath logs\apply_pilot_$(Get-Date -Format yyyyMMdd_HHmm).log
```

This processes only the first CSV row. Check the result in the portal under
**Settings > Alert Rules**: the rule's escalation chain and interval should match the CSV.

> `--limit` counts rows, not changes. If the first row already matches the CSV, the pilot
> prints `no changes needed` and updates nothing. In that case, use a larger `--limit`, until
> the summary shows `rules updated: 1`.

### 4b. Full run

```powershell
python scripts\update_alert_rules_csv.py `
  --config config\lm_config.json `
  --csv input\alert_rule_escalations.csv `
  --apply `
  2>&1 | Tee-Object -FilePath logs\apply_$(Get-Date -Format yyyyMMdd_HHmm).log
```

- Check the first line says `Mode: APPLY`.
- Each changed rule prints `UPDATED`.
- `rules updated` in the summary should equal the dry run's `rows with changes`, minus any rules
  the pilot already updated.

Rows the pilot already updated print `no changes needed` this time. That's expected.

### 4c. Verify

Run the dry run (step 3) again. Expected: `rows with changes: 0` and `errors: 0`.

---

## CSV format

The first row is a header. Columns are found by name, so their order doesn't matter.

| Column | Required | Accepted header names | Meaning |
|---|---|---|---|
| Rule ID | `id` or `name` | `id`, `rule_id`, `ruleId`, `alert_rule_id`, `alertRuleId` | LM alert rule ID. The preferred way to match a rule |
| Rule name | `id` or `name` | `name`, `rule_name`, `ruleName`, `alert_rule_name` | Used to match a rule if the ID is blank or not found |
| Chain ID | Yes | `escalatingChainId`, `escalating_chain_id`, `chain_id`, `chainId` | Target escalation chain ID (integer) |
| Interval | Yes | `escalationInterval`, `escalation_interval`, `interval` | Target escalation interval in minutes (integer) |
| Chain name | No | `escalatingChain`, `escalating_chain`, `chain_name`, `chainName` | Only used to check the chain ID. If it doesn't match the live chain's name, the script warns |

Example (illustrative values):

```csv
id,name,escalatingChainId,escalationInterval,escalatingChain
30,Sev 1 Circuit Down,17,15,Sev 1 SN Incident TH Circuit
```

If your headers have different names, map them with `--id-column`, `--name-column`,
`--chain-id-column`, `--interval-column` or `--chain-name-column`.

## How a row is processed

1. The rule is looked up **by ID**. If the CSV name differs from the live name, the script warns
   and still uses the ID match.
2. If the ID doesn't exist, the script falls back to matching **by name** and warns.
3. The live `escalatingChainId` and `escalationInterval` are compared with the CSV values.
4. With `--apply` only, a `PATCH /setting/alert/rules/{id}` is sent with just the fields that
   differ.

The script loads all rules and chains once, at the start of each run.

## All options

| Option | Description |
|---|---|
| `--config PATH` (required) | JSON config with `portal`, `access_id`, `access_key` |
| `--csv PATH` (required) | Target CSV |
| `--apply` | Send the updates. Leave it out for a dry run |
| `--limit N` | Process only the first N data rows |
| `--smoke-test` | Print counts and 5 sample rules, then exit. The CSV is still read to check its headers |
| `--timeout-seconds N` | HTTP timeout per request (default 60) |
| `--id-column`, `--name-column`, `--chain-id-column`, `--interval-column`, `--chain-name-column` | Exact CSV header names, when they differ from the defaults |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Finished, but one or more rows failed (look for `ERROR` lines). For `--smoke-test`: no rules returned |
| 2 | Fatal error: bad or missing config, missing CSV columns, authentication failure, etc. |

## Troubleshooting

| Message | Cause | What to do |
|---|---|---|
| `HTTP 401` / `HTTP 403` | Wrong token, or the user is missing permissions | Check `access_id`/`access_key` and the user's role |
| `Missing required config value: portal` | `portal` is blank in the config | Set it to `your-portal` |
| `CSV must include escalatingChainId ...` | Header not recognized | Rename the header, or pass `--chain-id-column` |
| `WARNING: CSV id=X maps to name=...` | The rule was renamed, or the CSV has the wrong ID | Confirm in the portal which rule is meant before applying |
| `WARNING: escalatingChainId X is named ...` | The chain ID in the CSV doesn't match the chain name in the CSV | Fix the CSV. Don't apply until resolved |
| `ERROR: No alert rule found with id=X` | The rule was deleted, or the ID is wrong | Fix the row, or remove it from the CSV |
| `Invalid integer for ...` | Chain ID or interval isn't a number | Fix the CSV value |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | Run `Set-ExecutionPolicy -Scope Process Bypass`, then activate again |

## Rollback

There's no automatic rollback. The dry-run log taken right before `--apply` shows each changed
field's previous value (the left side of every `old -> new` line). To revert, build a CSV with
those old values and run the script again with `--apply`, doing the dry run first as usual.
