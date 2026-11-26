"""TB/HIV GeneXpert diagnostic hub throughput, cartridge FEFO & hub-and-spoke courier SLA."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

# Cycle runtime (minutes) including specimen prep + module turnaround
ASSAY_RUN_MINUTES: dict[str, float] = {
    "xpert_mtb_rif_ultra": 90.0,
    "xpert_mtb_xdr": 95.0,
    "xpert_hiv_vl": 100.0,
    "xpert_eid": 100.0,
}


@dataclass(frozen=True)
class LabHubSpec:
    """District diagnostic hub GeneXpert instrument & power-reliability specification."""

    hub_id: str
    functional_modules: int  # e.g., 4 for GX-4, 16 for GX-16
    daily_shift_hours: float = 8.0
    operating_days_month: int = 22
    daily_sample_demand: dict[str, float] | None = None
    error_invalid_repeat_pct: float = 5.0
    power_outage_downtime_pct: float = 10.0  # Unprotected grid outages without UPS/solar
    ups_backup_installed: bool = True

    def __post_init__(self) -> None:
        if self.functional_modules <= 0 or self.daily_shift_hours <= 0 or self.operating_days_month <= 0:
            raise ValueError("functional_modules, daily_shift_hours, and operating_days_month must be positive")


@dataclass(frozen=True)
class HubCapacityReport:
    """GeneXpert module utilization, bottleneck detection, and turnaround time (TAT) forecast."""

    hub_id: str
    functional_modules: int
    effective_module_minutes_day: float
    required_module_minutes_day: float
    daily_tests_including_repeats: float
    utilization_pct: float
    overloaded: bool
    additional_gx4_machines_needed: int
    estimated_lab_tat_hours: float
    warnings: tuple[str, ...]


def evaluate_hub_capacity(spec: LabHubSpec) -> HubCapacityReport:
    """Audit GeneXpert hub throughput, utilization, repeat load, and backlog risk."""
    demand = spec.daily_sample_demand or {"xpert_mtb_rif_ultra": 12.0}
    downtime = 0.0 if spec.ups_backup_installed else (spec.power_outage_downtime_pct / 100.0)
    # 85% practical module duty factor accounts for batching gaps & maintenance
    eff_min_day = spec.functional_modules * spec.daily_shift_hours * 60.0 * (1.0 - downtime) * 0.85

    repeat_mult = 1.0 + (spec.error_invalid_repeat_pct / 100.0)
    req_min = 0.0
    tot_tests = 0.0
    for assay, count in demand.items():
        if count < 0:
            raise ValueError(f"Negative sample demand for {assay}")
        key = assay.strip().lower()
        if key not in ASSAY_RUN_MINUTES:
            raise KeyError(f"Unknown assay '{assay}'. Available: {sorted(ASSAY_RUN_MINUTES)}")
        eff_count = count * repeat_mult
        tot_tests += eff_count
        req_min += eff_count * ASSAY_RUN_MINUTES[key]

    util = (req_min / eff_min_day) * 100.0 if eff_min_day > 0 else 999.0
    overloaded = util > 85.0

    # Extra GX-4 (4-module) units needed to bring utilization below 80% safe operating ceiling
    add_gx4 = 0
    if util > 80.0:
        min_per_module = eff_min_day / spec.functional_modules
        needed_modules = math.ceil(req_min / (0.80 * min_per_module))
        add_gx4 = math.ceil(max(0, needed_modules - spec.functional_modules) / 4.0)

    # Approximate in-lab TAT (hours): 2h accessioning/run when <=80% utilized; queues build rapidly above 85%
    if util <= 80.0:
        tat_hr = 3.5
    elif util <= 100.0:
        tat_hr = 3.5 + ((util - 80.0) / 20.0) * 12.0
    else:
        tat_hr = 24.0 + (util - 100.0) * 0.6

    warnings: list[str] = []
    if not spec.ups_backup_installed:
        warnings.append(
            "NO UPS / SOLAR BACKUP: grid dips during a 90-minute GeneXpert run abort in-flight cartridges "
            "(2127/5007 power errors) and waste ~$8-$10 per cartridge."
        )
    if overloaded:
        warnings.append(
            f"HUB CAPACITY BOTTLENECK ({util:.1f}% utilization > 85%): add {add_gx4}x 4-module GeneXpert "
            "instrument(s) or introduce a second lab shift to preserve same-day TB initiation."
        )

    return HubCapacityReport(
        hub_id=spec.hub_id,
        functional_modules=spec.functional_modules,
        effective_module_minutes_day=round(eff_min_day, 1),
        required_module_minutes_day=round(req_min, 1),
        daily_tests_including_repeats=round(tot_tests, 1),
        utilization_pct=round(util, 1),
        overloaded=overloaded,
        additional_gx4_machines_needed=add_gx4,
        estimated_lab_tat_hours=round(tat_hr, 1),
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class CartridgeLot:
    """Batch of GeneXpert cartridges in hub stockroom."""

    lot_id: str
    assay: str
    quantity: int
    expiry_date: date


@dataclass(frozen=True)
class CartridgePlan:
    """FEFO cartridge stock-out vs. expiry-risk quantification."""

    assay: str
    monthly_consumption: int
    usable_stock_on_hand: int
    expired_stock_count: int
    projected_expiry_waste: int
    months_of_usable_stock: float
    reorder_cartridges_needed: int
    status: str  # "healthy", "stockout_risk", "overstock_expiry_risk"


def quantify_cartridges(
    lots: Sequence[CartridgeLot],
    *,
    assay: str,
    monthly_consumption: int,
    as_of: date,
    target_months_stock: float = 3.0,
) -> CartridgePlan:
    """Simulate FEFO cartridge burn-down to detect stockouts AND unconsumable expiry waste."""
    if monthly_consumption <= 0:
        raise ValueError("monthly_consumption must be positive")
    matching = [lot for lot in lots if lot.assay.strip().lower() == assay.strip().lower()]
    expired = sum(lot.quantity for lot in matching if lot.expiry_date < as_of)
    active = sorted([lot for lot in matching if lot.expiry_date >= as_of], key=lambda x: x.expiry_date)

    daily_rate = monthly_consumption / 30.0
    consumed_days = 0.0
    usable_total = 0
    wasted_expiry = 0
    for lot in active:
        days_left = max(0.0, float((lot.expiry_date - as_of).days))
        remaining_window = max(0.0, days_left - consumed_days)
        max_consumable = math.floor(remaining_window * daily_rate)
        used = min(lot.quantity, max_consumable)
        wasted = lot.quantity - used
        usable_total += used
        wasted_expiry += wasted
        consumed_days += used / daily_rate

    mos = usable_total / monthly_consumption
    target_qty = math.ceil(monthly_consumption * target_months_stock)
    reorder = max(0, target_qty - usable_total)

    if mos < 1.5:
        status = "stockout_risk"
    elif wasted_expiry > 0:
        status = "overstock_expiry_risk"
    else:
        status = "healthy"

    return CartridgePlan(
        assay=assay,
        monthly_consumption=monthly_consumption,
        usable_stock_on_hand=usable_total,
        expired_stock_count=expired,
        projected_expiry_waste=wasted_expiry,
        months_of_usable_stock=round(mos, 2),
        reorder_cartridges_needed=reorder,
        status=status,
    )


@dataclass(frozen=True)
class SpokeClinic:
    """Peripheral health facility sending sputum/DBS specimens to the district lab hub."""

    clinic_id: str
    round_trip_km: float
    weekly_pickup_days: int
    cold_box_2_8c_available: bool = True


@dataclass(frozen=True)
class CourierRouteAudit:
    """Hub-and-spoke sample courier turnaround SLA and cold-chain specimen viability audit."""

    spoke_count: int
    total_weekly_courier_km: float
    worst_case_pickup_wait_hours: float
    sla_compliant_48h: bool
    alerts: tuple[str, ...]


def audit_courier_network(
    spokes: Sequence[SpokeClinic],
    *,
    lab_tat_hours: float = 4.0,
    max_sputum_ambient_hours: float = 72.0,
) -> CourierRouteAudit:
    """Audit motorcycle hub-rider sample pickup cadence and WHO 48h TB result turnaround SLA."""
    if not spokes:
        raise ValueError("At least one SpokeClinic is required")
    weekly_km = 0.0
    max_wait_hr = 0.0
    alerts: list[str] = []

    for s in spokes:
        if s.weekly_pickup_days <= 0 or s.round_trip_km < 0:
            raise ValueError(f"Invalid schedule or distance for {s.clinic_id}")
        weekly_km += s.round_trip_km * s.weekly_pickup_days
        gap_hr = (7.0 / s.weekly_pickup_days) * 24.0
        max_wait_hr = max(max_wait_hr, gap_hr)
        if not s.cold_box_2_8c_available and gap_hr > max_sputum_ambient_hours:
            alerts.append(
                f"SPECIMEN VIABILITY RISK at {s.clinic_id}: {gap_hr:.0f}h pickup gap without 2-8C cold box "
                f"exceeds {max_sputum_ambient_hours:.0f}h ambient sputum limit."
            )

    total_tat = max_wait_hr + lab_tat_hours
    sla_ok = total_tat <= 48.0
    if not sla_ok:
        alerts.append(
            f"WHO 48h TB TAT BREACHED: worst-case wait + lab TAT is {total_tat:.1f}h; increase courier pickup "
            "frequency to >=4 days/week on high-volume spokes."
        )

    return CourierRouteAudit(
        spoke_count=len(spokes),
        total_weekly_courier_km=round(weekly_km, 1),
        worst_case_pickup_wait_hours=round(max_wait_hr, 1),
        sla_compliant_48h=sla_ok,
        alerts=tuple(alerts),
    )
