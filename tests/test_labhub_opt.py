"""Tests for labhub-opt."""

from __future__ import annotations

import json
from datetime import date

import pytest

from labhub_opt import (
    CartridgeLot,
    LabHubSpec,
    SpokeClinic,
    audit_courier_network,
    evaluate_hub_capacity,
    quantify_cartridges,
)
from labhub_opt.cli import main as cli_main


def test_overloaded_gx4_hub_recommends_expansion_and_ups_guard() -> None:
    rep = evaluate_hub_capacity(
        LabHubSpec(
            hub_id="HUB-ARUA",
            functional_modules=4,
            daily_sample_demand={"xpert_mtb_rif_ultra": 22.0, "xpert_hiv_vl": 4.0},
            ups_backup_installed=False,
        )
    )
    assert rep.overloaded is True
    assert rep.additional_gx4_machines_needed >= 1
    assert any("NO UPS" in w for w in rep.warnings)
    assert any("CAPACITY BOTTLENECK" in w for w in rep.warnings)


def test_fefo_cartridge_quantification_catches_unconsumable_expiry_waste() -> None:
    today = date(2026, 11, 25)
    lots = [
        # 500 cartridges expiring in 30 days when consumption is only 200/month => 300 will expire!
        CartridgeLot("L1", "xpert_mtb_rif_ultra", 500, date(2026, 12, 25)),
        CartridgeLot("L0", "xpert_mtb_rif_ultra", 20, date(2026, 11, 1)),
    ]
    plan = quantify_cartridges(lots, assay="xpert_mtb_rif_ultra", monthly_consumption=200, as_of=today)
    assert plan.expired_stock_count == 20
    assert plan.projected_expiry_waste >= 290
    assert plan.usable_stock_on_hand <= 210
    assert plan.status == "stockout_risk"


def test_courier_network_48h_sla_and_cold_box_viability() -> None:
    spokes = [
        SpokeClinic("HC3-A", round_trip_km=42.0, weekly_pickup_days=2, cold_box_2_8c_available=False),
        SpokeClinic("HC3-B", round_trip_km=28.0, weekly_pickup_days=5, cold_box_2_8c_available=True),
    ]
    audit = audit_courier_network(spokes, lab_tat_hours=4.0)
    assert audit.sla_compliant_48h is False
    assert any("SPECIMEN VIABILITY RISK" in a for a in audit.alerts)


def test_cli_runs(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["--modules", "4", "--tb-daily", "10", "--hiv-vl-daily", "2"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["overloaded"] is False
