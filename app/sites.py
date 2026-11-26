"""站点配置：亚马逊各站点域名、语言、货币与浏览器时区。"""

SITES = {
    "US": {"domain": "https://www.amazon.com", "locale": "en-US", "currency": "USD", "tz": "America/New_York"},
    "CA": {"domain": "https://www.amazon.ca", "locale": "en-CA", "currency": "CAD", "tz": "America/Toronto"},
    "MX": {"domain": "https://www.amazon.com.mx", "locale": "es-MX", "currency": "MXN", "tz": "America/Mexico_City"},
    "BR": {"domain": "https://www.amazon.com.br", "locale": "pt-BR", "currency": "BRL", "tz": "America/Sao_Paulo"},
    "UK": {"domain": "https://www.amazon.co.uk", "locale": "en-GB", "currency": "GBP", "tz": "Europe/London"},
    "DE": {"domain": "https://www.amazon.de", "locale": "de-DE", "currency": "EUR", "tz": "Europe/Berlin"},
    "FR": {"domain": "https://www.amazon.fr", "locale": "fr-FR", "currency": "EUR", "tz": "Europe/Paris"},
    "IT": {"domain": "https://www.amazon.it", "locale": "it-IT", "currency": "EUR", "tz": "Europe/Rome"},
    "ES": {"domain": "https://www.amazon.es", "locale": "es-ES", "currency": "EUR", "tz": "Europe/Madrid"},
    "NL": {"domain": "https://www.amazon.nl", "locale": "nl-NL", "currency": "EUR", "tz": "Europe/Amsterdam"},
    "SE": {"domain": "https://www.amazon.se", "locale": "sv-SE", "currency": "SEK", "tz": "Europe/Stockholm"},
    "PL": {"domain": "https://www.amazon.pl", "locale": "pl-PL", "currency": "PLN", "tz": "Europe/Warsaw"},
    "BE": {"domain": "https://www.amazon.com.be", "locale": "nl-BE", "currency": "EUR", "tz": "Europe/Brussels"},
    "TR": {"domain": "https://www.amazon.com.tr", "locale": "tr-TR", "currency": "TRY", "tz": "Europe/Istanbul"},
    "JP": {"domain": "https://www.amazon.co.jp", "locale": "ja-JP", "currency": "JPY", "tz": "Asia/Tokyo"},
    "AU": {"domain": "https://www.amazon.com.au", "locale": "en-AU", "currency": "AUD", "tz": "Australia/Sydney"},
    "IN": {"domain": "https://www.amazon.in", "locale": "en-IN", "currency": "INR", "tz": "Asia/Kolkata"},
    "SG": {"domain": "https://www.amazon.sg", "locale": "en-SG", "currency": "SGD", "tz": "Asia/Singapore"},
    "AE": {"domain": "https://www.amazon.ae", "locale": "en-AE", "currency": "AED", "tz": "Asia/Dubai"},
    "SA": {"domain": "https://www.amazon.sa", "locale": "ar-SA", "currency": "SAR", "tz": "Asia/Riyadh"},
}

DEFAULT_SITE = "US"


def get_site(code: str) -> dict:
    code = (code or DEFAULT_SITE).upper()
    if code not in SITES:
        raise ValueError(f"不支持的站点: {code}")
    return {**SITES[code], "code": code}
