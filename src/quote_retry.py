"""Keep retry provenance independent of extraction and currency ranking."""


def ranked_message_ids(result):
    return [
        quote["message_id"] for quote in sorted(
            result.get("quotes", []), key=lambda quote: quote.get("rank") or 999)
        if not quote.get("extraction_error") and quote.get("rank") is not None
    ]


def with_retry_metadata(result, retry):
    """Recompute change notices against the original, fixed retry baseline."""
    if retry:
        previous = retry["previous_ranking"]
        result["retry"] = {
            **retry,
            "ranking_changed": (
                ranked_message_ids(result) != previous if previous is not None else None),
            "recommendation_changed": (
                result.get("recommendation_message_id") != retry["previous_recommendation"]
                if previous is not None else None),
        }
    return result
