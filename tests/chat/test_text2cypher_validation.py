from app.chat.infrastructure.kg.validation.models import (
    CypherValidationTask,
    Neo4jStructuredSchema,
    Neo4jStructuredSchemaPropertyNumber,
)
from app.chat.infrastructure.kg.validation.utils.cypher_extractors import (
    extract_entities_for_validation,
)
from app.chat.infrastructure.kg.validation.schema_validation_rules import (
    build_validation_task_groups,
    validate_property_names_with_enum,
    validate_property_values_with_enum,
    validate_property_values_with_range,
)


def _build_schema() -> Neo4jStructuredSchema:
    return Neo4jStructuredSchema(
        node_props={
            "Product": [
                {
                    "property": "status",
                    "type": "STRING",
                    "values": ["active", "inactive"],
                    "distinct_count": 2,
                },
                Neo4jStructuredSchemaPropertyNumber(
                    property="price",
                    type="FLOAT",
                    min=0,
                    max=9999,
                ),
            ]
        },
        rel_props={
            "PURCHASED": [
                {
                    "property": "channel",
                    "type": "STRING",
                    "values": ["app", "web"],
                    "distinct_count": 2,
                },
                Neo4jStructuredSchemaPropertyNumber(
                    property="quantity",
                    type="INTEGER",
                    min=1,
                    max=10,
                ),
            ]
        },
        relationships=[
            {"start": "User", "type": "PURCHASED", "end": "Product"}
        ],
        metadata={},
    )


def test_schema_relationship_value_helpers_use_rel_props() -> None:
    schema = _build_schema()

    assert schema.get_relationship_property_values_enum() == {
        "PURCHASED": {"channel": {"app", "web"}}
    }
    rel_ranges = schema.get_relationship_property_values_range()
    assert list(rel_ranges.keys()) == ["PURCHASED"]
    assert rel_ranges["PURCHASED"]["quantity"].min == 1
    assert rel_ranges["PURCHASED"]["quantity"].max == 10


def test_build_validation_task_groups_splits_string_and_numeric_tasks() -> None:
    tasks = [
        CypherValidationTask(
            labels_or_types="Product",
            operator="=",
            property_name="status",
            property_value="archived",
            property_type="STRING",
        ),
        CypherValidationTask(
            labels_or_types="Product",
            operator="=",
            property_name="price",
            property_value=20000,
            property_type="FLOAT",
        ),
        CypherValidationTask(
            labels_or_types="Product",
            operator="=",
            property_name="unknown",
            property_value="x",
            property_type=None,
        ),
    ]

    groups = build_validation_task_groups(tasks)

    assert groups.name_checks == tasks
    assert [task.property_name for task in groups.enum_value_checks] == ["status"]
    assert [task.property_name for task in groups.range_value_checks] == ["price"]


def test_validate_property_names_with_enum_reports_missing_property() -> None:
    schema = _build_schema()
    tasks = [
        CypherValidationTask(
            labels_or_types="Product",
            operator="=",
            property_name="brand",
            property_value="Haier",
            property_type="STRING",
        )
    ]

    errors = validate_property_names_with_enum(
        schema.get_node_properties_enum(),
        tasks,
        "Node",
    )

    assert errors == [
        "Node ['Product'] does not have the property brand in the graph database."
    ]


def test_validate_property_values_with_enum_reports_missing_value() -> None:
    schema = _build_schema()
    tasks = [
        CypherValidationTask(
            labels_or_types="PURCHASED",
            operator="=",
            property_name="channel",
            property_value="store",
            property_type="STRING",
        )
    ]

    errors = validate_property_values_with_enum(
        schema.get_relationship_property_values_enum(),
        tasks,
        "Relationship",
    )

    assert errors == [
        "Relationship ['PURCHASED'] with property channel = store not found in graph database."
    ]


def test_validate_property_values_with_range_reports_out_of_range() -> None:
    schema = _build_schema()
    tasks = [
        CypherValidationTask(
            labels_or_types="PURCHASED",
            operator="=",
            property_name="quantity",
            property_value=99,
            property_type="INTEGER",
        )
    ]

    errors = validate_property_values_with_range(
        schema.get_relationship_property_values_range(),
        tasks,
        "Relationship",
    )

    assert errors == [
        "Relationship PURCHASED has property quantity = 99 which is out of range 1.0 to 10.0 in graph database."
    ]


def test_extract_entities_for_validation_reads_node_and_relationship_properties() -> None:
    parsed = extract_entities_for_validation(
        """
        MATCH (u:User {name: "Alice"})-[r:PURCHASED {channel: "web"}]->(p:Product)
        WHERE p.price > 99 AND r.quantity = 2
        RETURN p
        """
    )

    assert [task.model_dump() for task in parsed["nodes"]] == [
        {
            "labels_or_types": "User",
            "operator": "=",
            "property_name": "name",
            "property_value": "Alice",
            "property_type": None,
        },
        {
            "labels_or_types": "Product",
            "operator": ">",
            "property_name": "price",
            "property_value": "99",
            "property_type": None,
        },
    ]
    assert [task.model_dump() for task in parsed["relationships"]] == [
        {
            "labels_or_types": "PURCHASED",
            "operator": "=",
            "property_name": "channel",
            "property_value": "web",
            "property_type": None,
        },
        {
            "labels_or_types": "PURCHASED",
            "operator": "=",
            "property_name": "quantity",
            "property_value": "2",
            "property_type": None,
        },
    ]
