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
        options={"export_entity": EXPORT, "vat": 23, "use_corrected": True, "estimate": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    price = hass.states.get("sensor.rcem_pse_cena_rcem_netto") or hass.states.get(
        "sensor.rcem_pse_rcem_price_net"
    )
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
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    stats = await recorder_mock.async_add_executor_job(
        statistics_during_period,
        hass, start, None, {"rcem:earnings_net", "rcem:price_net"}, "month", None, {"change", "mean"},
    )
    months = stats["rcem:earnings_net"]
    assert months[0]["change"] == pytest.approx(sep_kwh * p)
    assert months[1]["change"] == pytest.approx(oct_kwh * 0.300)
    assert stats["rcem:price_net"][1]["mean"] == pytest.approx(0.300)
