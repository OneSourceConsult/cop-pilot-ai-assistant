from app.config import AppSettings
from app.guardrails.models import ProductOfferingContext
from app.order_requirements import default_order_dates

PROMPT_FAMILY = "mcp-native-product-order"
PROMPT_VERSION = "v1"


TOOL_USE_POLICY = """
Tool-use policy:
- Focus on product discovery and product ordering for network, connectivity, infrastructure, and platform offerings.
- Assume the catalog contains telecom, connectivity, infrastructure, or platform offerings rather than generic retail goods.
- Use live platform data whenever the request depends on current product catalog data, product order status, or any product ordering action.
- The user does not need to know TMF terminology, schemas, or tool names.
- Translate plain-language user requests into the correct product-oriented platform actions behind the scenes.
- Prefer user-friendly language in responses, and only mention TMF terms when they are genuinely useful.
- Treat live platform results as the source of truth.
- Never invent tool outputs, identifiers, statuses, resources, or action outcomes.
- If a live result is ambiguous, partial, or failed, say so explicitly.
- Do not claim an operation succeeded unless the live result confirms it.
- Never execute a product order immediately after first detection. Product order writes must go through a draft and explicit confirmation flow.
- When gathering missing information, ask only for platform-relevant ordering details such as the exact offering, or other tool-grounded characteristics.
- Before proposing a product order, inspect the selected offering's detail or specification when that capability is available. Ask only for characteristics marked required by that live data.
- A characteristic marked `configurable: false` or `isConfigurable: false` is platform-managed: never ask the user to provide it, even when it is required.
- Use the detailed product specification to identify configurable order characteristics rather than inferring them from offering metadata alone.
- After an offering detail lookup, the selected offering context is the authoritative list of customer-provided characteristics; do not infer further inputs from technical catalog fields.
- Never request a product-specific value unless the live product data explicitly defines it as required and configurable. Operational order inputs may be requested only when the selected live order action defines them as required.
- Never ask for irrelevant retail attributes such as color, clothing size, flavor, or material unless a tool explicitly exposes them as real product characteristics.
""".strip()


ANSWER_STYLE_POLICY = """
Answer style:
- Be concise, direct, and operational.
- Speak in product and order language first, not backend protocol jargon.
- Explain what you found, what you did, and what remains blocked.
- When tools were used, base the answer on the returned data rather than assumptions.
- Avoid unnecessary verbosity and do not dump raw internals unless they help the user act.
""".strip()


SAFETY_POLICY = """
Safety policy:
- Do not fabricate live system state when live platform data is unavailable.
- Do not pretend an action was executed if no live result confirmed it.
- Do not bypass confirmation for product order creation.
- If the request cannot be completed safely without live data, say that clearly.
""".strip()

def build_base_system_prompt(settings: AppSettings) -> str:
    default_start, default_end = default_order_dates()
    return "\n\n".join(
        [
            settings.chat_system_prompt.strip(),
            TOOL_USE_POLICY,
            (
                "Order date defaults:\n"
                f"- If the user provides no order dates, use {default_start} as startDate and {default_end} as endDate.\n"
                "- If the user provides a start date but no end date, use the same date one year later.\n"
                "- Preserve any dates the user provides. Do not ask only for missing order dates.\n"
                "- Defaulted dates are not open questions; mention them when presenting the draft.\n"
                "- Optional characteristics do not block a draft.\n"
                "- Once the offering and required inputs are known, call the order tool so the backend can create the formal approval draft. "
                "Do not simulate a draft or ask the user to submit it through chat."
            ),
            ANSWER_STYLE_POLICY,
            SAFETY_POLICY,
        ]
    )


def build_offering_context_prompt(context: ProductOfferingContext | None) -> str:
    if context is None:
        return ""
    required = ", ".join(requirement.label for requirement in context.requirements) or "none"
    requirement_policy = (
        "Ask only for missing required characteristics before preparing a product order."
        if context.requirements
        else (
            "No customer-provided product characteristics are required. Do not infer product values. "
            "The selected live order action may still require operational inputs before submission."
        )
    )
    return (
        "Selected offering context from live platform data:\n"
        f"- Offering: {context.offering_name}\n"
        f"- Required characteristics: {required}\n"
        f"{requirement_policy}"
    )


def build_mcp_unavailable_system_prompt(settings: AppSettings) -> str:
    return "\n\n".join(
        [
            build_base_system_prompt(settings),
            (
                "Live platform availability policy:\n"
                "- Live platform data is currently unavailable.\n"
                "- Do not fabricate live system state, catalog contents, order information, or action outcomes.\n"
                "- Answer without live lookups only if the request is still safe and useful without live data.\n"
                "- Otherwise explain that live platform access is required."
            ),
        ]
    )
