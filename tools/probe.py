#!/usr/bin/env python3
"""Standalone probe for a Doremi/Dolby cinema server.

Runs outside Home Assistant, with no dependencies beyond the standard library.
Use it to verify this integration's protocol assumptions against real hardware
and to produce a report that can be pasted into a GitHub issue.

    python3 tools/probe.py 10.0.0.50
    python3 tools/probe.py 10.0.0.50 --command GetSPLList
    python3 tools/probe.py 10.0.0.50 --full --raw > report.txt

Read-only by default: it issues no command that changes server state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import load  # noqa: E402

klv, api = load()
DEFAULT_PORT = api.DEFAULT_PORT
IMSClient = api.IMSClient
IMSCommandError = api.IMSCommandError
IMSConnectionError = api.IMSConnectionError
REQUEST_BY_NAME = klv.REQUEST_BY_NAME

# Commands that only read.  Anything mutating is deliberately excluded.
READ_ONLY = [
    ("GetProductInfo", {}),
    ("GetAPIProtocolVersion", {}),
    ("GetTimeZone", {}),
    ("WhoAmI", {}),
    ("StatusSPL2", {"flags": 0}),
    ("StatusSPL", {}),
    ("GetSchedulerEnable", {}),
    ("GetCurrentSchedule", {}),
    ("GetNextSchedule", {}),
    ("GetSPLList", {}),
    ("GetCPLList", {}),
    ("GetKDMList", {}),
]


def _fmt(value: Any) -> Any:
    if isinstance(value, list):
        if len(value) > 6:
            return value[:6] + [f"... {len(value) - 6} more"]
        return value
    return value


async def run_one(client: IMSClient, name: str, params: dict[str, Any]) -> None:
    label = f"{name}({', '.join(f'{k}={v}' for k, v in params.items())})"
    try:
        result = await client.command(name, _check_return_code=False, **params)
    except IMSCommandError as err:
        print(f"  {label}\n    -> command error: {err}")
        return
    except IMSConnectionError as err:
        print(f"  {label}\n    -> CONNECTION LOST: {err}")
        raise
    except Exception as err:  # noqa: BLE001
        print(f"  {label}\n    -> decode failure: {type(err).__name__}: {err}")
        return

    print(f"  {label}")
    for key, value in result.items():
        print(f"    {key:32} {_fmt(value)}")


async def probe(args: argparse.Namespace) -> int:
    client = IMSClient(host=args.host, port=args.port, timeout=args.timeout)

    print(f"# Dolby/Doremi cinema server probe")
    print(f"# target {args.host}:{args.port}\n")

    try:
        await client.connect()
    except IMSConnectionError as err:
        print(f"FAILED: {err}")
        print(
            "\nThings to check:\n"
            "  - is TCP 11730 reachable (try: nc -vz HOST 11730)?\n"
            "  - is a firewall or VLAN between HA and the projection network?\n"
        )
        return 1
    print("connected ok\n")

    try:
        if args.command:
            params = json.loads(args.params) if args.params else {}
            await run_one(client, args.command, params)
            return 0

        print("## read-only commands")
        for name, params in READ_ONLY:
            await run_one(client, name, params)

        if args.full:
            print("\n## per-composition detail (first 5)")
            try:
                cpls = await client.cpl_list()
            except Exception as err:  # noqa: BLE001
                print(f"  could not list compositions: {err}")
                cpls = []
            for cpl in cpls[:5]:
                await run_one(client, "GetCPLInfo2", {"uuid": cpl})

            print("\n## per-key detail (first 3)")
            try:
                kdms = await client.kdm_list()
            except Exception as err:  # noqa: BLE001
                print(f"  could not list keys: {err}")
                kdms = []
            for kdm in kdms[:3]:
                await run_one(client, "GetKDMInfo2", {"uuid": kdm})
    finally:
        await client.disconnect()

    print(
        "\n## please report\n"
        "  - anything above marked 'decode failure' or 'unknown key'\n"
        "  - whether playlist position/duration look like seconds or frames\n"
        "  - the product name and software version reported\n"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--full", action="store_true", help="also dump per-CPL and per-KDM detail")
    parser.add_argument("--raw", action="store_true", help="include raw hex for unknown fields")
    parser.add_argument("--command", help="run a single named command instead of the sweep")
    parser.add_argument("--params", help="JSON object of parameters for --command")
    parser.add_argument(
        "--list-commands", action="store_true", help="print every command this build knows"
    )
    args = parser.parse_args()

    if args.list_commands:
        for name in sorted(REQUEST_BY_NAME):
            req = REQUEST_BY_NAME[name]
            arglist = ", ".join(a.name for a in req.args) or "-"
            print(f"{name:24} key={req.key.hex()}  args: {arglist}")
        return 0

    return asyncio.run(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
