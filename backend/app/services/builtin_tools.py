"""Built-in tools backed by free, keyless public services.

Every agent can be granted these from the agent form, like the calculator.
None needs an API key or an account:

* get_weather   - Open-Meteo (open-meteo.com), open-source weather API.
* search_arxiv  - the arXiv API (export.arxiv.org).
* read_pdf      - downloads a PDF and extracts its text with pypdf.
* web_search    - a SearXNG instance when WEB_SEARCH_SEARXNG_URL is set;
                  otherwise DuckDuckGo instant answers plus Wikipedia search.
* travel_info   - country facts from the bundled mledoze/countries dataset
                  (ODbL, app/data/countries.json) and exchange rates from
                  Frankfurter (frankfurter.dev, European Central Bank data).

The services are fixed hosts, so a model cannot steer these tools anywhere
else - with one exception, read_pdf, whose URL comes from the model. It goes
through the same SSRF check as user-defined HTTP tools, on every redirect hop.

Each tool runs under the runtime's tool timeout (TOOL_TIMEOUT_SECONDS), and
each outbound request has its own shorter one, so a slow service produces a
readable error rather than an abandoned call.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config.settings import get_settings
from app.services.tool_base import BaseTool, ToolError

# Wikipedia and others refuse requests without a descriptive User-Agent.
_USER_AGENT = "AgentPlatform/1.0 (+https://github.com/nandini-ro/agent-platform)"
_REQUEST_TIMEOUT = 8.0

# Tests replace this with an httpx.MockTransport; None means the real network.
_transport: httpx.AsyncBaseTransport | None = None


def _client(**kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=_REQUEST_TIMEOUT,
        # A redirect from a fixed host is unexpected; from read_pdf's URL it
        # would escape the SSRF check. Both are handled explicitly instead.
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT},
        transport=_transport,
        **kwargs,
    )


async def _get_json(service: str, url: str, params: dict[str, Any] | None = None) -> Any:
    async with _client() as client:
        response = await _send(client, service, url, params)
    try:
        return response.json()
    except ValueError:
        raise ToolError(f"{service} returned a response that is not JSON.") from None


async def _send(
    client: httpx.AsyncClient, service: str, url: str, params: dict[str, Any] | None
) -> httpx.Response:
    try:
        response = await client.get(url, params=params)
    except httpx.TimeoutException:
        raise ToolError(f"{service} did not answer within {_REQUEST_TIMEOUT:.0f}s.") from None
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach {service}: {exc}") from None
    if response.status_code >= 400:
        raise ToolError(f"{service} returned HTTP {response.status_code}.")
    return response


def _clean(text: str) -> str:
    """Strip HTML tags and entities, and collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

# WMO weather interpretation codes, as Open-Meteo reports them.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


class GetWeatherTool(BaseTool):
    name = "get_weather"
    description = (
        "Get the current weather and a daily forecast (up to 7 days) for a "
        "place, by name. Use it for any question about weather, temperature or "
        "rain somewhere. Source: Open-Meteo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or place name, e.g. 'Hyderabad' or 'Paris, France'",
                "minLength": 1,
                "maxLength": 100,
            },
            "days": {
                "type": "integer",
                "description": "Forecast days, 1-7. Default 3.",
                "minimum": 1,
                "maximum": 7,
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        location = arguments["location"].strip()
        days = arguments.get("days", 3)

        # The geocoder matches a bare name, so "Paris, France" is searched as
        # "Paris" and the country used to pick between the candidates.
        name, _, qualifier = location.partition(",")
        places = (
            await _get_json(
                "Open-Meteo geocoding",
                "https://geocoding-api.open-meteo.com/v1/search",
                {"name": name.strip(), "count": 10, "format": "json"},
            )
        ).get("results") or []
        qualifier = qualifier.strip().lower()
        if qualifier:
            places = [
                p for p in places
                if qualifier in {str(p.get(k, "")).lower() for k in ("country", "country_code", "admin1")}
            ] or places
        if not places:
            raise ToolError(f"No place called {location!r} was found.")
        place = places[0]

        forecast = await _get_json(
            "Open-Meteo",
            "https://api.open-meteo.com/v1/forecast",
            {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "wind_speed_10m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "forecast_days": days,
                "timezone": "auto",
            },
        )

        where = ", ".join(
            str(part) for part in (place.get("name"), place.get("admin1"), place.get("country")) if part
        )
        now = forecast.get("current") or {}
        lines = [
            f"{where} ({place['latitude']:.2f}, {place['longitude']:.2f}), "
            f"local time zone {forecast.get('timezone', 'unknown')}",
            (
                f"Now: {now.get('temperature_2m')}°C (feels like "
                f"{now.get('apparent_temperature')}°C), "
                f"{_WMO.get(now.get('weather_code'), 'unknown conditions')}, "
                f"humidity {now.get('relative_humidity_2m')}%, "
                f"wind {now.get('wind_speed_10m')} km/h"
            ),
            "Forecast:",
        ]
        daily = forecast.get("daily") or {}
        for i, day in enumerate(daily.get("time", [])):
            rain = (daily.get("precipitation_probability_max") or [None] * (i + 1))[i]
            lines.append(
                f"- {day}: {_WMO.get(daily['weather_code'][i], 'unknown')}, "
                f"{daily['temperature_2m_min'][i]}-{daily['temperature_2m_max'][i]}°C"
                + (f", {rain}% chance of precipitation" if rain is not None else "")
            )
        lines.append("Source: Open-Meteo (open-meteo.com)")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

_ATOM = {"a": "http://www.w3.org/2005/Atom"}

# arXiv matches nothing on these, so requiring one empties every result.
_ARXIV_STOPWORDS = frozenset(
    "a an and are as at be by for from how in is it of on or the to was what "
    "when which with you your all can do does using via".split()
)


def arxiv_query(words: list[str]) -> str:
    """The exact phrase, or every meaningful word.

    The phrase finds a paper named by its title ("attention is all you need");
    the AND of keywords finds papers on a topic described loosely. Only word
    characters reach here, so a query cannot inject arXiv's own operators.
    """
    phrase = " ".join(words)
    keywords = [w for w in words if w.lower() not in _ARXIV_STOPWORDS and len(w) > 1]
    if len(words) == 1 or not keywords:
        return f'all:"{phrase}"'
    return f'all:"{phrase}" OR (' + " AND ".join(f"all:{w}" for w in keywords) + ")"


class SearchArxivTool(BaseTool):
    name = "search_arxiv"
    description = (
        "Search arXiv for research papers. Returns each paper's title, "
        "authors, date, abstract and PDF link. Use it to find papers on a "
        "topic; pass a PDF link to read_pdf to read a paper in full."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords, e.g. 'transformer attention'",
                "minLength": 1,
                "maxLength": 200,
            },
            "max_results": {
                "type": "integer",
                "description": "How many papers, 1-10. Default 5.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        words = re.findall(r"[\w\-]+", arguments["query"])[:12]
        if not words:
            raise ToolError("The query has no searchable words.")
        async with _client() as client:
            response = await _send(
                client,
                "arXiv",
                "https://export.arxiv.org/api/query",
                {
                    "search_query": arxiv_query(words),
                    "max_results": arguments.get("max_results", 5),
                    "sortBy": "relevance",
                },
            )
        try:
            feed = ET.fromstring(response.content)
        except ET.ParseError:
            raise ToolError("arXiv returned a response that could not be read.") from None

        papers = []
        for i, entry in enumerate(feed.findall("a:entry", _ATOM), start=1):
            authors = [_clean(a.findtext("a:name", "", _ATOM)) for a in entry.findall("a:author", _ATOM)]
            pdf = next(
                (l.get("href") for l in entry.findall("a:link", _ATOM) if l.get("title") == "pdf"),
                None,
            )
            papers.append(
                "\n".join(
                    [
                        f"{i}. {_clean(entry.findtext('a:title', '', _ATOM))}",
                        "   Authors: "
                        + ", ".join(authors[:4])
                        + (" et al." if len(authors) > 4 else ""),
                        f"   Published: {entry.findtext('a:published', '', _ATOM)[:10]}",
                        f"   Abstract page: {entry.findtext('a:id', '', _ATOM)}",
                        *([f"   PDF: {pdf}"] if pdf else []),
                        f"   Abstract: {_shorten(_clean(entry.findtext('a:summary', '', _ATOM)), 500)}",
                    ]
                )
            )
        if not papers:
            return f"No arXiv papers matched {arguments['query']!r}."
        return "\n\n".join(papers)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_MAX_PDF_BYTES = 15 * 1024 * 1024
_MAX_PDF_CHARS = 12_000
_MAX_REDIRECTS = 3


class ReadPdfTool(BaseTool):
    name = "read_pdf"
    description = (
        "Download a PDF from a public URL and return its text, page by page. "
        "Use it to read papers, reports or manuals the user links to. Scanned "
        "PDFs without a text layer return no text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public http(s) URL of the PDF",
                "minLength": 1,
                "maxLength": 2000,
            },
            "max_pages": {
                "type": "integer",
                "description": "Read at most this many pages from the start, 1-30. Default 10.",
                "minimum": 1,
                "maximum": 30,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        data = await self._download(arguments["url"].strip())
        # pypdf is pure Python and CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(_pdf_text, data, arguments.get("max_pages", 10))

    async def _download(self, url: str) -> bytes:
        # Imported here: http_tool imports the tool registry, which imports
        # this module.
        from app.services.http_tool import HTTPToolError, assert_safe_url

        async with _client() as client:
            for _ in range(_MAX_REDIRECTS + 1):
                try:
                    assert_safe_url(url)
                except HTTPToolError as exc:
                    raise ToolError(str(exc)) from None
                try:
                    async with client.stream("GET", url) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get("location", ""))
                            continue
                        if response.status_code >= 400:
                            raise ToolError(f"The PDF URL returned HTTP {response.status_code}.")
                        declared = int(response.headers.get("content-length") or 0)
                        if declared > _MAX_PDF_BYTES:
                            raise ToolError(_too_large())
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > _MAX_PDF_BYTES:
                                raise ToolError(_too_large())
                            chunks.append(chunk)
                        return b"".join(chunks)
                except httpx.TimeoutException:
                    raise ToolError(
                        f"The PDF did not download within {_REQUEST_TIMEOUT:.0f}s."
                    ) from None
                except httpx.HTTPError as exc:
                    raise ToolError(f"Could not download the PDF: {exc}") from None
        raise ToolError(f"The PDF URL redirected more than {_MAX_REDIRECTS} times.")


def _too_large() -> str:
    return f"The PDF is larger than {_MAX_PDF_BYTES // (1024 * 1024)} MB."


def _pdf_text(data: bytes, max_pages: int) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise ToolError("The URL did not return a PDF.")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolError("The PDF is password-protected.")
        total = len(reader.pages)
        parts = []
        for number in range(min(max_pages, total)):
            text = (reader.pages[number].extract_text() or "").strip()
            if text:
                parts.append(f"[page {number + 1}]\n{text}")
    except PdfReadError as exc:
        raise ToolError(f"The PDF could not be read: {exc}") from None

    shown = min(max_pages, total)
    header = f"PDF with {total} page(s); text of page(s) 1-{shown}:"
    if not parts:
        return f"{header}\n(no extractable text - the pages may be scanned images)"
    body = "\n\n".join(parts)
    if len(body) > _MAX_PDF_CHARS:
        body = body[:_MAX_PDF_CHARS] + "\n...[truncated]"
    return f"{header}\n\n{body}"


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search for information on a topic. Returns an instant answer when "
        "there is one, and a list of results with titles, links and snippets. "
        "Use it for facts, definitions and finding sources."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for",
                "minLength": 1,
                "maxLength": 200,
            },
            "max_results": {
                "type": "integer",
                "description": "How many results, 1-10. Default 5.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        query = arguments["query"].strip()
        limit = arguments.get("max_results", 5)
        searxng = get_settings().web_search_searxng_url.rstrip("/")
        if searxng:
            return await self._searxng(searxng, query, limit)
        return await self._keyless(query, limit)

    async def _searxng(self, base: str, query: str, limit: int) -> str:
        # Configured by the administrator, so it may be on a private network.
        data = await _get_json("SearXNG", f"{base}/search", {"q": query, "format": "json"})
        lines = []
        if data.get("answers"):
            lines.append(f"Answer: {_clean(str(data['answers'][0]))}")
        for i, r in enumerate((data.get("results") or [])[:limit], start=1):
            lines.append(f"{i}. {_clean(r.get('title', ''))} - {r.get('url', '')}")
            if r.get("content"):
                lines.append(f"   {_shorten(_clean(r['content']), 300)}")
        return "\n".join(lines) or f"No results for {query!r}."

    async def _keyless(self, query: str, limit: int) -> str:
        instant, wiki = await asyncio.gather(
            _get_json(
                "DuckDuckGo",
                "https://api.duckduckgo.com/",
                {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            ),
            _get_json(
                "Wikipedia",
                "https://en.wikipedia.org/w/api.php",
                {"action": "query", "list": "search", "srsearch": query,
                 "srlimit": limit, "format": "json"},
            ),
            return_exceptions=True,
        )
        if isinstance(instant, Exception) and isinstance(wiki, Exception):
            raise ToolError(f"Search failed: {instant}; {wiki}")

        lines = []
        if isinstance(instant, dict):
            answer = instant.get("Answer") or instant.get("AbstractText") or ""
            if answer:
                source = instant.get("AbstractURL") or instant.get("AbstractSource") or "DuckDuckGo"
                lines.append(f"Instant answer: {_shorten(_clean(str(answer)), 600)} ({source})")
        if isinstance(wiki, dict):
            hits = (wiki.get("query") or {}).get("search") or []
            if hits:
                lines.append("Results:")
            for i, hit in enumerate(hits[:limit], start=1):
                title = hit.get("title", "")
                lines.append(f"{i}. {title} - https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")
                if hit.get("snippet"):
                    lines.append(f"   {_clean(hit['snippet'])}")
        if not lines:
            return f"No results for {query!r}."
        lines.append("Sources: DuckDuckGo instant answers, Wikipedia search")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------

_COUNTRIES_FILE = Path(__file__).resolve().parent.parent / "data" / "countries.json"


@lru_cache
def _countries() -> list[dict[str, Any]]:
    return json.loads(_COUNTRIES_FILE.read_text(encoding="utf-8"))


def find_countries(query: str) -> list[dict[str, Any]]:
    """Exact matches on any name or code first; otherwise names containing it."""
    q = query.strip().lower()
    exact = [
        c for c in _countries()
        if q in {c["name"].lower(), c["official_name"].lower(), c["cca2"].lower(),
                 c["cca3"].lower(), *(a.lower() for a in c["aliases"])}
    ]
    if exact:
        return exact
    return [c for c in _countries() if q in c["name"].lower() or q in c["official_name"].lower()]


class TravelInfoTool(BaseTool):
    name = "travel_info"
    description = (
        "Get travel facts about a country: capital, region, languages, "
        "currency, calling code and neighbours. Give home_currency (e.g. INR) "
        "to also get today's exchange rate into the local currency."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "country": {
                "type": "string",
                "description": "Country name or code, e.g. 'Japan', 'UK', 'DE'",
                "minLength": 1,
                "maxLength": 100,
            },
            "home_currency": {
                "type": "string",
                "description": "The traveller's currency, 3-letter code, e.g. INR",
                "pattern": "^[A-Za-z]{3}$",
            },
        },
        "required": ["country"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> str:
        matches = find_countries(arguments["country"])
        if not matches:
            raise ToolError(f"No country matches {arguments['country']!r}.")
        if len(matches) > 1:
            names = ", ".join(c["name"] for c in matches[:10])
            return f"Several countries match {arguments['country']!r}: {names}. Ask which one."
        c = matches[0]
        names = {x["cca3"]: x["name"] for x in _countries()}
        lines = [
            f"{c['flag']} {c['name']} ({c['official_name']})".strip(),
            f"Capital: {', '.join(c['capital']) or 'none'}",
            f"Region: {c['region']}" + (f" / {c['subregion']}" if c["subregion"] else ""),
            f"Languages: {', '.join(c['languages']) or 'unknown'}",
            "Currency: "
            + (", ".join(f"{name} ({code})" for code, name in c["currencies"].items()) or "none"),
            f"Calling code: {', '.join(c['calling_codes']) or 'unknown'}",
            "Borders: "
            + (", ".join(names.get(b, b) for b in c["borders"]) or "none (island or isolated)"),
        ]
        home = arguments.get("home_currency", "").upper()
        if home:
            lines.extend(await _rates(home, list(c["currencies"])))
        lines.append("Sources: mledoze/countries (ODbL); exchange rates from Frankfurter (ECB)")
        return "\n".join(lines)


async def _rates(home: str, local: list[str]) -> list[str]:
    targets = [code for code in local if code != home]
    if not targets:
        return [f"Exchange rate: the local currency is {home}, your home currency."]
    try:
        data = await _get_json(
            "Frankfurter",
            "https://api.frankfurter.dev/v1/latest",
            {"from": home, "to": ",".join(targets)},
        )
    except ToolError as exc:
        # The facts are still worth returning; Frankfurter covers about 30
        # currencies and answers 404 for the rest.
        return [f"Exchange rate: unavailable for {home} to {', '.join(targets)} ({exc})"]
    rates = data.get("rates") or {}
    return [
        f"Exchange rate: 1 {home} = {rate} {code} (ECB reference rate, {data.get('date')})"
        for code, rate in rates.items()
    ] or [f"Exchange rate: unavailable for {home} to {', '.join(targets)}"]


BUILTIN_TOOLS: list[BaseTool] = [
    GetWeatherTool(),
    SearchArxivTool(),
    ReadPdfTool(),
    WebSearchTool(),
    TravelInfoTool(),
]
