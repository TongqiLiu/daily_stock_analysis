# -*- coding: utf-8 -*-
"""
Search tools — wraps SearchService methods as agent-callable tools.

Tools:
- search_stock_news: search latest stock news
- search_comprehensive_intel: multi-dimensional intelligence search
- search_research_reports: search broker research / rating updates
"""

import html
import json
import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.agent.news_evidence import record_news_evidence
from src.agent.tools.registry import ToolParameter, ToolDefinition, ToolPolicy

logger = logging.getLogger(__name__)

FUTU_NEWS_SEARCH_URL = "https://ai-news-search.futunn.com/news_search"
FUTU_USER_AGENT = "daily-stock-analysis-agent/1.0"

_NEWS_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["news:read"],
    scope_dimensions=["stock"],
)
_INTEL_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_write_cache"],
    permissions=["intel:read"],
    scope_dimensions=["stock"],
)
_RESEARCH_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read"],
    permissions=["research:read"],
    scope_dimensions=["stock"],
)


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db
    return get_db()


def _get_search_service():
    """Return shared SearchService singleton."""
    from src.search_service import get_search_service
    return get_search_service()


def _canonical_search_code(stock_code: str) -> str:
    from data_provider.base import canonical_stock_code, normalize_stock_code
    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    raw = str(stock_code or "").strip()
    target = parse_analysis_target(raw)
    if target.asset_type == ParseStatus.INDEX and target.canonical_id:
        return target.canonical_id
    return canonical_stock_code(normalize_stock_code(raw))


def _resolve_search_subject(stock_code: str, stock_name: str) -> tuple[str, str]:
    from src.services.stock_list_parser import ParseStatus, parse_analysis_target

    target = parse_analysis_target(stock_code)
    if target.asset_type == ParseStatus.INDEX and target.matched_index is not None:
        return "", target.matched_index.display_name
    return stock_code, stock_name


def _coerce_result_limit(value: Any, *, default: int = 8, upper_bound: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, upper_bound))


def _strip_search_markup(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"</?em>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _format_publish_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return text

    if numeric > 10_000_000_000:
        numeric = numeric / 1000
    try:
        return datetime.fromtimestamp(numeric).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (OSError, OverflowError, ValueError):
        return text


def _fetch_futu_research(keyword: str, *, size: int, lang: str, timeout: int = 8) -> dict:
    return _fetch_futu_news(keyword, size=size, lang=lang, news_type=3, timeout=timeout)


def _fetch_futu_news(
    keyword: str,
    *,
    size: int,
    lang: str,
    news_type: int = 1,
    timeout: int = 8,
) -> dict:
    """Fetch public Futu news without requiring a third-party search API key."""
    params = urlencode(
        {
            "keyword": keyword,
            "size": size,
            "news_type": news_type,
            "lang": lang,
            "sort_type": 2,
        }
    )
    url = f"{FUTU_NEWS_SEARCH_URL}?{params}"
    request = Request(url, headers={"User-Agent": FUTU_USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _futu_news_keyword_candidates(stock_code: str, stock_name: str) -> list[str]:
    """Prefer a verified company name over an ambiguous ticker such as PENG."""
    candidates: list[str] = []
    canonical_code = _canonical_search_code(stock_code)
    try:
        from src.data.stock_mapping import foreign_stock_english_aliases

        aliases = foreign_stock_english_aliases(canonical_code, stock_name)
    except Exception:  # Mapping is optional for unknown symbols.
        aliases = ()
    for value in (*aliases, stock_name, canonical_code, stock_code):
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    return candidates


def _futu_news_matches_subject(item: dict, keyword: str) -> bool:
    """Require a direct company-name match so ambiguous ticker searches stay fail-closed."""
    title = _strip_search_markup(item.get("title")).casefold()
    normalized_keyword = re.sub(r"\s+", " ", str(keyword or "").casefold()).strip()
    # A bare ticker is too ambiguous for this public endpoint (for example PENG).
    if len(normalized_keyword) < 5 or " " not in normalized_keyword:
        return False
    return normalized_keyword in title


def _search_futu_public_news(stock_code: str, stock_name: str, *, limit: int) -> dict:
    """Return direct company news from Futu as a no-key fallback for Agent news tools."""
    attempts = []
    last_error = ""
    for keyword in _futu_news_keyword_candidates(stock_code, stock_name):
        try:
            payload = _fetch_futu_news(keyword, size=limit, lang="en", news_type=1)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            continue
        if str(payload.get("code")) != "0":
            last_error = str(payload.get("message") or "Futu news search returned non-zero code")
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            continue
        raw_items = payload.get("data") or []
        if not isinstance(raw_items, list):
            last_error = "Futu news search returned invalid data shape"
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            continue
        items = [item for item in raw_items if isinstance(item, dict) and _futu_news_matches_subject(item, keyword)]
        attempts.append({"keyword": keyword, "success": True, "results_count": len(items)})
        if items:
            return {
                "success": True,
                "query": keyword,
                "provider": "Futu public news",
                "attempts": attempts,
                "items": items[:limit],
            }
        last_error = "Futu returned no direct company-news matches"
    return {
        "success": False,
        "provider": "Futu public news",
        "attempts": attempts,
        "error": last_error or "Futu public news search unavailable",
        "items": [],
    }


def _format_futu_news_result(payload: dict) -> dict:
    """Normalize public Futu result fields to the Agent news-tool response contract."""
    results = []
    for item in payload.get("items") or []:
        results.append({
            "title": _strip_search_markup(item.get("title")),
            "snippet": "",
            "url": str(item.get("url") or "").strip(),
            "source": "Futu public news",
            "published_date": _format_publish_time(item.get("publish_time")),
        })
    return {
        "query": payload.get("query", ""),
        "provider": payload.get("provider", "Futu public news"),
        "success": bool(results),
        "results_count": len(results),
        "results": results,
        "attempts": payload.get("attempts", []),
    }


def _persist_futu_news_result(*, stock_code: str, stock_name: str, result: dict) -> None:
    """Keep fallback news visible in the same best-effort evidence store as keyed providers."""
    from src.search_service import SearchResponse, SearchResult

    response = SearchResponse(
        query=str(result.get("query") or stock_name),
        provider=str(result.get("provider") or "Futu public news"),
        success=bool(result.get("success")),
        results=[
            SearchResult(
                title=str(item.get("title") or ""),
                snippet=str(item.get("snippet") or ""),
                url=str(item.get("url") or ""),
                source=str(item.get("source") or "Futu public news"),
                published_date=str(item.get("published_date") or "") or None,
            )
            for item in result.get("results") or []
            if isinstance(item, dict)
        ],
    )
    _persist_news_response(
        stock_code=stock_code,
        stock_name=stock_name,
        dimension="latest_news",
        response=response,
    )


def _try_futu_public_news_fallback(stock_code: str, stock_name: str) -> Optional[dict]:
    fallback = _search_futu_public_news(stock_code, stock_name, limit=5)
    if not fallback["success"]:
        return None
    result = _format_futu_news_result(fallback)
    _persist_futu_news_result(stock_code=stock_code, stock_name=stock_name, result=result)
    return result


def _persist_news_response(
    *,
    stock_code: str,
    stock_name: str,
    dimension: str,
    response,
) -> None:
    """Best-effort news persistence for Agent search tools."""
    if not response or not getattr(response, "success", False) or not getattr(response, "results", None):
        return

    code = _canonical_search_code(stock_code)
    try:
        saved_count = _get_db().save_news_intel(
            code=code,
            name=stock_name,
            dimension=dimension,
            query=response.query,
            response=response,
            query_context=None,
        )
        logger.info(
            "Agent news intel persisted for %s (dimension=%s, new_records=%s)",
            code,
            dimension,
            saved_count,
        )
    except Exception as exc:
        logger.warning(
            "Agent news intel persistence failed for %s (dimension=%s): %s",
            code,
            dimension,
            exc,
        )


def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock."""
    service = _get_search_service()
    query_code, query_name = _resolve_search_subject(stock_code, stock_name)

    if not service.is_available:
        fallback = _search_futu_public_news(query_code, query_name, limit=5)
        if fallback["success"]:
            result = _format_futu_news_result(fallback)
            _persist_futu_news_result(stock_code=query_code, stock_name=query_name, result=result)
            record_news_evidence(result["results_count"])
            return result
        record_news_evidence(0)
        return {
            "success": False,
            "provider": "Futu public news",
            "error": "No search engine available and Futu public-news fallback failed: " + fallback["error"],
            "attempts": fallback["attempts"],
            "results": [],
        }

    response = service.search_stock_news(query_code, query_name, max_results=5)

    if not response.success:
        fallback_result = _try_futu_public_news_fallback(query_code, query_name)
        if fallback_result is not None:
            record_news_evidence(fallback_result["results_count"])
            return fallback_result
        # 检索已发起但失败：Agent 这一轮没有拿到新闻证据，必须记 0 而不是不记，
        # 否则报告会把「搜过但失败」误报成「未配置搜索渠道」。
        record_news_evidence(0)
        return {
            "query": response.query,
            "success": False,
            "error": response.error_message,
        }

    record_news_evidence(len(response.results))

    _persist_news_response(
        stock_code=stock_code,
        stock_name=query_name,
        dimension="latest_news",
        response=response,
    )

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {
                "title": r.title,
                "snippet": r.snippet,
                "url": r.url,
                "source": r.source,
                "published_date": r.published_date,
            }
            for r in response.results
        ],
    }


search_stock_news_tool = ToolDefinition(
    name="search_stock_news",
    description="Search for the latest news articles about a specific stock. "
                "Requires both stock_code and stock_name for accurate search. "
                "Returns news titles, snippets, sources, and URLs.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_stock_news,
    category="search",
    policy=_NEWS_READ_POLICY,
)


# ============================================================
# search_comprehensive_intel
# ============================================================

def _handle_search_comprehensive_intel(stock_code: str, stock_name: str) -> dict:
    """Multi-dimensional intelligence search."""
    service = _get_search_service()
    query_code, query_name = _resolve_search_subject(stock_code, stock_name)

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    intel_results = service.search_comprehensive_intel(
        stock_code=query_code,
        stock_name=query_name,
        max_searches=6,
    )

    if not intel_results:
        # 多维检索已发起但整体没有结果，同样必须记 0（见 _handle_search_stock_news）。
        record_news_evidence(0)
        return {"error": "Comprehensive intel search returned no results"}

    # Format into readable report
    report = service.format_intel_report(intel_results, query_name)

    # 本次真正交给 Agent 的证据条数，按维度累计后一次性记录。
    evidence_count = 0

    # Also return structured data
    dimensions = {}
    for dim_name, response in intel_results.items():
        if response and response.success:
            evidence_count += len(response.results)
            _persist_news_response(
                stock_code=stock_code,
                stock_name=query_name,
                dimension=dim_name,
                response=response,
            )
            dimensions[dim_name] = {
                "query": response.query,
                "results_count": len(response.results),
                "results": [
                    {
                        "title": r.title,
                        "snippet": r.snippet,
                        "source": r.source,
                    }
                    for r in response.results[:3]  # limit to 3 per dimension to save tokens
                ],
            }

    record_news_evidence(evidence_count)

    return {
        "report": report,
        "dimensions": dimensions,
    }


search_comprehensive_intel_tool = ToolDefinition(
    name="search_comprehensive_intel",
    description="Multi-dimensional intelligence search: latest news, market analysis, "
                "risk checking, earnings outlook, and industry trends for a stock. "
                "Returns a formatted report and structured results.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_comprehensive_intel,
    category="search",
    policy=_INTEL_READ_POLICY,
)


def _handle_search_research_reports(
    stock_code: str,
    stock_name: str = "",
    max_results: int = 8,
    lang: str = "zh-CN",
) -> dict:
    """Search public broker research / rating updates for a stock."""
    limit = _coerce_result_limit(max_results)
    keyword_candidates = []
    for candidate in (stock_code, stock_name):
        cleaned = str(candidate or "").strip()
        if cleaned and cleaned not in keyword_candidates:
            keyword_candidates.append(cleaned)

    if not keyword_candidates:
        return {
            "success": False,
            "error": "stock_code or stock_name is required",
            "results": [],
        }

    attempts = []
    last_error = ""
    for keyword in keyword_candidates:
        try:
            payload = _fetch_futu_research(keyword, size=limit, lang=str(lang or "zh-CN"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            logger.warning("Futu research search failed for %s: %s", keyword, exc)
            continue

        if str(payload.get("code")) != "0":
            last_error = str(payload.get("message") or "Futu research search returned non-zero code")
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            continue

        raw_items = payload.get("data") or []
        if not isinstance(raw_items, list):
            last_error = "Futu research search returned invalid data shape"
            attempts.append({"keyword": keyword, "success": False, "error": last_error})
            continue

        results = []
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": _strip_search_markup(item.get("title")),
                    "publish_time": _format_publish_time(item.get("publish_time")),
                    "url": str(item.get("url") or "").strip(),
                    "news_id": str(item.get("news_id") or "").strip(),
                    "source": "Futu research search",
                }
            )

        attempts.append({"keyword": keyword, "success": True, "results_count": len(results)})
        if results:
            return {
                "query": keyword,
                "provider": "Futu",
                "success": True,
                "results_count": len(results),
                "results": results,
                "attempts": attempts,
                "disclaimer": "Public research/rating headlines only; verify full broker reports and primary filings before relying on details.",
            }

        last_error = "No research report results found"

    return {
        "provider": "Futu",
        "success": False,
        "error": last_error or "No research report results found",
        "results": [],
        "attempts": attempts,
    }


search_research_reports_tool = ToolDefinition(
    name="search_research_reports",
    description="Search public broker research reports and analyst rating/target-price updates for a stock. "
                "Uses public Futu research-search results and returns titles, publish times, and URLs. "
                "Use this to ground buy-side memo or Serenity-style research reads; do not invent full report details.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code or ticker, e.g., 'ADBE', '600519', 'HK.00700'.",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Company or stock name used as fallback keyword, e.g., 'Adobe' or '贵州茅台'.",
            required=False,
            default="",
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Maximum number of research results to return, capped at 20.",
            required=False,
            default=8,
        ),
        ToolParameter(
            name="lang",
            type="string",
            description="Result language hint for Futu search.",
            required=False,
            enum=["zh-CN", "zh-HK", "en"],
            default="zh-CN",
        ),
    ],
    handler=_handle_search_research_reports,
    category="search",
    policy=_RESEARCH_READ_POLICY,
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
    search_research_reports_tool,
]
