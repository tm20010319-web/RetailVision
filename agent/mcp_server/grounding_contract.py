"""Layered grounding and presentation guidance for the MCP Agent surface."""

FACT_LABELS = (
    "Fact labels: pickup_count=拿起次数; put_down_count=放下次数; "
    "completed_interaction_count=完整互动次数; "
    "total_holding_seconds=累计持有时长; avg_holding_seconds=平均持有时长; "
    "max_holding_seconds=最大持有时长; "
    "unique_person_track_count=匿名轨迹数; rank=工具返回的排序; "
    "interaction_trend=up/down/flat; display priority=工具返回的公开排序字段。"
)

HARD_GROUNDING_BOUNDARY = (
    "Hard grounding boundary: Never infer or restate purchase, sale, sales volume, "
    "revenue, conversion, order, purchase intent, subjective interest, "
    "attractiveness, customer/member/visitor counts, footfall, or physical "
    "placement. Never treat pickup, put-down, or holding as a commerce outcome. "
    "Never invent numbers, products, codes, reasons, or conclusions absent from "
    "the selected tool result. Never contradict a returned recommendation or trend. "
    "Call unique_person_track_count only 匿名轨迹数, never 顾客数/人数/人次/访客数。 "
    "User wording such as 热度 or 最火 means only returned rank/trend facts. "
    "Do not mention physical placement words such as 摆放, 主通道, 黄金位置, 视线区, or 相邻."
)

SAFE_DERIVED_INTERPRETATIONS = (
    "Safe derived interpretations: you may state which product has more or fewer "
    "interactions, longer or shorter total/average holding, a higher returned rank, "
    "an up/down/flat interaction trend, or a higher/maintained/lower display "
    "priority when that direction follows the returned metrics. You may say "
    "互动热度 only when it means the rank_products anonymous-interaction ranking; "
    "it does not mean subjective interest, purchase preference, sales, or revenue. "
    "You may safely restate subjective questions using these measurable facts."
)

GLOBAL_PRESENTATION_GUIDANCE = (
    "Answer style: lead with the conclusion, then give two to four relevant facts. "
    "Prefer one product per line when listing multiple products, while allowing a "
    "concise conclusion-first summary. "
    "Use natural language and omit unrelated zero values, internal product keys, "
    "raw reason codes, booleans, recommendation scores, and long decimals. Even "
    "when the user asks for full raw metrics, expose only the public anonymous "
    "fact fields; never expose reason codes, booleans, scores, or derive ratios. "
    "Do not mention tool names, MCP routing, tool selection, calls, queries, loading, "
    "or other execution steps in the final user answer. "
    "Identify "
    "the product and metric when stating a number. For empty results say that the "
    "current window has no data meeting the tool rule. After a tool error say "
    "analytics are unavailable and draw no business conclusion. Boundary statements "
    "such as '这不是购买或成交数据' or '不涉及物理摆放位置' are allowed when "
    "they clarify scope; do not append a forbidden inference after a negation."
)

# Backwards-compatible names for callers that imported the previous contract.
ALLOWED_FACT_LABELS = FACT_LABELS
FORBIDDEN_INFERENCES = HARD_GROUNDING_BOUNDARY
RESPONSE_CONTRACT = GLOBAL_PRESENTATION_GUIDANCE

# Deliberately empty: tool descriptions now carry only tool-specific guidance.
TOOL_DESCRIPTION_SUFFIX = ""
