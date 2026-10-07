#!/usr/bin/env python3
"""
Update LogicMonitor alert rule escalation settings from a CSV file.

Default behavior is DRY RUN. Use --apply to PATCH changes.

CSV expectations:
  - id: LogicMonitor alert rule ID (preferred when numeric)
  - name: alert rule name (used when id is blank or non-numeric)
  - escalatingChainId: target escalation chain ID
  - escalationInterval: escalation interval in minutes
  - escalatingChain: optional chain name for validation only

Examples:
  python scripts/update_alert_rules_csv.py \\
    --config config/lm_export_config.json \\
    --csv input/alert_rule_escalations.csv

  python scripts/update_alert_rules_csv.py \\
    --config config/lm_export_config.json \\
    --csv input/alert_rule_escalations.csv --apply
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


DEFAULT_ALERT_RULES_ENDPOINT = "/setting/alert/rules"
DEFAULT_ESCALATION_CHAINS_ENDPOINT = "/setting/alert/chains"
DEFAULT_PAGE_SIZE = 1000
REQUEST_TIMEOUT = 60
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

ID_COLUMN_CANDIDATES = ("id", "rule_id", "ruleId", "alert_rule_id", "alertRuleId")
NAME_COLUMN_CANDIDATES = ("name", "rule_name", "ruleName", "alert_rule_name")
CHAIN_ID_COLUMN_CANDIDATES = ("escalatingChainId", "escalating_chain_id", "chain_id", "chainId")
INTERVAL_COLUMN_CANDIDATES = ("escalationInterval", "escalation_interval", "interval")
CHAIN_NAME_COLUMN_CANDIDATES = ("escalatingChain", "escalating_chain", "chain_name", "chainName")


@dataclass
class ScriptStats:
    rows_seen: int = 0
    rows_with_changes: int = 0
    rows_unchanged: int = 0
    rows_skipped: int = 0
    rules_updated: int = 0
    warnings: int = 0
    errors: int = 0


class LogicMonitorApiError(RuntimeError):
    """Raised when the LogicMonitor API returns an unsuccessful response."""


def require_value(value: str, key_name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"Missing required config value: {key_name}")
    return value


def pick_first_existing(headers: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    header_set = set(headers)
    for candidate in candidates:
        if candidate in header_set:
            return candidate
    return None


def parse_csv(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header row: {path}")
        rows = [{key: (value or "").strip() for key, value in row.items()} for row in reader]
        return list(reader.fieldnames), rows


def parse_int(value: str, field_name: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing required integer field: {field_name}")
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {field_name}: {value!r}") from exc


def parse_optional_rule_id(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text or text.lower() == "id":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def response_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return payload["items"]
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data, list):
            return data
    if isinstance(payload, list):
        return payload
    return []


def unwrap_single_resource(response_json: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = response_json.get("data", response_json)
    if isinstance(data, dict) and "items" in data:
        items = data.get("items") or []
        return items[0] if items else None
    if isinstance(data, dict):
        return data
    return None


def get_total(payload: Any) -> Optional[int]:
    if isinstance(payload, dict):
        for key in ("total", "totalCount", "total_count"):
            if isinstance(payload.get(key), int):
                return payload[key]
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("total", "totalCount", "total_count"):
                if isinstance(data.get(key), int):
                    return data[key]
    return None


class LogicMonitorClient:
    def __init__(
        self,
        portal: str,
        access_id: str,
        access_key: str,
        alert_rules_endpoint: str = DEFAULT_ALERT_RULES_ENDPOINT,
        escalation_chains_endpoint: str = DEFAULT_ESCALATION_CHAINS_ENDPOINT,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout_seconds: int = REQUEST_TIMEOUT,
        api_version: str = "3",
    ) -> None:
        self.portal = require_value(portal, "portal")
        self.access_id = require_value(access_id, "access_id")
        self.access_key = require_value(access_key, "access_key")
        self.alert_rules_endpoint = alert_rules_endpoint or DEFAULT_ALERT_RULES_ENDPOINT
        self.escalation_chains_endpoint = escalation_chains_endpoint or DEFAULT_ESCALATION_CHAINS_ENDPOINT
        self.page_size = page_size
        self.timeout_seconds = timeout_seconds
        self.api_version = str(api_version or "3").strip()
        self.base_url = f"https://{self.portal}.logicmonitor.com/santaba/rest"
        self.session = requests.Session()

    @classmethod
    def from_config(cls, config_path: str, timeout_seconds: int = REQUEST_TIMEOUT) -> "LogicMonitorClient":
        with open(config_path, "r", encoding="utf-8") as file_handle:
            config = json.load(file_handle)
        endpoints = config.get("endpoints") or {}
        return cls(
            portal=config.get("portal", ""),
            access_id=config.get("access_id", ""),
            access_key=config.get("access_key", ""),
            alert_rules_endpoint=endpoints.get("alert_rules", DEFAULT_ALERT_RULES_ENDPOINT),
            escalation_chains_endpoint=endpoints.get("escalation_chains", DEFAULT_ESCALATION_CHAINS_ENDPOINT),
            page_size=int(config.get("page_size", DEFAULT_PAGE_SIZE)),
            timeout_seconds=timeout_seconds,
            api_version=str(config.get("api_version", "3") or "3"),
        )

    def _authorization_header(self, method: str, resource_path: str, body: str) -> str:
        epoch = str(int(time.time() * 1000))
        request_vars = method.upper() + epoch + body + resource_path
        signature_hex = hmac.new(
            self.access_key.encode("utf-8"),
            msg=request_vars.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        signature = base64.b64encode(signature_hex.encode("utf-8")).decode("utf-8")
        return f"LMv1 {self.access_id}:{signature}:{epoch}"

    def request(
        self,
        method: str,
        resource_path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body = ""
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        headers = {
            "Authorization": self._authorization_header(method, resource_path, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_version:
            headers["X-Version"] = self.api_version

        response = self.session.request(
            method.upper(),
            self.base_url + resource_path,
            params=params,
            data=body if body else None,
            headers=headers,
            timeout=self.timeout_seconds,
        )

        try:
            response_json = response.json()
        except ValueError as exc:
            raise LogicMonitorApiError(
                f"{method.upper()} {resource_path} returned HTTP {response.status_code}; "
                f"response was not JSON: {response.text[:500]}"
            ) from exc

        if not response.ok:
            message = (
                response_json.get("errmsg")
                or response_json.get("message")
                or response_json.get("errorMessage")
                or response.text[:500]
            )
            raise LogicMonitorApiError(
                f"{method.upper()} {resource_path} returned HTTP {response.status_code}: {message}"
            )

        body_status = response_json.get("status")
        if isinstance(body_status, int) and body_status not in (200, 202):
            message = response_json.get("errmsg") or response_json.get("message") or response.text[:500]
            raise LogicMonitorApiError(
                f"{method.upper()} {resource_path} returned LM status {body_status}: {message}"
            )

        return response_json

    def _get_with_retry(
        self,
        resource_path: str,
        params: Dict[str, Any],
        max_retries: int = 5,
    ) -> requests.Response:
        delay = 2
        url = self.base_url + resource_path
        for attempt in range(1, max_retries + 1):
            body = ""
            headers = {
                "Authorization": self._authorization_header("GET", resource_path, body),
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if self.api_version:
                headers["X-Version"] = self.api_version
            response = self.session.get(
                url,
                headers=headers,
                params=params,
                timeout=self.timeout_seconds,
            )
            if response.status_code not in RETRYABLE_STATUS:
                return response
            if attempt == max_retries:
                return response
            time.sleep(delay)
            delay = min(delay * 2, 30)
        return response

    def fetch_collection(self, resource_path: str) -> List[Dict[str, Any]]:
        all_items: List[Dict[str, Any]] = []
        offset = 0
        total: Optional[int] = None

        while True:
            params = {"size": self.page_size, "offset": offset}
            response = self._get_with_retry(resource_path, params)
            if response.status_code >= 400:
                raise LogicMonitorApiError(
                    f"GET {resource_path} failed with HTTP {response.status_code}: {response.text[:500]}"
                )
            payload = response.json()
            items = response_items(payload)
            if total is None:
                total = get_total(payload)
            if not items:
                break
            all_items.extend(items)
            offset += len(items)
            if len(items) < self.page_size:
                break
            if total is not None and offset >= total:
                break
        return all_items

    def fetch_alert_rules(self) -> List[Dict[str, Any]]:
        return self.fetch_collection(self.alert_rules_endpoint)

    def fetch_escalation_chains(self) -> List[Dict[str, Any]]:
        return self.fetch_collection(self.escalation_chains_endpoint)

    def get_alert_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        resource_path = f"{self.alert_rules_endpoint}/{rule_id}"
        response_json = self.request("GET", resource_path)
        return unwrap_single_resource(response_json)

    def patch_alert_rule(self, rule_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        resource_path = f"{self.alert_rules_endpoint}/{rule_id}"
        return self.request("PATCH", resource_path, payload=payload)


def resolve_columns(headers: Sequence[str], args: argparse.Namespace) -> Tuple[str, str, str, str, Optional[str]]:
    id_column = args.id_column or pick_first_existing(headers, ID_COLUMN_CANDIDATES)
    name_column = args.name_column or pick_first_existing(headers, NAME_COLUMN_CANDIDATES)
    chain_id_column = args.chain_id_column or pick_first_existing(headers, CHAIN_ID_COLUMN_CANDIDATES)
    interval_column = args.interval_column or pick_first_existing(headers, INTERVAL_COLUMN_CANDIDATES)
    chain_name_column = args.chain_name_column or pick_first_existing(headers, CHAIN_NAME_COLUMN_CANDIDATES)

    if not chain_id_column:
        raise ValueError("CSV must include escalatingChainId (or pass --chain-id-column).")
    if not interval_column:
        raise ValueError("CSV must include escalationInterval (or pass --interval-column).")
    if not id_column and not name_column:
        raise ValueError("CSV must include id and/or name (or pass --id-column / --name-column).")

    return id_column or "", name_column or "", chain_id_column, interval_column, chain_name_column


def index_rules(rules: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_id: Dict[int, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for rule in rules:
        rule_id = rule.get("id")
        if isinstance(rule_id, int):
            by_id[rule_id] = rule
        name = str(rule.get("name") or "").strip()
        if name:
            by_name[name] = rule
    return by_id, by_name


def index_chain_names(chains: List[Dict[str, Any]]) -> Dict[int, str]:
    return {
        int(chain["id"]): str(chain.get("name") or "")
        for chain in chains
        if isinstance(chain.get("id"), int)
    }


def lookup_rule(
    row: Dict[str, str],
    *,
    id_column: str,
    name_column: str,
    rules_by_id: Dict[int, Dict[str, Any]],
    rules_by_name: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str, List[str]]:
    warnings: List[str] = []
    rule_id = parse_optional_rule_id(row.get(id_column, "")) if id_column else None
    name = str(row.get(name_column) or "").strip() if name_column else ""

    if rule_id is not None:
        rule = rules_by_id.get(rule_id)
        if rule:
            if name and str(rule.get("name") or "").strip() != name:
                warnings.append(
                    f"CSV id={rule_id} maps to name={rule.get('name')!r}, "
                    f"but CSV name is {name!r}; using id match"
                )
            return rule, f"id={rule_id}", warnings
        if name:
            warnings.append(f"No alert rule found with id={rule_id}; falling back to name={name!r}")
        else:
            raise LookupError(f"No alert rule found with id={rule_id}")

    if not name:
        raise LookupError("Row has no valid id and blank name.")

    rule = rules_by_name.get(name)
    if rule:
        return rule, f"name={name!r}", warnings
    raise LookupError(f"No alert rule found with name={name!r}")


def build_change_plan(
    rule: Dict[str, Any],
    desired_chain_id: int,
    desired_interval: int,
) -> Dict[str, Tuple[Any, Any]]:
    changes: Dict[str, Tuple[Any, Any]] = {}
    current_chain_id = rule.get("escalatingChainId")
    current_interval = rule.get("escalationInterval")

    if current_chain_id != desired_chain_id:
        changes["escalatingChainId"] = (current_chain_id, desired_chain_id)
    if current_interval != desired_interval:
        changes["escalationInterval"] = (current_interval, desired_interval)
    return changes


def print_change_plan(
    row_index: int,
    lookup_label: str,
    rule: Dict[str, Any],
    changes: Dict[str, Tuple[Any, Any]],
    warnings: List[str],
) -> None:
    print(
        f"[row {row_index}] rule id={rule.get('id')} name={rule.get('name')!r} matched by {lookup_label}"
    )
    if warnings:
        for warning in warnings:
            print(f"  WARNING: {warning}")
    if not changes:
        print("  no changes needed")
        return
    print("  planned changes:")
    for field_name, (old_value, new_value) in changes.items():
        print(f"    {field_name}: {old_value!r} -> {new_value!r}")


def process_rows(
    client: LogicMonitorClient,
    rows: List[Dict[str, str]],
    *,
    id_column: str,
    name_column: str,
    chain_id_column: str,
    interval_column: str,
    chain_name_column: Optional[str],
    apply_changes: bool,
    limit: Optional[int],
) -> ScriptStats:
    stats = ScriptStats()
    print("Fetching alert rules from LogicMonitor...")
    rules = client.fetch_alert_rules()
    print(f"Loaded {len(rules)} alert rules.")

    print("Fetching escalation chains from LogicMonitor...")
    chains = client.fetch_escalation_chains()
    chain_names = index_chain_names(chains)
    print(f"Loaded {len(chains)} escalation chains.")
    print()

    rules_by_id, rules_by_name = index_rules(rules)

    for index, row in enumerate(rows, start=2):
        if limit is not None and stats.rows_seen >= limit:
            break
        stats.rows_seen += 1

        try:
            desired_chain_id = parse_int(row.get(chain_id_column, ""), chain_id_column)
            desired_interval = parse_int(row.get(interval_column, ""), interval_column)
            rule, lookup_label, lookup_warnings = lookup_rule(
                row,
                id_column=id_column,
                name_column=name_column,
                rules_by_id=rules_by_id,
                rules_by_name=rules_by_name,
            )
            assert rule is not None

            warnings: List[str] = list(lookup_warnings)
            if lookup_warnings:
                stats.warnings += len(lookup_warnings)
            if chain_name_column:
                expected_name = str(row.get(chain_name_column) or "").strip()
                actual_name = chain_names.get(desired_chain_id, "")
                if expected_name and actual_name and expected_name != actual_name:
                    warnings.append(
                        f"escalatingChainId {desired_chain_id} is named {actual_name!r}, "
                        f"but CSV has {expected_name!r}"
                    )
                    stats.warnings += 1

            changes = build_change_plan(rule, desired_chain_id, desired_interval)
            print_change_plan(index, lookup_label, rule, changes, warnings)

            if not changes:
                stats.rows_unchanged += 1
                continue

            stats.rows_with_changes += 1
            if apply_changes:
                payload = {
                    field_name: new_value
                    for field_name, (_, new_value) in changes.items()
                }
                client.patch_alert_rule(int(rule["id"]), payload)
                stats.rules_updated += 1
                print("  UPDATED")
            else:
                print("  DRY RUN: no API update sent")

        except Exception as exc:
            stats.errors += 1
            stats.rows_skipped += 1
            print(f"[row {index}] ERROR: {exc}", file=sys.stderr)

    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update LogicMonitor alert rule escalation chain settings from CSV."
    )
    parser.add_argument("--config", required=True, help="Path to JSON config with portal/access_id/access_key.")
    parser.add_argument("--csv", required=True, help="Path to CSV with alert rule escalation targets.")
    parser.add_argument("--id-column", help="Exact CSV header containing alert rule id.")
    parser.add_argument("--name-column", help="Exact CSV header containing alert rule name.")
    parser.add_argument("--chain-id-column", help="Exact CSV header containing escalatingChainId.")
    parser.add_argument("--interval-column", help="Exact CSV header containing escalationInterval.")
    parser.add_argument("--chain-name-column", help="Exact CSV header containing escalatingChain name for validation.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="PATCH alert rules. Omit this flag for dry-run mode.",
    )
    parser.add_argument("--limit", type=int, help="Process only the first N data rows.")
    parser.add_argument("--timeout-seconds", type=int, default=REQUEST_TIMEOUT, help="HTTP timeout per request.")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="List a few alert rules and escalation chains, then exit.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        headers, rows = parse_csv(args.csv)
        id_column, name_column, chain_id_column, interval_column, chain_name_column = resolve_columns(headers, args)

        print("Mode:", "APPLY" if args.apply else "DRY RUN")
        print("ID column:", id_column or "<none>")
        print("Name column:", name_column or "<none>")
        print("Chain ID column:", chain_id_column)
        print("Interval column:", interval_column)
        print("Chain name column:", chain_name_column or "<none>")
        print()

        client = LogicMonitorClient.from_config(args.config, timeout_seconds=args.timeout_seconds)

        if args.smoke_test:
            rules = client.fetch_alert_rules()
            chains = client.fetch_escalation_chains()
            print(f"Smoke test: {len(rules)} alert rules, {len(chains)} escalation chains.")
            for rule in rules[:5]:
                print(
                    "  rule id={id} name={name!r} chainId={chainId} interval={interval}".format(
                        id=rule.get("id"),
                        name=rule.get("name"),
                        chainId=rule.get("escalatingChainId"),
                        interval=rule.get("escalationInterval"),
                    )
                )
            return 0 if rules else 1

        stats = process_rows(
            client,
            rows,
            id_column=id_column,
            name_column=name_column,
            chain_id_column=chain_id_column,
            interval_column=interval_column,
            chain_name_column=chain_name_column,
            apply_changes=args.apply,
            limit=args.limit,
        )

        print()
        print("Summary")
        print(f"  rows seen:          {stats.rows_seen}")
        print(f"  rows with changes:  {stats.rows_with_changes}")
        print(f"  rows unchanged:     {stats.rows_unchanged}")
        print(f"  rules updated:      {stats.rules_updated}")
        print(f"  rows skipped:       {stats.rows_skipped}")
        print(f"  warnings:           {stats.warnings}")
        print(f"  errors:             {stats.errors}")

        return 1 if stats.errors else 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
