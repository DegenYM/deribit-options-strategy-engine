from __future__ import annotations

import argparse
from pathlib import Path

from ..env_layout import find_repo_root
from .common import render


def register_parsers(subparsers: argparse._SubParsersAction) -> None:
    investor_parser = subparsers.add_parser(
        "investor",
        help="Investor onboarding: init, import handoff, validate, list (ops registry separate from accounts.toml)",
    )
    investor_sub = investor_parser.add_subparsers(dest="investor_command", required=True)

    inv_init = investor_sub.add_parser("init", help="Scaffold config/investors/<id>/ and registry row")
    inv_init.add_argument("investor_id", metavar="ID", help="New investor id (lowercase)")
    inv_init.add_argument(
        "--strategies",
        default="naked",
        help="Comma-separated strategy slugs: naked, bull_put, covered_call (default: naked)",
    )
    inv_init.add_argument("--display-name", default=None, help="Display name for manifest/registry")
    inv_init.add_argument("--email", default=None, help="Dashboard Access email (stored in registry only)")
    inv_init.add_argument(
        "--deribit-env",
        default="mainnet",
        choices=("mainnet", "testnet"),
        help="DERIBIT_ENV written into scaffolded account env files (default: mainnet)",
    )
    inv_init.add_argument(
        "--no-register",
        action="store_true",
        help="Skip appending [[investors]] to config/platform/registry.toml",
    )
    inv_init.add_argument("--json", action="store_true", help="Emit JSON")

    inv_import = investor_sub.add_parser(
        "import-handoff",
        help="Import secrets from handoff TOML into accounts/.env.*",
    )
    inv_import.add_argument("handoff_file", metavar="PATH", help="Handoff TOML path")
    inv_import.add_argument("--investor", metavar="ID", default=None, help="Override [investor].id")
    inv_import.add_argument("--json", action="store_true", help="Emit JSON")

    inv_validate = investor_sub.add_parser("validate", help="Check manifest, registry, and Deribit API auth")
    inv_validate.add_argument("investor_id", metavar="ID")
    inv_validate.add_argument("--no-api", action="store_true", help="Skip live Deribit API checks")
    inv_validate.add_argument(
        "--no-bootstrap-hwm",
        action="store_true",
        help="Skip automatic initial HWM bootstrap after successful API checks",
    )
    inv_validate.add_argument("--json", action="store_true", help="Emit JSON")

    inv_bootstrap = investor_sub.add_parser(
        "bootstrap-hwm",
        help="Bootstrap initial HWM from transaction log (or INITIAL_HWM_NAV_PERF)",
    )
    inv_bootstrap.add_argument("investor_id", metavar="ID")
    inv_bootstrap.add_argument(
        "--force",
        action="store_true",
        help="Re-run bootstrap even if fee ledger already has HWM / flow baseline",
    )
    inv_bootstrap.add_argument("--json", action="store_true", help="Emit JSON")

    inv_list = investor_sub.add_parser("list", help="List investors from registry and disk")
    inv_list.add_argument("--json", action="store_true", help="Emit JSON")

    inv_launchd = investor_sub.add_parser(
        "render-launchd",
        help="Write launchd plists to config/platform/generated/launchd/",
    )
    inv_launchd.add_argument("investor_id", metavar="ID")
    inv_launchd.add_argument("--port", type=int, default=None, help="Override frontend port in plist")
    inv_launchd.add_argument("--json", action="store_true", help="Emit JSON")

    inv_systemd = investor_sub.add_parser(
        "render-systemd",
        help="Write systemd units to config/platform/generated/systemd/",
    )
    inv_systemd.add_argument("investor_id", metavar="ID")
    inv_systemd.add_argument("--port", type=int, default=None, help="Override frontend port in unit")
    inv_systemd.add_argument("--json", action="store_true", help="Emit JSON")

    inv_frontend = investor_sub.add_parser(
        "frontend",
        help="Start/stop/restart/status all investor frontends via launchd (macOS)",
    )
    inv_frontend_sub = inv_frontend.add_subparsers(dest="frontend_command", required=True)
    for action in ("start", "stop", "restart", "status"):
        p = inv_frontend_sub.add_parser(action, help=f"{action} frontend LaunchAgent(s)")
        p.add_argument(
            "--investor",
            metavar="ID",
            default=None,
            help="Only this investor (default: all frontend_enabled in registry.toml)",
        )
        p.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include registry rows with frontend_enabled=false",
        )
        p.add_argument(
            "--no-health",
            action="store_true",
            help="Skip local http://127.0.0.1:<port>/api/health probe",
        )
        p.add_argument("--json", action="store_true", help="Emit JSON")

    inv_live = investor_sub.add_parser(
        "live",
        help="Start/stop/restart/status all investor live bots via launchd (macOS)",
    )
    inv_live_sub = inv_live.add_subparsers(dest="live_command", required=True)
    for action in ("start", "stop", "restart", "status"):
        p = inv_live_sub.add_parser(action, help=f"{action} live LaunchAgent(s)")
        p.add_argument(
            "--investor",
            metavar="ID",
            default=None,
            help="Only this investor (default: all live_enabled in registry.toml)",
        )
        p.add_argument(
            "--include-disabled",
            action="store_true",
            help="Include registry rows with live_enabled=false",
        )
        p.add_argument(
            "--no-supervisor-check",
            action="store_true",
            help="Skip waiting for logs/live/<id>/supervisor.log started pid=",
        )
        p.add_argument("--json", action="store_true", help="Emit JSON")


def dispatch(args: argparse.Namespace) -> int | None:
    if args.command != "investor":
        return None

    from ..investor_ops import (
        bootstrap_initial_hwm,
        import_handoff,
        investor_init,
        list_investors,
        parse_strategy_slugs,
        render_launchd_plists,
        render_systemd_units,
        validate_investor,
    )

    repo_root = find_repo_root(Path.cwd())
    if repo_root is None:
        raise SystemExit("Cannot locate repository root")

    if args.investor_command == "init":
        strategies = parse_strategy_slugs(args.strategies)
        result = investor_init(
            args.investor_id,
            strategies=strategies,
            display_name=args.display_name,
            dashboard_email=args.email,
            deribit_env=args.deribit_env,
            register=not args.no_register,
            repo_root=repo_root,
        )
        payload = {
            "action": "investor-init",
            "investor_id": result.investor_id,
            "investor_dir": str(result.investor_dir),
            "strategies": list(result.strategies),
            "frontend_port": result.frontend_port,
            "launchd_paths": [str(path) for path in result.launchd_paths],
            "systemd_paths": [str(path) for path in result.systemd_paths],
            "next_steps": [
                "Fill secrets: ./bot investor import-handoff config/handoff/<id>.toml",
                f"Validate + initial HWM: ./bot investor validate {result.investor_id}",
                "Install launchd (macOS) or systemd (Linux): see docs/operator-onboarding-zh-TW.md",
            ],
        }
        render(payload, args.json)
        return 0 if result.investor_id else 1

    if args.investor_command == "import-handoff":
        outcome = import_handoff(
            Path(args.handoff_file),
            investor_id=args.investor,
            repo_root=repo_root,
        )
        render({"action": "investor-import-handoff", **outcome}, args.json)
        return 0

    if args.investor_command == "validate":
        result = validate_investor(
            args.investor_id,
            check_api=not args.no_api,
            bootstrap_hwm=not args.no_bootstrap_hwm,
            repo_root=repo_root,
        )
        payload = {
            "action": "investor-validate",
            "investor_id": result.investor_id,
            "ok": result.ok,
            "issues": [{"level": issue.level, "code": issue.code, "message": issue.message} for issue in result.issues],
            "api_checks": list(result.api_checks),
            "hwm_bootstrap": result.hwm_bootstrap,
        }
        render(payload, args.json)
        return 0 if result.ok else 1

    if args.investor_command == "bootstrap-hwm":
        outcome = bootstrap_initial_hwm(
            args.investor_id,
            repo_root=repo_root,
            force=args.force,
        )
        render({"action": "investor-bootstrap-hwm", **outcome}, args.json)
        return 0

    if args.investor_command == "list":
        rows = list_investors(repo_root=repo_root)
        render({"action": "investor-list", "investors": rows}, args.json)
        return 0

    if args.investor_command == "render-launchd":
        from ..investor_registry import load_platform_registry

        registry = load_platform_registry(repo_root=repo_root)
        paths = render_launchd_plists(
            args.investor_id,
            repo_root=repo_root,
            registry=registry,
            frontend_port=args.port,
        )
        render(
            {
                "action": "investor-render-launchd",
                "investor_id": args.investor_id,
                "paths": [str(path) for path in paths],
            },
            args.json,
        )
        return 0

    if args.investor_command == "render-systemd":
        from ..investor_registry import load_platform_registry

        registry = load_platform_registry(repo_root=repo_root)
        paths = render_systemd_units(
            args.investor_id,
            repo_root=repo_root,
            registry=registry,
            frontend_port=args.port,
        )
        render(
            {
                "action": "investor-render-systemd",
                "investor_id": args.investor_id,
                "paths": [str(path) for path in paths],
            },
            args.json,
        )
        return 0

    if args.investor_command == "frontend":
        from ..investor_frontend_launchd import manage_frontend_launchd

        results = manage_frontend_launchd(
            args.frontend_command,
            repo_root=repo_root,
            investor_id=args.investor,
            include_disabled=args.include_disabled,
            check_health=not args.no_health,
        )
        payload = {
            "action": f"investor-frontend-{args.frontend_command}",
            "results": [row.to_dict() for row in results],
        }
        render(payload, args.json)
        if not args.json:
            for row in results:
                port = row.frontend_port if row.frontend_port is not None else "?"
                health = ""
                if row.health_ok is not None:
                    health = " health=" + ("ok" if row.health_ok else "fail")
                mark = "ok" if row.ok else "FAIL"
                print(f"[{mark}] {row.investor_id} :{port} {row.state} — {row.message}{health}")
        return 0 if all(row.ok for row in results) else 1

    if args.investor_command == "live":
        from ..investor_live_launchd import manage_live_launchd

        results = manage_live_launchd(
            args.live_command,
            repo_root=repo_root,
            investor_id=args.investor,
            include_disabled=args.include_disabled,
            check_supervisor=not args.no_supervisor_check,
        )
        payload = {
            "action": f"investor-live-{args.live_command}",
            "results": [row.to_dict() for row in results],
        }
        render(payload, args.json)
        if not args.json:
            for row in results:
                supervisor = ""
                if row.supervisor_ok is not None:
                    supervisor = " supervisor=" + ("ok" if row.supervisor_ok else "fail")
                mark = "ok" if row.ok else "FAIL"
                print(f"[{mark}] {row.investor_id} {row.state} — {row.message}{supervisor}")
        return 0 if all(row.ok for row in results) else 1

    raise SystemExit(2)
