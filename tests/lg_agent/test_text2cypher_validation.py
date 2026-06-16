import asyncio

from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.text2cypher.validation.models import (
    CypherValidationTask,
    Neo4jStructuredSchema,
    Neo4jStructuredSchemaPropertyNumber,
    ValidateCypherOutput,
)
from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.text2cypher.validation.schema_validation_rules import (
    validate_property_names_with_enum,
    validate_property_values_with_enum,
    validate_property_values_with_range,
)
from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.text2cypher.validation.utils.cypher_extractors import (
    extract_entities_for_validation,
)
from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.text2cypher.validation.validators import (
    validate_cypher_query_with_llm,
    validate_cypher_query_with_schema,
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


def test_validate_cypher_query_with_llm_sanitizes_graph_schema_for_prompt() -> None:
    captured: list[dict[str, str]] = []

    class FakeChain:
        async def ainvoke(self, payload: dict[str, str]) -> ValidateCypherOutput:
            captured.append(payload)
            return ValidateCypherOutput(errors=["bad cypher"], filters=[])

    class FakeGraph:
        get_schema = (
            "- **CypherQuery**\n"
            "remove me\n"
            "Relationship properties\n"
            "- **Product** {name: STRING}"
        )
        structured_schema = {"node_props": {}}

    result = asyncio.run(
        validate_cypher_query_with_llm(
            FakeChain(),
            question="查产品",
            graph=FakeGraph(),
            cypher_statement="MATCH (p:Product) RETURN p",
        )
    )

    assert result == {"errors": ["bad cypher"], "mapping_errors": []}
    assert captured == [
        {
            "question": "查产品",
            "schema": "Relationship properties\n- **Product** [name: STRING]",
            "cypher": "MATCH (p:Product) RETURN p",
        }
    ]


def test_validate_cypher_query_with_schema_checks_enums_and_ranges() -> None:
    class FakeGraph:
        get_structured_schema = _build_schema().model_dump()

    errors = validate_cypher_query_with_schema(
        FakeGraph(),
        """
        MATCH (u:User)-[r:PURCHASED {channel: "store"}]->(p:Product)
        WHERE p.price > 20000
        RETURN p
        """,
    )

    assert errors == [
        "Node Product has property price = 20000 which is out of range 0.0 to 9999.0 in graph database.",
        "Relationship ['PURCHASED'] with property channel = store not found in graph database.",
    ]


def test_schema_relationships_keep_plain_mapping_shape() -> None:
    schema = _build_schema()

    assert schema.relationships == [
        {"start": "User", "type": "PURCHASED", "end": "Product"}
    ]
