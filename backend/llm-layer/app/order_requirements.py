from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime

from app.guardrails.models import ProductOfferingContext, ProductRequirement


ORDER_TOOLS_WITH_DATES = {"createProductOrder", "createServiceOrder"}


def apply_order_date_defaults(
    tool_name: str,
    arguments: dict[str, object],
    *,
    today: date | None = None,
) -> tuple[dict[str, object], list[str]]:
    """Fill missing order dates while preserving dates supplied by the user."""
    resolved = dict(arguments)
    if tool_name not in ORDER_TOOLS_WITH_DATES:
        return resolved, []

    current_date = today or datetime.now(UTC).date()
    defaulted: list[str] = []
    start_value = resolved.get("startDate")
    start_date = _iso_date(start_value)
    if _is_missing(start_value):
        start_date = current_date
        resolved["startDate"] = start_date.isoformat()
        defaulted.append("startDate")
    elif start_date is None:
        start_date = current_date

    if _is_missing(resolved.get("endDate")):
        resolved["endDate"] = _one_year_after(start_date).isoformat()
        defaulted.append("endDate")

    return resolved, defaulted


def default_order_dates(*, today: date | None = None) -> tuple[str, str]:
    start_date = today or datetime.now(UTC).date()
    return start_date.isoformat(), _one_year_after(start_date).isoformat()


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _one_year_after(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def offering_context_from_result(result: str) -> ProductOfferingContext | None:
    """Extract the selected offering's mandatory characteristics from a TMF-style response."""
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None

    fallback: ProductOfferingContext | None = None
    for candidate in _objects(payload):
        requirements = _requirements_from(candidate)
        if requirements:
            return ProductOfferingContext(
                offering_id=_first_text(candidate, "id", "productOfferingId"),
                offering_name=_first_text(candidate, "name", "productOfferingName") or "Selected product",
                requirements=requirements,
            )
        if fallback is None and _has_characteristic_definitions(candidate):
            fallback = ProductOfferingContext(
                offering_id=_first_text(candidate, "id", "productOfferingId"),
                offering_name=_first_text(candidate, "name", "productOfferingName") or "Selected product",
                requirements=[],
            )
    return fallback


def product_specification_id_from_result(result: str) -> str | None:
    """Extract a linked product specification identifier from an offering response."""
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return None

    for candidate in _objects(payload):
        specification = candidate.get("productSpecification")
        if isinstance(specification, dict):
            specification_id = _first_text(specification, "id", "productSpecId", "productSpecificationId")
            if specification_id:
                return specification_id
    return None


def platform_managed_characteristic_names(result: str) -> set[str]:
    """Return characteristic names explicitly managed by the platform."""
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return set()

    names: set[str] = set()
    for candidate in _objects(payload):
        for key in (
            "productOfferingCharacteristic",
            "productSpecCharacteristic",
            "characteristicSpecification",
            "prodSpecCharValueUse",
        ):
            values = candidate.get(key)
            if not isinstance(values, list):
                continue
            for characteristic in values:
                if isinstance(characteristic, dict) and not _is_customer_configurable(characteristic):
                    name = _first_text(characteristic, "name", "characteristicName")
                    if name:
                        names.add(_normalize_key(name))
    return names


def model_safe_offering_result(result: str, platform_managed_names: set[str] | None = None) -> str:
    """Hide platform-managed characteristics from the conversational tool result."""
    try:
        payload = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return result

    return json.dumps(
        _remove_platform_managed_characteristics(payload, platform_managed_names or set()),
        ensure_ascii=True,
    )


def missing_requirement_keys(
    arguments: dict[str, object], requirements: Iterable[ProductRequirement]
) -> list[ProductRequirement]:
    values = _flatten_values(arguments)
    return [requirement for requirement in requirements if not _has_value(values, requirement.key)]


def _objects(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _objects(nested)


def _remove_platform_managed_characteristics(
    value: object,
    platform_managed_names: set[str],
    parent_key: str | None = None,
) -> object:
    characteristic_list_keys = {
        "productOfferingCharacteristic",
        "productSpecCharacteristic",
        "characteristicSpecification",
        "prodSpecCharValueUse",
    }
    if isinstance(value, list):
        items = value
        if parent_key in characteristic_list_keys:
            items = [
                item
                for item in value
                if not isinstance(item, dict)
                or (
                    _is_customer_configurable(item)
                    and _normalize_key(_first_text(item, "name", "characteristicName") or "")
                    not in platform_managed_names
                )
            ]
        return [_remove_platform_managed_characteristics(item, platform_managed_names) for item in items]
    if isinstance(value, dict):
        return {
            key: _remove_platform_managed_characteristics(nested, platform_managed_names, key)
            for key, nested in value.items()
        }
    return value


def _requirements_from(offering: dict[str, object]) -> list[ProductRequirement]:
    characteristic_lists: list[object] = []
    for key in (
        "productOfferingCharacteristic",
        "productSpecCharacteristic",
        "characteristicSpecification",
        "prodSpecCharValueUse",
    ):
        characteristic_lists.append(offering.get(key))

    specification = offering.get("productSpecification")
    if isinstance(specification, dict):
        for key in (
            "productSpecCharacteristic",
            "characteristicSpecification",
            "prodSpecCharValueUse",
        ):
            characteristic_lists.append(specification.get(key))

    requirements: list[ProductRequirement] = []
    seen: set[str] = set()
    for values in characteristic_lists:
        if not isinstance(values, list):
            continue
        for characteristic in values:
            if (
                not isinstance(characteristic, dict)
                or not _is_required(characteristic)
                or not _is_customer_configurable(characteristic)
            ):
                continue
            name = _first_text(characteristic, "name", "characteristicName")
            if name and name not in seen:
                seen.add(name)
                requirements.append(ProductRequirement(key=name, label=name))
    return requirements


def _has_characteristic_definitions(offering: dict[str, object]) -> bool:
    return any(
        isinstance(offering.get(key), list)
        for key in (
            "productOfferingCharacteristic",
            "productSpecCharacteristic",
            "characteristicSpecification",
            "prodSpecCharValueUse",
        )
    )


def _is_required(characteristic: dict[str, object]) -> bool:
    if characteristic.get("required") is True or characteristic.get("mandatory") is True:
        return True
    cardinality = characteristic.get("minCardinality")
    try:
        return int(str(cardinality)) > 0
    except (TypeError, ValueError):
        return False


def _is_customer_configurable(characteristic: dict[str, object]) -> bool:
    """Do not request platform-managed characteristics from the customer."""
    configurable = characteristic.get("configurable", characteristic.get("isConfigurable"))
    return configurable not in (False, 0, "false", "False", "0")


def _flatten_values(arguments: dict[str, object]) -> dict[str, object]:
    values = dict(arguments)
    for key in ("characteristics", "productCharacteristic"):
        nested = arguments.get(key)
        if isinstance(nested, dict):
            values.update(nested)
        elif isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    name = _first_text(item, "name", "characteristicName")
                    if name:
                        values[name] = item.get("value")
    return values


def _has_value(values: dict[str, object], required_key: str) -> bool:
    normalized_required = _normalize_key(required_key)
    for key, value in values.items():
        if _normalize_key(str(key)) == normalized_required and value not in (None, "", [], {}):
            return True
    return False


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _first_text(value: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if candidate not in (None, ""):
            return str(candidate)
    return None
