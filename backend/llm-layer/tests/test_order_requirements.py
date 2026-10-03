from datetime import date

from app.order_requirements import (
    apply_order_date_defaults,
    missing_requirement_keys,
    model_safe_offering_result,
    offering_context_from_result,
    platform_managed_characteristic_names,
    product_specification_id_from_result,
)


def test_order_dates_default_to_one_year_from_today() -> None:
    arguments, defaulted = apply_order_date_defaults(
        "createProductOrder",
        {"offeringsWithCharacteristics": {"offering-1": {}}},
        today=date(2026, 9, 21),
    )

    assert arguments["startDate"] == "2026-09-21"
    assert arguments["endDate"] == "2027-09-21"
    assert defaulted == ["startDate", "endDate"]


def test_order_dates_preserve_start_and_default_end_from_it() -> None:
    arguments, defaulted = apply_order_date_defaults(
        "createProductOrder",
        {"startDate": "2027-06-01"},
        today=date(2026, 9, 21),
    )

    assert arguments["startDate"] == "2027-06-01"
    assert arguments["endDate"] == "2028-06-01"
    assert defaulted == ["endDate"]


def test_order_dates_preserve_complete_user_range() -> None:
    arguments, defaulted = apply_order_date_defaults(
        "createProductOrder",
        {"startDate": "2027-06-01", "endDate": "2027-08-31"},
        today=date(2026, 9, 21),
    )

    assert arguments == {"startDate": "2027-06-01", "endDate": "2027-08-31"}
    assert defaulted == []


def test_order_dates_do_not_replace_a_user_value() -> None:
    arguments, defaulted = apply_order_date_defaults(
        "createProductOrder",
        {"startDate": "next Monday", "endDate": "next December"},
        today=date(2026, 9, 21),
    )

    assert arguments == {"startDate": "next Monday", "endDate": "next December"}
    assert defaulted == []


def test_order_date_default_handles_leap_day() -> None:
    arguments, _ = apply_order_date_defaults(
        "createServiceOrder",
        {"startDate": "2028-02-29"},
        today=date(2026, 9, 21),
    )

    assert arguments["endDate"] == "2029-02-28"


def test_extracts_required_characteristics_from_product_specification() -> None:
    context = offering_context_from_result(
        '''{
            "id": "offering-1",
            "name": "Managed Connectivity",
            "productSpecification": {
                "productSpecCharacteristic": [
                    {"name": "bandwidth", "minCardinality": 1},
                    {"name": "installationDate", "mandatory": true},
                    {"name": "optionalNote", "minCardinality": 0}
                ]
            }
        }'''
    )

    assert context is not None
    assert context.offering_id == "offering-1"
    assert [requirement.key for requirement in context.requirements] == ["bandwidth", "installationDate"]


def test_excludes_required_non_configurable_technical_characteristics() -> None:
    context = offering_context_from_result(
        '''{
            "id": "offering-1",
            "name": "Dummy COPPILOT Product Offering",
            "productSpecification": {
                "productSpecCharacteristic": [
                    {"name": "SSPEC_GRAPH_NOTATION", "required": true, "configurable": false},
                    {"name": "bandwidth", "required": true, "configurable": true}
                ]
            }
        }'''
    )

    assert context is not None
    assert [requirement.key for requirement in context.requirements] == ["bandwidth"]


def test_keeps_context_when_the_specification_has_no_customer_requirements() -> None:
    context = offering_context_from_result(
        '''{
            "id": "specification-1",
            "name": "Platform-managed product",
            "productSpecCharacteristic": [
                {"name": "platformValue", "required": true, "configurable": false}
            ]
        }'''
    )

    assert context is not None
    assert context.requirements == []


def test_removes_non_configurable_technical_characteristics_from_model_context() -> None:
    result = model_safe_offering_result(
        '''{
            "productSpecification": {
                "productSpecCharacteristic": [
                    {"name": "SSPEC_GRAPH_NOTATION", "required": true, "configurable": false},
                    {"name": "bandwidth", "required": true, "configurable": true}
                ]
            }
        }'''
    )

    assert "SSPEC_GRAPH_NOTATION" not in result
    assert "bandwidth" in result


def test_removes_offering_characteristics_known_to_be_platform_managed() -> None:
    specification_result = '''{
        "productSpecCharacteristic": [
            {"name": "SSPEC_GRAPH_NOTATION", "configurable": false}
        ]
    }'''
    offering_result = '''{
        "prodSpecCharValueUse": [
            {"name": "SSPEC_GRAPH_NOTATION", "valueType": "LONGTEXT"}
        ]
    }'''

    names = platform_managed_characteristic_names(specification_result)
    result = model_safe_offering_result(offering_result, names)

    assert "SSPEC_GRAPH_NOTATION" not in result


def test_extracts_linked_product_specification_id() -> None:
    result = '''{
        "productSpecification": {"id": "specification-1"}
    }'''

    assert product_specification_id_from_result(result) == "specification-1"


def test_accepts_characteristic_values_in_tmf_characteristic_list() -> None:
    context = offering_context_from_result(
        '''{"name":"Managed Connectivity","productOfferingCharacteristic":[{"name":"bandwidth","required":true}]}'''
    )

    assert context is not None
    assert missing_requirement_keys(
        {"productCharacteristic": [{"name": "bandwidth", "value": "1 Gbps"}]}, context.requirements
    ) == []
