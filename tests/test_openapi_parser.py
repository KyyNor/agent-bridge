from __future__ import annotations

from agent_bridge.capability_hub.models import ToolType
from agent_bridge.capability_hub.sources.openapi.parser import parse_openapi_operations


def test_parse_openapi_operations_builds_candidate_tools_from_yaml() -> None:
    candidates = parse_openapi_operations(
        """
        openapi: 3.0.0
        info:
          title: Petstore
          version: "1"
        paths:
          /pets:
            get:
              operationId: listPets
              summary: List pets
              parameters:
                - name: limit
                  in: query
                  schema:
                    type: integer
          /pets/{petId}:
            get:
              summary: Get pet
              parameters:
                - name: petId
                  in: path
                  required: true
                  schema:
                    type: string
            post:
              operationId: updatePet
              summary: Update pet
              requestBody:
                content:
                  application/json:
                    schema:
                      type: object
                      properties:
                        name:
                          type: string
        """
    )

    by_tool = {item["tool_name"]: item for item in candidates}
    assert by_tool["list_pets"]["tool_type"] == ToolType.search.value
    assert by_tool["list_pets"]["input_schema"]["properties"]["limit"]["type"] == "integer"
    assert by_tool["get_pets_pet_id"]["tool_type"] == ToolType.detail.value
    assert by_tool["get_pets_pet_id"]["request_mapping"]["path"] == {"petId": "petId"}
    assert by_tool["update_pet"]["tool_type"] == ToolType.unconfigured.value
    assert "body" in by_tool["update_pet"]["input_schema"]["properties"]


def test_parse_openapi_operations_deduplicates_tool_names() -> None:
    candidates = parse_openapi_operations(
        {
            "openapi": "3.0.0",
            "paths": {
                "/users": {"get": {"operationId": "lookup"}},
                "/teams": {"get": {"operationId": "lookup"}},
            },
        }
    )

    names = [item["tool_name"] for item in candidates]
    assert names[0] == "lookup"
    assert names[1].startswith("lookup_")
    assert len(set(names)) == 2
