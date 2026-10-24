"""Currency comparisons use fixed fixture rates, never a live service."""
from datetime import date, timedelta
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.quote_analysis import rank_quotes, rank_quotes_in_myr
from src.quote_chat import currency_reference_rows, prepare_model_messages
from src.quote_chat_pdf import _html_document
from src.quote_currency import MyrRateProvider, convert_quote
from src.quotation_export import build_quotation_workbook
from io import BytesIO
from zipfile import ZipFile


RATES = {
    "USD": {"rate": "4.5", "date": "2026-10-23", "source": "Fixture reference rates"},
    "SGD": {"rate": "3.25", "date": "2026-10-23", "source": "Fixture reference rates"},
}


class CurrencyTests(TestCase):
    def quotes(self):
        return [
            {"message_id": "native", "currency": "MYR", "total_price": 500,
             "delivery_days": 2, "delivery_unit": "calendar_days"},
            {"message_id": "usd", "currency": "USD", "total_price": 100,
             "delivery_days": 2, "delivery_unit": "calendar_days"},
            {"message_id": "sgd", "currency": "SGD", "total_price": 150,
             "delivery_days": 2, "delivery_unit": "calendar_days"},
        ]

    def test_price_and_balanced_use_myr_not_original_numbers(self):
        for priority in ("price", "balanced"):
            result = rank_quotes(self.quotes(), priority, RATES)
            self.assertEqual(result["recommendation_message_id"], "usd")
            self.assertEqual([row["message_id"] for row in result["quotes"]],
                             ["usd", "sgd", "native"])
            usd = result["quotes"][0]
            self.assertEqual(usd["total_price"], 100)
            self.assertEqual(usd["currency"], "USD")
            self.assertEqual(usd["myr_total_price"], 450)
            self.assertEqual(usd["exchange_rate_date"], "2026-10-23")
        self.assertNotIn("myr_total_price", self.quotes()[0])

    def test_missing_rates_never_compare_raw_foreign_amounts(self):
        result = rank_quotes(self.quotes(), "price")
        self.assertIsNone(result["recommendation_message_id"])
        self.assertTrue(all(row["rank"] is None for row in result["quotes"]))
        usd = next(row for row in result["quotes"] if row["message_id"] == "usd")
        self.assertIsNone(usd["myr_total_price"])
        self.assertIn("USD", usd["conversion_error"])
        timed = rank_quotes(self.quotes(), "time")
        self.assertIsNotNone(timed["recommendation_message_id"])

    def test_rounding_zero_native_and_ambiguous_currency(self):
        self.assertEqual(convert_quote({"currency": "USD", "total_price": 1.01}, RATES)["myr_total_price"], 4.55)
        self.assertEqual(convert_quote({"currency": "MYR", "total_price": 0}, {})["myr_total_price"], 0)
        for amount in (True, float("inf"), -1):
            self.assertIsNone(convert_quote({"currency": "MYR", "total_price": amount}, {})["myr_total_price"])
        self.assertIsNone(convert_quote({"currency": "", "total_price": 30}, RATES)["myr_total_price"])
        provider = MagicMock(return_value=RATES)
        rank_quotes_in_myr([self.quotes()[0]], "price", provider)
        provider.assert_not_called()
        rank_quotes_in_myr(self.quotes(), "price", provider)
        provider.assert_called_once()

    def test_reference_provider_inverts_myr_base_and_caches(self):
        provider = MyrRateProvider()
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [json.dumps([{
            "base": "MYR", "quote": "USD", "rate": 0.25,
            "date": date.today().isoformat(),
        }]).encode()]
        with patch("src.quote_currency.requests.get", return_value=response) as get:
            self.assertEqual(float(provider.rates()["USD"]["rate"]), 4)
            self.assertEqual(float(provider.rates()["USD"]["rate"]), 4)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(get.call_args.kwargs["params"], {"base": "MYR"})

    def test_stale_future_invalid_rates_fail_closed_and_failure_is_cached(self):
        for days, rate in ((8, 0.25), (-1, 0.25), (0, 0), (0, "NaN")):
            provider = MyrRateProvider()
            response = MagicMock()
            response.__enter__.return_value = response
            response.iter_content.return_value = [json.dumps([{
                "base": "MYR", "quote": "USD", "rate": rate,
                "date": (date.today() - timedelta(days=days)).isoformat(),
            }]).encode()]
            with patch("src.quote_currency.requests.get", return_value=response) as get:
                self.assertEqual(provider.rates(), {})
                self.assertEqual(provider.rates(), {})
                self.assertEqual(get.call_count, 2)

    def test_ecb_feed_covers_invalid_future_dated_broad_feed_independently(self):
        def response_for(url, **kwargs):
            response = MagicMock()
            response.__enter__.return_value = response
            if "v1/latest" in url:
                data = {"base": "MYR", "date": date.today().isoformat(), "rates": {"USD": 0.25}}
            else:
                data = [{"base": "MYR", "quote": "USD", "rate": 0.5,
                         "date": (date.today() + timedelta(days=1)).isoformat()}]
            response.iter_content.return_value = [json.dumps(data).encode()]
            return response
        with patch("src.quote_currency.requests.get", side_effect=response_for):
            rates = MyrRateProvider().rates()
        self.assertEqual(float(rates["USD"]["rate"]), 4)
        self.assertEqual(rates["USD"]["date"], date.today().isoformat())

    def test_excel_chat_context_and_pdf_reference_preserve_both_prices(self):
        quote = rank_quotes(self.quotes(), "price", RATES)["quotes"][0]
        row = dict(quote, comparison_id=1, client_name="<Foreign supplier>", vendor="<Foreign supplier>")
        messages, _, _ = prepare_model_messages([{"role": "user", "content": "Compare prices"}], [row])
        context = " ".join(message["content"] for message in messages)
        self.assertIn('"price_myr":450.0', context)
        self.assertIn("exchange_rate_date", context)
        reference = currency_reference_rows([row])
        self.assertIn("Original: USD 100.00", reference[0]["item_price"])
        self.assertIn("MYR 450.00", reference[0]["item_price"])
        html = _html_document("Answer", [], 1, 1, reference)
        self.assertIn("MYR price reference", html)
        self.assertIn("&lt;Foreign supplier&gt;", html)
        self.assertIn("2026-10-23", html)
        workbook = build_quotation_workbook([row])
        with ZipFile(BytesIO(workbook)) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode()
        for value in ("Price (MYR / RM)", "Exchange rate date", "USD", "450", "100", "2026-10-23"):
            self.assertIn(value, xml)
