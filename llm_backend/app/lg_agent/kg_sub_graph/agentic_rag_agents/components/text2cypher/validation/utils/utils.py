from ..models import CypherValidationTask, Neo4jStructuredSchema
from typing import List, Literal


def update_task_list_with_property_type(
    tasks: List[CypherValidationTask],
    structure_graph_schema: Neo4jStructuredSchema,
    node_or_rel: Literal["node", "rel"],
) -> List[CypherValidationTask]:
    """为任务列表中的每个条目分配 Neo4j 属性类型。"""
    if node_or_rel == "node":
        schema = structure_graph_schema.node_props
    else:
        schema = structure_graph_schema.rel_props

    for task in tasks:
        labels_or_types = task.parsed_labels_or_types
        found_types = set()
        for lt in labels_or_types:
            name_type_map = {d.property: d.type for d in schema.get(lt, list())}
            found_types.add(name_type_map.get(task.property_name))

        if len(found_types) > 1:
            pass
        elif not len(found_types):
            pass

        if len(found_types) > 0:
            t = list(found_types)[0]
            task.property_type = t

    return tasks
