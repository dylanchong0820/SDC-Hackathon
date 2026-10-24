"""Dated reference-rate conversion. No quotation contents are sent to the provider."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import threading
import time

import requests


RATE_SOURCE = "Frankfurter reference rates"


class MyrRateProvider:
    """One bounded request per hour; failures are cached briefly to avoid storms."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cached = {}
        self.expires_at = 0

    def rates(self):
        with self.lock:
            if time.monotonic() < self.expires_at:
                return dict(self.cached)
            deadline = time.monotonic() + 8
            rates = {}
            healthy = True
            # The broader feed and the ECB-compatible feed are validated independently.
            # A malformed/future-dated row must never force us to accept an unsafe rate.
            for path in ("v2/rates", "v1/latest"):
                fetched = self._fetch(path, deadline)
                healthy = healthy and bool(fetched)
                rates.update(fetched)
            self.cached = rates
            self.expires_at = time.monotonic() + (3600 if healthy else 60)
            return dict(rates)

    @staticmethod
    def _fetch(path, deadline):
        rates = {}
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {}
            response = requests.get(
                f"https://api.frankfurter.dev/{path}",
                params={"base": "MYR"}, timeout=(min(2, remaining), min(3, remaining)),
                headers={"Accept": "application/json"}, stream=True,
            )
            with response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(8192):
                    body.extend(chunk)
                    if len(body) > 256 * 1024 or time.monotonic() > deadline:
                        raise ValueError("Rate response exceeds its limit.")
                rows = json.loads(body)
            if path == "v1/latest":
                if not isinstance(rows, dict) or rows.get("base") != "MYR":
                    return {}
                rows = [{"base": "MYR", "quote": code, "rate": value, "date": rows.get("date")}
                        for code, value in rows.get("rates", {}).items()]
            if not isinstance(rows, list) or len(rows) > 500:
                raise ValueError("Invalid rates.")
            for row in rows:
                if not isinstance(row, dict) or row.get("base") != "MYR":
                    continue
                code = row.get("quote", "")
                if not isinstance(code, str) or len(code) != 3 or not code.isascii() or not code.isalpha():
                    continue
                day = date.fromisoformat(row["date"])
                if not 0 <= (date.today() - day).days <= 7:
                    continue
                value = Decimal(str(row["rate"]))
                if value.is_finite() and value > 0:
                    rates[code.upper()] = {
                        "rate": str(Decimal(1) / value),
                        "date": day.isoformat(), "source": RATE_SOURCE,
                    }
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, InvalidOperation, ArithmeticError):
            return {}
        return rates


_provider = MyrRateProvider()


def get_myr_rates():
    return _provider.rates()


def convert_quote(quote, rates):
    """Keep original amount/currency untouched and add explicit MYR fields."""
    result = dict(quote)
    result.update(myr_total_price=None, exchange_rate=None, exchange_rate_date="",
                  exchange_rate_source="", conversion_error="")
    currency = str(quote.get("currency") or "").upper()
    amount = quote.get("total_price")
    if amount is None:
        result["conversion_error"] = "No price was stated; MYR conversion is unavailable."
        return result
    try:
        value = Decimal(str(amount))
        if isinstance(amount, bool) or not value.is_finite() or value < 0:
            raise ValueError("Invalid amount.")
        if currency == "MYR":
            rate = Decimal(1)
        else:
            snapshot = rates.get(currency)
            if not snapshot:
                result["conversion_error"] = (
                    f"No current MYR reference rate is available for {currency or 'an unstated currency'}. "
                    "The original price is retained."
                )
                return result
            rate = Decimal(str(snapshot["rate"]))
            if not rate.is_finite() or rate <= 0:
                raise ValueError("Invalid rate.")
            result["exchange_rate_date"] = snapshot["date"]
            result["exchange_rate_source"] = snapshot["source"]
        converted = (value * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        number = float(converted)
        if not converted.is_finite() or abs(number) == float("inf"):
            raise ValueError("Invalid converted amount.")
        result["myr_total_price"] = number
        result["exchange_rate"] = float(rate)
    except (InvalidOperation, ValueError, KeyError, TypeError, OverflowError):
        result["conversion_error"] = "This price could not be safely converted to MYR."
    return result
