"""End-to-end test with the real recorder."""

from datetime import datetime, timedelta
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.rcem.const import DOMAIN, URL

HTML = (Path(__file__).parent / "fixture_pse.html").read_text(encoding="utf-8")
EXPORT = "sensor.grid_export"


async def _import_export_stats(hass: HomeAssistant, start: datetime, hours: int, kwh: float):
    total = 0.0
    rows = []
    for i in range(hours):
        total += kwh
        rows.append({"start": start + timedelta(hours=i), "state": total, "sum": total})
    async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": EXPORT,
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(hass)


@pytest.mark.parametrize("time_zone", ["Europe/Warsaw"])
async def test_earnings(
    recorder_mock: Recorder,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    time_zone: str,
):
    await hass.config.async_set_time_zone(time_zone)
    freezer.move_to("2026-11-01T12:30:00+01:00")
    aioclient_mock.get(URL, text=HTML)

    # 1 kWh every hour from 1 Sep 2026 00:00 local until 1 Nov 12:00 local.
    start = dt_util.as_utc(datetime(2026, 9, 1, tzinfo=dt_util.get_default_time_zone()))
    end = dt_util.as_utc(datetime(2026, 11, 1, 12, tzinfo=dt_util.get_default_time_zone()))
    hours = int((end - start).total_seconds() // 3600)
    await _import_export_stats(hass, start, hours, 1.0)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={
            "export_entity": EXPORT,
            "vat": 23,
            "use_corrected": True,
            "estimate_method": "last_published",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    price = hass.states.get("sensor.rcem_pse_rcem_price_net")
    assert price is not None, sorted(hass.states.async_entity_ids("sensor"))
    assert float(price.state) == pytest.approx(0.34568)
    assert price.attributes["estimated"] is True
    assert price.attributes["source_month"] == "2026-09"

    sep_kwh = 30 * 24
    oct_kwh = 31 * 24 + 1  # DST change on 25 Oct adds an hour
    nov_kwh = 12
    p = 0.34568
    expected_net = (sep_kwh + oct_kwh + nov_kwh) * p  # Oct/Nov estimated with Sep

    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    total = next(s for e, s in states.items() if "total" in e and "net" in e)
    assert float(total.state) == pytest.approx(round(expected_net, 2))
    gross_total = next(s for e, s in states.items() if "total" in e and "gross" in e)
    assert float(gross_total.state) == pytest.approx(round(expected_net * 1.23, 2), abs=0.01)
    assert total.attributes["months"]["2026-09"]["estimated"] is False
    assert total.attributes["months"]["2026-10"]["estimated"] is True

    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass, start, None,
        {"rcem:earnings_net", "rcem:earnings_gross", "rcem:price_net", "rcem:price_gross"},
        "day", None, {"sum", "change", "mean"},
    )
    earn = stats["rcem:earnings_net"]
    assert earn[-1]["sum"] == pytest.approx(expected_net)
    assert earn[0]["change"] == pytest.approx(24 * p)
    assert stats["rcem:earnings_gross"][-1]["sum"] == pytest.approx(expected_net * 1.23)
    assert all(r["mean"] == pytest.approx(p) for r in stats["rcem:price_net"])
    assert stats["rcem:price_gross"][0]["mean"] == pytest.approx(p * 1.23)

    # October gets published: history is rewritten with the real price.
    html_oct = HTML.replace(
        "</tbody>",
        '<tr><td colspan="4"><b>październik</b></td></tr>'
        "<tr><td>RCEm</td><td>300,00</td><td>11.11.2026</td><td>-</td></tr></tbody>",
        1,
    )
    assert html_oct != HTML
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, text=html_oct)
    freezer.tick(timedelta(hours=4))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    # Scheduled coordinator refreshes run as background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    assert hass.states.get("sensor.rcem_pse_rcem_price_net").attributes["source_month"] == "2026-10"
    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass, start, None, {"rcem:earnings_net", "rcem:price_net"}, "month", None, {"change", "mean"},
    )
    months = stats["rcem:earnings_net"]
    assert months[0]["change"] == pytest.approx(sep_kwh * p)
    assert months[1]["change"] == pytest.approx(oct_kwh * 0.300)
    assert stats["rcem:price_net"][1]["mean"] == pytest.approx(0.300)

    # PSE unreachable after a restart: prices come from the on-disk cache.
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, exc=TimeoutError())
    freezer.tick(timedelta(hours=4))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.name == "LOADED"
    assert aioclient_mock.call_count == 1
    state = hass.states.get("sensor.rcem_pse_rcem_price_net")
    assert float(state.state) == pytest.approx(0.300)


def _api_rows(url, field, value_fn):
    """Fake PSE API page: 96 quarter-hours per day in the filtered date range."""
    flt = url.query["$filter"]
    start, end = (datetime.fromisoformat(x).date() for x in flt.split("'")[1::2])
    rows = []
    day = start
    while day <= end:
        for q in range(96):
            h, m = divmod(q * 15, 60)
            h2, m2 = divmod(q * 15 + 15, 60)
            rows.append(
                {
                    "business_date": day.isoformat(),
                    "period": f"{h:02d}:{m:02d} - {h2 % 24:02d}:{m2:02d}",
                    field: value_fn(day, q),
                }
            )
        day += timedelta(days=1)
    return {"value": rows}


@pytest.mark.parametrize("time_zone", ["Europe/Warsaw"])
async def test_rce_estimate(
    recorder_mock: Recorder,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    time_zone: str,
):
    """Unpublished October is estimated from RCE weighted by PV × calibration."""
    await hass.config.async_set_time_zone(time_zone)
    freezer.move_to("2026-11-01T12:30:00+01:00")
    aioclient_mock.get(URL, text=HTML)

    # October RCE is 500 PLN/MWh, earlier months 400; PV only around noon,
    # where a price spike (ignored: no PV) and a negative price (clamped) sit.
    def rce(day, q):
        if q == 2:
            return 9999.0
        if q == 48:
            return -100.0
        return 500.0 if day.month == 10 and day.year == 2026 else 400.0

    def pv(day, q):
        return 1000.0 if 40 <= q <= 56 else 0.0

    async def api(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        if url.path.endswith("rce-pln"):
            body = _api_rows(url, "rce_pln", rce)
        else:
            body = _api_rows(url, "pv", pv)
        return AiohttpClientMockResponse(method, url, json=body)

    aioclient_mock.get("https://api.raporty.pse.pl/api/rce-pln", side_effect=api)
    aioclient_mock.get("https://api.raporty.pse.pl/api/his-wlk-cal", side_effect=api)

    start = dt_util.as_utc(datetime(2026, 10, 1, tzinfo=dt_util.get_default_time_zone()))
    await _import_export_stats(hass, start, 24 * 31 + 1 + 12, 1.0)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={"export_entity": EXPORT, "vat": 23, "use_corrected": True, "estimate": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)

    # PV-weighted RCE: 16 noon periods at 400/500 and one at 0 (clamped).
    weighted = {m: v * 16 / 17 for m, v in (("old", 400.0), ("oct", 500.0))}
    from custom_components.rcem.parser import parse_rcem

    prices = parse_rcem(HTML)
    calib = [k for k in sorted(prices, reverse=True) if k >= "2025-10"][:12]
    factor = sum(prices[k].value() / weighted["old"] for k in calib) / len(calib)
    expected_oct = weighted["oct"] * factor / 1000

    state = hass.states.get("sensor.rcem_pse_rcem_price_net")
    assert state.attributes["estimate_method"] == "rce"
    assert state.attributes["source_month"] == "2026-10"  # November falls back to October
    assert state.attributes["calibration_factor"] == pytest.approx(factor, rel=1e-3)
    assert float(state.state) == pytest.approx(expected_oct, abs=1e-5)

    month = hass.states.get("sensor.rcem_pse_earnings_last_month_net")
    assert month.attributes["estimate_method"] == "rce"
    assert month.attributes["rce_days"] == 31
    assert float(month.state) == pytest.approx(round((24 * 31 + 1) * expected_oct, 2), abs=0.01)

    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass, start, None, {"rcem:earnings_net"}, "month", None, {"change"},
    )
    assert stats["rcem:earnings_net"][0]["change"] == pytest.approx((24 * 31 + 1) * expected_oct)


@pytest.mark.parametrize("time_zone", ["Europe/Warsaw"])
async def test_external_meter_statistic(
    recorder_mock: Recorder,
    enable_custom_integrations: None,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    time_zone: str,
):
    """Export taken from a billing meter's external statistic (e.g. Enea)."""
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    await hass.config.async_set_time_zone(time_zone)
    freezer.move_to("2026-10-05T12:30:00+02:00")
    aioclient_mock.get(URL, text=HTML)
    start = dt_util.as_utc(datetime(2026, 9, 1, tzinfo=dt_util.get_default_time_zone()))
    rows, total = [], 0.0
    for i in range(30 * 24):
        total += 0.5
        rows.append({"start": start + timedelta(hours=i), "state": total, "sum": total})
    async_add_external_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": "Energia oddana",
            "source": "enea",
            "statistic_id": "enea:meter_energia_oddana",
            "unit_class": "energy",
            "unit_of_measurement": "kWh",
        },
        rows,
    )
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={
            "export_entity": "enea:meter_energia_oddana",
            "vat": 23,
            "use_corrected": True,
            "estimate_method": "last_published",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    last = hass.states.get("sensor.rcem_pse_earnings_last_month_gross")
    assert float(last.state) == pytest.approx(round(360 * 0.34568 * 1.23, 2), abs=0.01)
    assert last.attributes["export_entity"] == "enea:meter_energia_oddana"
