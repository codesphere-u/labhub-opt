"""CLI entrypoint for labhub-opt."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .hub import LabHubSpec, evaluate_hub_capacity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="labhub-opt",
        description="Evaluate TB/HIV GeneXpert diagnostic hub throughput, utilization, and expansion needs.",
    )
    parser.add_argument("--hub", default="HUB-LIRA-01", help="Lab hub identifier.")
    parser.add_argument("--modules", type=int, default=4, help="Functional GeneXpert modules (e.g. 4 or 16).")
    parser.add_argument("--tb-daily", type=float, default=14.0, help="Daily Xpert MTB/RIF Ultra samples.")
    parser.add_argument("--hiv-vl-daily", type=float, default=4.0, help="Daily Xpert HIV viral-load samples.")
    parser.add_argument("--no-ups", action="store_true", help="Flag if hub lacks UPS/solar power protection.")
    args = parser.parse_args(argv)

    rep = evaluate_hub_capacity(
        LabHubSpec(
            hub_id=args.hub,
            functional_modules=args.modules,
            daily_sample_demand={"xpert_mtb_rif_ultra": args.tb_daily, "xpert_hiv_vl": args.hiv_vl_daily},
            ups_backup_installed=not args.no_ups,
        )
    )
    print(json.dumps(asdict(rep), indent=2))
    return 0 if not rep.overloaded else 1


if __name__ == "__main__":
    raise SystemExit(main())
