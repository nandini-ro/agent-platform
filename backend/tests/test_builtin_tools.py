"""Built-in public-service tools: weather, arXiv, PDF, web search, travel.

No test touches the network. Requests go to an in-process transport that
answers per host, so the suite checks what each tool asks for and how it reads
the answer.
"""

import socket

import httpx
import pytest

from app.services import builtin_tools, http_tool
from app.services.builtin_tools import (
    GetWeatherTool,
    ReadPdfTool,
    SearchArxivTool,
    TravelInfoTool,
    WebSearchTool,
    arxiv_query,
    find_countries,
)
from app.services.tool_registry import ToolError, registry


@pytest.fixture
def network(monkeypatch):
    """Route tool requests to handlers keyed by host. Yields (routes, seen)."""
    routes: dict[str, object] = {}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        route = routes.get(request.url.host)
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    monkeypatch.setattr(builtin_tools, "_transport", httpx.MockTransport(handler))
    return routes, seen


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every hostname to a public address; IP literals stay as given."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        if host.replace(".", "").isdigit():
            return real(host, port, *args, **kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(http_tool.socket, "getaddrinfo", fake)


def test_all_five_are_registered_and_selectable():
    for name in ("get_weather", "search_arxiv", "read_pdf", "web_search", "travel_info"):
        tool = registry.get(name)
        assert tool is not None, name
        assert tool.input_schema["additionalProperties"] is False


# -- weather ----------------------------------------------------------------


def _place(name, country, admin1, lat, lon, code):
    return {"name": name, "country": country, "country_code": code,
            "admin1": admin1, "latitude": lat, "longitude": lon}


@pytest.mark.asyncio
async def test_weather_geocodes_then_forecasts(network):
    routes, seen = network
    routes["geocoding-api.open-meteo.com"] = lambda r: httpx.Response(200, json={"results": [
        _place("Paris", "United States", "Texas", 33.66, -95.55, "US"),
        _place("Paris", "France", "Île-de-France", 48.85, 2.35, "FR"),
    ]})
    routes["api.open-meteo.com"] = httpx.Response(200, json={
        "timezone": "Europe/Paris",
        "current": {"temperature_2m": 16.7, "apparent_temperature": 15.8,
                    "relative_humidity_2m": 78, "wind_speed_10m": 12.0, "weather_code": 3},
        "daily": {"time": ["2026-10-27"], "weather_code": [80],
                  "temperature_2m_min": [10.8], "temperature_2m_max": [17.1],
                  "precipitation_probability_max": [73]},
    })

    out = await GetWeatherTool().execute({"location": "Paris, France", "days": 1})

    assert "Paris, Île-de-France, France" in out  # the qualifier picked France
    assert "16.7°C" in out and "overcast" in out
    assert "2026-10-27: light rain showers, 10.8-17.1°C, 73% chance" in out
    forecast = seen[-1]
    assert forecast.url.params["latitude"] == "48.85"
    assert forecast.url.params["forecast_days"] == "1"


@pytest.mark.asyncio
async def test_weather_unknown_place_is_a_tool_error(network):
    routes, _ = network
    routes["geocoding-api.open-meteo.com"] = httpx.Response(200, json={})
    with pytest.raises(ToolError, match="No place called"):
        await GetWeatherTool().execute({"location": "Nowhereville"})


# -- arXiv ------------------------------------------------------------------


def test_arxiv_query_tries_the_phrase_then_the_keywords():
    assert arxiv_query(["attention", "is", "all", "you", "need"]) == (
        'all:"attention is all you need" OR (all:attention AND all:need)'
    )
    assert arxiv_query(["crispr"]) == 'all:"crispr"'


@pytest.mark.asyncio
async def test_arxiv_query_cannot_carry_operators(network):
    """Quotes, parentheses and colons are stripped before the query is built,
    and stopwords and single letters leave the keyword half."""
    routes, seen = network
    routes["export.arxiv.org"] = httpx.Response(
        200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    await SearchArxivTool().execute({"query": 'x") OR ti:(y'})
    assert seen[0].url.params["search_query"] == (
        'all:"x OR ti y" OR (all:ti)'
    )


_FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence transduction models...  </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <author><name>Niki Parmar</name></author>
    <author><name>Jakob Uszkoreit</name></author>
    <author><name>Llion Jones</name></author>
    <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
    <link title="pdf" href="https://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
  </entry>
</feed>"""


@pytest.mark.asyncio
async def test_arxiv_lists_papers_with_pdf_links(network):
    routes, seen = network
    routes["export.arxiv.org"] = httpx.Response(200, content=_FEED)

    out = await SearchArxivTool().execute({"query": "attention is all you need", "max_results": 1})

    assert "1. Attention Is All You Need" in out
    assert "Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit et al." in out
    assert "Published: 2017-06-12" in out
    assert "PDF: https://arxiv.org/pdf/1706.03762v7" in out
    assert seen[0].url.params["max_results"] == "1"


@pytest.mark.asyncio
async def test_arxiv_with_no_matches_says_so(network):
    routes, _ = network
    routes["export.arxiv.org"] = httpx.Response(
        200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    )
    out = await SearchArxivTool().execute({"query": "zzzz"})
    assert out.startswith("No arXiv papers matched")


# -- PDF --------------------------------------------------------------------


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF whose page shows `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref
    )
    return bytes(out)


@pytest.mark.asyncio
async def test_read_pdf_returns_page_text(network, public_dns):
    routes, _ = network
    routes["papers.example.com"] = httpx.Response(
        200, content=_pdf("Hello from page one"), headers={"content-type": "application/pdf"}
    )
    out = await ReadPdfTool().execute({"url": "https://papers.example.com/a.pdf"})
    assert out.startswith("PDF with 1 page(s)")
    assert "Hello from page one" in out


@pytest.mark.asyncio
async def test_read_pdf_refuses_private_addresses(network):
    with pytest.raises(ToolError, match="non-public address"):
        await ReadPdfTool().execute({"url": "http://127.0.0.1:8000/secret.pdf"})


@pytest.mark.asyncio
async def test_read_pdf_checks_every_redirect_hop(network, public_dns):
    """A public URL that redirects inward must not be followed."""
    routes, seen = network
    routes["papers.example.com"] = httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data"}
    )
    with pytest.raises(ToolError, match="non-public address"):
        await ReadPdfTool().execute({"url": "https://papers.example.com/a.pdf"})
    assert [r.url.host for r in seen] == ["papers.example.com"]


@pytest.mark.asyncio
async def test_read_pdf_rejects_non_pdf_and_oversized(network, public_dns):
    routes, _ = network
    routes["papers.example.com"] = httpx.Response(200, content=b"<html>not a pdf</html>")
    with pytest.raises(ToolError, match="did not return a PDF"):
        await ReadPdfTool().execute({"url": "https://papers.example.com/a.pdf"})

    routes["papers.example.com"] = httpx.Response(
        200, content=b"%PDF", headers={"content-length": str(200 * 1024 * 1024)}
    )
    with pytest.raises(ToolError, match="larger than"):
        await ReadPdfTool().execute({"url": "https://papers.example.com/a.pdf"})


# -- web search -------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_search_combines_instant_answer_and_results(network):
    routes, _ = network
    routes["api.duckduckgo.com"] = httpx.Response(200, json={
        "AbstractText": "Photosynthesis converts light energy.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Photosynthesis",
    })
    routes["en.wikipedia.org"] = httpx.Response(200, json={"query": {"search": [
        {"title": "Photosynthesis", "snippet": '<span class="searchmatch">Photosynthesis</span> is &amp; more'},
        {"title": "C4 carbon fixation", "snippet": ""},
    ]}})

    out = await WebSearchTool().execute({"query": "photosynthesis"})

    assert "Instant answer: Photosynthesis converts light energy." in out
    assert "1. Photosynthesis - https://en.wikipedia.org/wiki/Photosynthesis" in out
    assert "   Photosynthesis is & more" in out
    assert "2. C4 carbon fixation - https://en.wikipedia.org/wiki/C4_carbon_fixation" in out


@pytest.mark.asyncio
async def test_web_search_fails_only_when_every_source_fails(network):
    with pytest.raises(ToolError, match="Search failed"):
        await WebSearchTool().execute({"query": "anything"})


@pytest.mark.asyncio
async def test_web_search_uses_searxng_when_configured(network, monkeypatch):
    routes, seen = network
    monkeypatch.setattr(
        builtin_tools.get_settings(), "web_search_searxng_url", "https://search.example.org/"
    )
    routes["search.example.org"] = httpx.Response(200, json={"results": [
        {"title": "Result A", "url": "https://a.example", "content": "About A"},
    ]})

    out = await WebSearchTool().execute({"query": "a", "max_results": 1})

    assert out == "1. Result A - https://a.example\n   About A"
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["format"] == "json"


# -- travel -----------------------------------------------------------------


@pytest.mark.parametrize("query", ["Japan", "japan", "JP", "JPN", "Nippon"])
def test_countries_are_found_by_name_code_or_alias(query):
    assert [c["name"] for c in find_countries(query)] == ["Japan"]


@pytest.mark.asyncio
async def test_travel_info_with_exchange_rate(network):
    routes, seen = network
    routes["api.frankfurter.dev"] = httpx.Response(
        200, json={"base": "INR", "date": "2026-10-26", "rates": {"JPY": 1.6541}}
    )
    out = await TravelInfoTool().execute({"country": "Japan", "home_currency": "inr"})

    assert "Capital: Tokyo" in out
    assert "Currency: Japanese yen (JPY)" in out
    assert "Calling code: +81" in out
    assert "1 INR = 1.6541 JPY" in out
    assert seen[0].url.params["from"] == "INR"


@pytest.mark.asyncio
async def test_travel_info_still_answers_when_rates_are_down(network):
    out = await TravelInfoTool().execute({"country": "UK", "home_currency": "USD"})
    assert "Capital: London" in out
    assert "Exchange rate: unavailable for USD to GBP" in out


@pytest.mark.asyncio
async def test_travel_info_asks_when_ambiguous():
    out = await TravelInfoTool().execute({"country": "korea"})
    assert out.startswith("Several countries match")
    assert "North Korea" in out and "South Korea" in out


@pytest.mark.asyncio
async def test_travel_info_unknown_country_is_a_tool_error():
    with pytest.raises(ToolError, match="No country matches"):
        await TravelInfoTool().execute({"country": "Atlantis"})
