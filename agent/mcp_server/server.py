"""RetailVision read-only MCP Server entrypoint."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from agent.analytics.product_interaction_mcp import ProductAnalyticsMCPTools
from agent.analytics.product_interaction_analytics import ProductAnalyticsQueryService
from agent.schema_evolution import (
    MemberBehaviorSchemaNotReadyError,
    ProductAnalyticsSchemaNotReadyError,
    require_member_behavior_schema,
    require_product_analytics_schema,
)
from agent.analytics.member_behavior import MemberBehaviorQueryService, MemberBehaviorScopePolicy
from agent.analytics.member_behavior_mcp import MemberBehaviorMCPTools
from agent.analytics.member_behavior_repository import SqlAlchemyMemberBehaviorReadAdapter
from agent.store_context import StoreContext
from agent.analytics.product_catalog import ProductCatalog
from agent.config_values import strict_bool_env

from .schemas import (
    SERVER_INFO,
    jsonrpc_error,
    jsonrpc_result,
    member_tool_definitions,
    negotiate_protocol_version,
    text_result,
    tool_definitions,
)
from .tools import MemberBehaviorMCPToolDispatcher, ProductAnalyticsMCPToolDispatcher
from .transport import StdioJsonRpcTransport
from .grounding_contract import (
    FACT_LABELS,
    HARD_GROUNDING_BOUNDARY,
    SAFE_DERIVED_INTERPRETATIONS,
)

logger = logging.getLogger(__name__)

_PRODUCT_CATALOG_PROFILES = {
    "stage10_smart_toys_v1": (
        "智能熊=smart_toy_a,智能象/智能小象=smart_toy_b"
    ),
}


def configured_product_aliases(*, require_profile: bool = False) -> str | None:
    aliases = (os.getenv("RETAILVISION_PRODUCT_ALIASES") or "").strip()
    if aliases:
        return aliases
    profile = (os.getenv("RETAILVISION_PRODUCT_CATALOG_PROFILE") or "").strip()
    if profile:
        try:
            return _PRODUCT_CATALOG_PROFILES[profile]
        except KeyError as exc:
            raise ValueError("product_catalog_profile_unknown") from exc
    if require_profile:
        raise ValueError("product_catalog_required")
    return None


def configured_product_catalog() -> ProductCatalog:
    return ProductCatalog.from_alias_spec(configured_product_aliases())


def build_agent_instructions(
    store_context: StoreContext,
    *,
    product_aliases: str | None = None,
) -> str:
    """Publish trusted canary scope and grounding policy to MCP clients."""
    strict_inference_boundary = strict_bool_env(
        "STORE_STRICT_INFERENCE_BOUNDARY", "true"
    )
    camera_id = store_context.default_camera_id or "all-configured-cameras"
    aliases = (product_aliases or "").strip()
    alias_line = (
        f"Trusted product aliases: {aliases}."
        if aliases
        else (
            "No product catalog profile is configured; Product Analytics tools "
            "remain unavailable until deployment configures one."
        )
    )
    return "\n".join((
        "RetailVision anonymous Product Analytics single-store canary.",
        f"Trusted scope: store_id={store_context.store_id}; camera_id={camera_id}.",
        "For anonymous Product Analytics tools, never ask the user to choose or override store_id or camera_id. Use only their trusted values above; the server rejects any mismatch. Separate Member Behavior tools have their own scope and defaults below.",
        (
            "Default naive-local query window: "
            f"start_at={store_context.default_start_at.isoformat()}; "
            f"end_at={store_context.default_end_at.isoformat()}. "
            "Use this window only for anonymous Product Analytics relative requests such as recently; never exceed 31 days. Do not reuse it for Member Behavior tools."
        ),
        alias_line,
        "Tool routing is strict: hottest products -> rank_products; one product performance -> get_product_behavior_summary; two products -> compare_product_behavior; which products deserve priority display (推荐重点陈列哪些商品) -> recommend_hot_products; rising heat -> analyze_product_trend; anomalous behavior -> detect_product_anomaly; priority-based display action (商品应该怎样调整陈列) -> suggest_product_display. Explicit requests to list all raw metrics use rank_products in the trusted default window. Do not substitute one display tool for the other.",
        "A complete visual-interaction Store report is a composed query: use rank_products for overall product interaction and list_recent_member_behaviors for confirmed member behavior, even when the user does not explicitly say member. If a separate operating-data source is available, financial/stock/operating reports use that source instead. Keep each tool's reported time scope separate and never attribute anonymous product metrics to a named member.",
        "For anonymous visual Product Analytics questions, use these RetailVision MCP tools only. Operating-data questions use their independently configured Store Business MCP tools. Do not inspect files, raw or restricted artifacts, database tables, ORM, or SQL.",
        (
            "Treat subjective popularity or interest wording as a request for interaction ranking or holding facts. Those metrics do not establish subjective preference. Do not invent multipliers, ratios, percentages, people counts, or purchase tendencies; only repeat a value when it is a public field returned by the selected tool."
            if strict_inference_boundary else
            "开发分析模式：拿起次数、互动次数、持有时长和排序是观测事实。可以明确标注为分析判断，说明互动更高或持有更久可能代表相对更高的互动热度、关注度或兴趣，并据此给出陈列、导购、活动实验建议。必须区分观测事实、分析判断和建议；不得编造数值、商品或事件，也不能声称已经购买、成交、产生销量、收入或转化。"
        ),
        "For every concrete product analytics question, call the appropriate MCP tool before giving a final answer; do not answer from these instructions alone. Subjective wording still routes to rank_products and must be grounded in its returned results.",
        FACT_LABELS,
        (
            HARD_GROUNDING_BOUNDARY
            if strict_inference_boundary else
            "宽松分析仍需事实落地：建议只能建立在本次工具返回的公开字段上；不能把匿名轨迹数说成顾客人数，也不能把建议的陈列位置说成系统已观测到的实际位置。"
        ),
        (
            SAFE_DERIVED_INTERPRETATIONS
            if strict_inference_boundary else
            "可以综合多个公开指标形成自然的经营分析，不要求逐字段播报；对于推断和建议使用“从当前互动数据看”“建议尝试”等表述。"
        ),
        "Do not characterize a summary as normal, stable, deep, or fluctuating unless that exact conclusion is returned by the selected tool. When a comparison returns a recommendation, repeat that product key consistently and do not recommend the other product.",
        "Never expose internal table names, SQL, raw person track tokens or hashes, identity candidates, or Face bounding boxes. Anonymous Product Analytics results must not be used to identify a member; Member Behavior tools may expose only their explicit public member fields.",
    ))


def build_member_agent_instructions(store_context: StoreContext, timezone_name: str) -> str:
    camera_scope = (
        f"camera_id={store_context.default_camera_id}"
        if store_context.default_camera_id is not None
        else "camera_scope=store_all; omit camera_id"
    )
    strict_inference_boundary = strict_bool_env(
        "STORE_STRICT_INFERENCE_BOUNDARY", "true"
    )
    return "\n".join((
        "RetailVision confirmed Offline Member Behavior tools.",
        f"Trusted scope: Member Behavior store_id={store_context.store_id}; {camera_scope}.",
        f"Trusted store timezone: {timezone_name}.",
        (
            "Default naive-local query window: Member Behavior "
            f"start_at={store_context.default_start_at.isoformat()}; "
            f"end_at={store_context.default_end_at.isoformat()}. "
            "These defaults apply only to Member Behavior tools."
        ),
        "Member Behavior scope arguments are optional. For a recent/general member request without an explicit narrower scope, omit store_id, camera_id, start_at and end_at: the server applies the member defaults above. list_recent_member_behaviors accepts {}. Never copy the Product Analytics camera or time window into a member call.",
        "A store_all camera scope is represented by omitting camera_id, not by an ID named store-wide/all-configured-cameras and not by null. For an explicitly requested date range, provide start_at and end_at together only within the member window; do not silently replace an out-of-range request with the defaults.",
        "Member Behavior tools read only formally published Offline BehaviorEvent facts with confirmed identity.",
        "工具路由：未指定会员的最近、全部或整体会员行为，调用 list_recent_member_behaviors；点名会员的汇总调用 get_member_behavior_summary，点名会员的逐条记录调用 list_member_behavior_timeline。只要问题包含会员行为、会员互动或会员商品分析，就优先遵守这条会员路由；即使用户同时要求商品报告或经营建议，也不能用 rank_products 替代会员行为工具。",
        "完整、综合或全量视觉互动报告必须组合 rank_products 与 list_recent_member_behaviors；分别说明匿名商品互动和已确认会员行为，不得因为用户没有明确写出“会员”就省略会员章节。若另有经营数据源，财务、库存及综合经营报告使用经营工具，不用视觉指标替代。",
        "Do not attribute anonymous, candidate, probable, ambiguous, conflict, unmatched, or unavailable identity to a member.",
        (
            "Do not infer purchases, purchase intent, preferences, spending ability, or identity relationships from pickup, holding, or put-down facts."
            if strict_inference_boundary else
            "开发分析模式下，可以把拿起次数、互动频次和持有时长作为互动兴趣或关注度信号，形成分析判断，并提出陈列、导购、活动建议。请明确区分观测事实、分析判断和建议；不能声称已经购买或成交，也不能编造工具未返回的数值、事件或会员关系。"
        ),
        "Answer member behavior questions only from RetailVision MCP tool results; do not read files, databases, SQL, or Artifacts.",
    ))


class ProductAnalyticsMCPServer:
    """Protocol dispatcher with no direct ORM or SQL business operations."""

    def __init__(
        self,
        dispatcher: ProductAnalyticsMCPToolDispatcher | None,
        *,
        enabled: bool = False,
        member_dispatcher: MemberBehaviorMCPToolDispatcher | None = None,
        product_enabled: bool | None = None,
        member_enabled: bool = False,
        instructions: str = "",
        close_callback: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("product_analytics_mcp_enabled_must_be_boolean")
        if product_enabled is None:
            product_enabled = enabled
        if not isinstance(product_enabled, bool) or not isinstance(member_enabled, bool):
            raise ValueError("mcp_enabled_must_be_boolean")
        self.dispatcher = dispatcher
        self.product_dispatcher = dispatcher
        self.member_dispatcher = member_dispatcher
        self.product_enabled = product_enabled
        self.member_enabled = member_enabled
        # Compatibility attribute; it no longer controls protocol tools/list.
        self.enabled = product_enabled
        self.instructions = instructions
        self.initialized = False
        self._close_callback = close_callback
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._close_callback is not None:
            self._close_callback()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(method, str):
            return jsonrpc_error(request_id, -32600, "method_required")
        if method == "initialize":
            if not isinstance(params, dict):
                return jsonrpc_error(request_id, -32602, "params_object_required")
            self.initialized = True
            result = {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
            if self.instructions:
                result["instructions"] = self.instructions
            return jsonrpc_result(request_id, result)
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return jsonrpc_result(request_id, {})
        if not self.initialized:
            return jsonrpc_error(request_id, -32002, "server_not_initialized")
        if method == "tools/list":
            definitions = []
            if self.product_enabled:
                definitions.extend(tool_definitions())
            if self.member_enabled:
                definitions.extend(member_tool_definitions())
            return jsonrpc_result(request_id, {"tools": definitions})
        if method == "tools/call":
            if not isinstance(params, dict):
                return jsonrpc_error(request_id, -32602, "params_object_required")
            name = params.get("name")
            product_names = {item["name"] for item in tool_definitions()}
            member_names = {item["name"] for item in member_tool_definitions()}
            if isinstance(name, str) and name in product_names:
                if not self.product_enabled or self.product_dispatcher is None:
                    return jsonrpc_result(request_id, text_result({"error": "product_analytics_mcp_disabled"}, is_error=True))
                result = self.product_dispatcher.call(name, params.get("arguments", {}))
            elif isinstance(name, str) and name in member_names:
                if not self.member_enabled or self.member_dispatcher is None:
                    return jsonrpc_result(request_id, text_result({"error": "member_behavior_mcp_disabled"}, is_error=True))
                result = self.member_dispatcher.call(name, params.get("arguments", {}))
            else:
                result = text_result({"error": "unknown_tool"}, is_error=True)
            return jsonrpc_result(request_id, result)
        return jsonrpc_error(request_id, -32601, f"method_not_found: {method}")


def build_default_server(
    *,
    database_url: str | None = None,
    member_database_url: str | None = None,
    enabled: bool | None = None,
    product_enabled: bool | None = None,
    member_enabled: bool | None = None,
) -> ProductAnalyticsMCPServer:
    """Compose enabled read-only feature dispatchers without schema mutation."""
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise ValueError("product_analytics_mcp_enabled_must_be_boolean")
        if product_enabled is None:
            product_enabled = enabled
    if product_enabled is None:
        product_enabled = strict_bool_env("RETAILVISION_PRODUCT_ANALYTICS_MCP_ENABLED", "false")
    if member_enabled is None:
        member_enabled = strict_bool_env("RETAILVISION_MEMBER_BEHAVIOR_MCP_ENABLED", "false")
    if not product_enabled and not member_enabled:
        return ProductAnalyticsMCPServer(None, enabled=False, product_enabled=False, member_enabled=False)

    member_context = (
        StoreContext.from_environment(
            require_explicit_scope=True,
            environment_prefix="RETAILVISION_MEMBER_",
            fallback_prefix="RETAILVISION_",
        )
        if member_enabled else None
    )
    product_context = StoreContext.from_environment()
    product_aliases = None
    if product_enabled:
        product_aliases = configured_product_aliases(require_profile=True)
    from agent.paths import default_database_url, resolve_database_url

    product_url = resolve_database_url(
        database_url or os.getenv("RETAILVISION_DATABASE_URL")
        or os.getenv("DATABASE_URL") or default_database_url()
    )
    member_url = resolve_database_url(
        member_database_url or os.getenv("RETAILVISION_MEMBER_DATABASE_URL")
        or product_url
    )
    if product_enabled:
        _require_existing_sqlite_database(
            product_url, product_enabled=True, member_enabled=False
        )
    if member_enabled:
        _require_existing_sqlite_database(
            member_url, product_enabled=False, member_enabled=True
        )
    engines: dict[str, Any] = {}

    def read_only_engine(url: str) -> Any:
        if url in engines:
            return engines[url]
        engine = create_engine(url)
        if engine.dialect.name == "sqlite":
            @event.listens_for(engine, "connect")
            def _set_sqlite_query_only(dbapi_connection: Any, _: Any) -> None:
                dbapi_connection.execute("PRAGMA query_only = ON")
        engines[url] = engine
        return engine

    product_engine = read_only_engine(product_url) if product_enabled else None
    member_engine = read_only_engine(member_url) if member_enabled else None
    try:
        if product_enabled:
            require_product_analytics_schema(product_engine)
        if member_enabled:
            require_member_behavior_schema(member_engine)
            timezone_name = (
                os.getenv("RETAILVISION_MEMBER_TIMEZONE")
                or os.getenv("RETAILVISION_TIMEZONE", "")
            )
            member_policy = MemberBehaviorScopePolicy(member_context, timezone_name)
        else:
            member_policy = None
            timezone_name = None
    except Exception:
        for engine in engines.values():
            engine.dispose()
        raise
    product_sessions = (
        sessionmaker(bind=product_engine, expire_on_commit=False)
        if product_engine is not None else None
    )
    member_sessions = (
        sessionmaker(bind=member_engine, expire_on_commit=False)
        if member_engine is not None else None
    )
    product_dispatcher = None
    if product_enabled:
        adapter = ProductAnalyticsMCPTools(
            ProductAnalyticsQueryService(product_sessions), product_context,
            product_catalog=ProductCatalog.from_alias_spec(product_aliases),
        )
        product_dispatcher = ProductAnalyticsMCPToolDispatcher(adapter)
    member_dispatcher = None
    if member_enabled:
        member_adapter = MemberBehaviorMCPTools(
            MemberBehaviorQueryService(
                SqlAlchemyMemberBehaviorReadAdapter(member_sessions), member_policy
            )
        )
        member_dispatcher = MemberBehaviorMCPToolDispatcher(member_adapter)
    instructions = []
    if product_enabled:
        instructions.append(build_agent_instructions(product_context, product_aliases=product_aliases))
    if member_enabled:
        instructions.append(build_member_agent_instructions(member_context, timezone_name))

    def close_engines() -> None:
        for engine in engines.values():
            engine.dispose()

    return ProductAnalyticsMCPServer(
        product_dispatcher,
        enabled=bool(product_enabled),
        product_enabled=product_enabled,
        member_enabled=member_enabled,
        member_dispatcher=member_dispatcher,
        instructions="\n".join(instructions),
        close_callback=close_engines,
    )


def _require_existing_sqlite_database(
    database_url: str,
    *,
    product_enabled: bool = True,
    member_enabled: bool = False,
) -> None:
    parsed = make_url(database_url)
    if not parsed.drivername.startswith("sqlite"):
        return
    database = parsed.database
    if not database or database == ":memory:":
        return
    if not Path(database).is_file():
        if member_enabled and not product_enabled:
            raise MemberBehaviorSchemaNotReadyError(
                "member_behavior_schema_incompatible"
            )
        raise ProductAnalyticsSchemaNotReadyError(
            "product_analytics_schema_incompatible"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--member-database-url", default=None)
    args = parser.parse_args(argv)
    # MCP stdio is a UTF-8 JSON-RPC boundary even when the host Windows
    # console defaults to a legacy code page.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    logging.basicConfig(stream=__import__("sys").stderr, level=logging.INFO)
    try:
        server = build_default_server(
            database_url=args.database_url,
            member_database_url=args.member_database_url,
        )
    except (ProductAnalyticsSchemaNotReadyError, MemberBehaviorSchemaNotReadyError) as error:
        logger.error(str(error))
        return 2
    except ValueError as error:
        code = str(error)
        if code not in {
            "product_catalog_required",
            "product_catalog_profile_unknown",
            "product_catalog_invalid",
            "member_behavior_timezone_required",
            "member_behavior_timezone_invalid",
            "store_context_store_id_required",
            "store_context_analysis_window_required",
            "store_context_time_invalid",
            "store_context_time_range_invalid",
            "query_time_range_exceeds_maximum",
            "invalid_time",
        }:
            raise
        logger.error(code)
        return 2
    try:
        StdioJsonRpcTransport(server.handle).serve()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
