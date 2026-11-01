"""Constants for the RCEm integration."""

from datetime import timedelta

DOMAIN = "rcem"
NAME = "RCEm (PSE)"

URL = "https://www.pse.pl/oire/rcem-rynkowa-miesieczna-cena-energii-elektrycznej"

CONF_EXPORT_ENTITY = "export_entity"
CONF_VAT = "vat"
CONF_USE_CORRECTED = "use_corrected"
CONF_ESTIMATE = "estimate"  # legacy bool, replaced by CONF_ESTIMATE_METHOD
CONF_ESTIMATE_METHOD = "estimate_method"

DEFAULT_VAT = 23.0
DEFAULT_USE_CORRECTED = True
DEFAULT_ESTIMATE = True

ESTIMATE_RCE = "rce"
ESTIMATE_LAST_PUBLISHED = "last_published"
ESTIMATE_NONE = "none"
ESTIMATE_METHODS = [ESTIMATE_RCE, ESTIMATE_LAST_PUBLISHED, ESTIMATE_NONE]
DEFAULT_ESTIMATE_METHOD = ESTIMATE_RCE

UPDATE_INTERVAL = timedelta(hours=1)
FETCH_INTERVAL = timedelta(hours=3)
# pse.pl is often slow to accept connections.
FETCH_TIMEOUT = 60

# RCE + PV data used to estimate unpublished months.
API_URL = "https://api.raporty.pse.pl/api"
FORECAST_INTERVAL = timedelta(hours=6)
CALIBRATION_MONTHS = 12
# A day has 96 quarter-hours (92 on the spring DST change).
MIN_PERIODS_PER_DAY = 92

CURRENCY = "PLN"
PRICE_UNIT = "PLN/kWh"

STAT_PRICE_NET = f"{DOMAIN}:price_net"
STAT_PRICE_GROSS = f"{DOMAIN}:price_gross"
STAT_EARNINGS_NET = f"{DOMAIN}:earnings_net"
STAT_EARNINGS_GROSS = f"{DOMAIN}:earnings_gross"
ALL_STATS = [STAT_PRICE_NET, STAT_PRICE_GROSS, STAT_EARNINGS_NET, STAT_EARNINGS_GROSS]

# Without an export sensor, price statistics go back this many months.
PRICE_ONLY_MONTHS_BACK = 12
