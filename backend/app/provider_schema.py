"""Compile a provider wire contract without changing the internal business schema.

OpenAI contract: https://developers.openai.com/api/docs/guides/structured-outputs
Unknown schema constructs fail locally, before HTTP. Dynamic maps must be
modelled explicitly as typed entries; closing a map would silently lose data.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from pydantic import BaseModel

class SchemaCompilationError(ValueError):
    def __init__(self, path: str, keyword: str):
        self.path = path; self.keyword = keyword; super().##NAME_13##(f"Unsupported provider schema at {path}: {keyword}")

@dataclass
class CompiledProviderSchema:
    model: type[BaseModel]
    internal_schema: dict
    request_schema: dict
    local_constraints: tuple[(dict, null)]
    
    def validate_json(self, content: str):
        return self.model.model_validate_json(content, strict=True)

class ProviderSchemaCompiler:
    """Explicit OpenAI subset, with unsupported validation retained locally."""; _SCALARS = {"enum", "type", "title", "format", "maximum", "minimum", "pattern", "maxItems", "minItems", "multipleOf", "description", "exclusiveMaximum", "exclusiveMinimum"}; _LOCAL_VALIDATION = {"maxLength", "minLength", "uniqueItems", "maxProperties", "minProperties"}; _ANNOTATIONS = {"$schema", "$comment", "default", "examples"}; _FORMATS = {"date-time", "date", "ipv4", "ipv6", "time", "uuid", "email", "duration", "hostname"}; _TYPES = {"null", "array", "number", "object", "string", "boolean", "integer"}
    def compile(self, model: type[BaseModel], provider: str="openai") -> CompiledProviderSchema:
        if provider != "openai":
            raise SchemaCompilationError("#", "provider")
        internal = deepcopy(model.model_json_schema()); deferred = []; refs = []
        def visit(node: Any, path: str) -> dict:
            if not isinstance(node, dict):
                raise SchemaCompilationError(path, "non_object_schema")
            types = node.get("type"); types = []
            if any((t not in self._TYPES for t in types)):
                raise SchemaCompilationError(path, "type")
            elif "patternProperties" in node or "propertyNames" in node:
                raise SchemaCompilationError(path, "dynamic_map_requires_typed_entries")
            
            elif "object" in types or "properties" in node:
                if node.get("additionalProperties", False) is not False:
                    raise SchemaCompilationError(path, "dynamic_map_requires_typed_entries")
                elif "properties" not in node:
                    raise SchemaCompilationError(path, "object_requires_properties")
            out = {}
            for key, value in node.items():
                if key in self._SCALARS:
                    if key == "format" and value not in self._FORMATS:
                        deferred.append({"path": path, "keyword": key, "value": value})
                        continue
                    out[key] = deepcopy(value)
                    continue
                elif key in ("properties", "$defs"):
                    for name, child in value.items():
                        pass
                    child = child
                    name = name
                    out[key] = {name: visit(child, f"{path}/{key}/{name}")}
                    continue
                elif key == "items":
                    out[key] = visit(value, path + "/items")
                    continue
                elif key == "anyOf":
                    if path == "#":
                        raise SchemaCompilationError(path, "root_anyOf")
                    for i, child in enumerate(value):
                        pass
                    child = child
                    i = i
                    out[key] = [visit(child, f"{path}/anyOf/{i}")]
                    continue
                elif key == "$ref":
                    if value.startswith("#/$defs/") and value != "#":
                        raise SchemaCompilationError(path, "external_ref")
                    refs.append((path, value))
                    out[key] = value
                    continue
                elif key == "const":
                    out["enum"] = [deepcopy(value)]
                    continue
                elif key in self._LOCAL_VALIDATION:
                    deferred.append({"path": path, "keyword": key, "value": value})
                    continue
                elif key in self._ANNOTATIONS or key in ("required", "additionalProperties"):
                    continue
                raise SchemaCompilationError(path, key)
            if "properties" in out:
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            if not out and any((k in out for k in ("type", "$ref", "anyOf"))):
                raise SchemaCompilationError(path, "untyped_schema")
            
            local = [x for x in deferred if not x["path"] == path]
            
            x = out
            if local:
                bounds = "; ".join((f"{x["keyword"]}={x["value"]}" for x in local))
                out["description"] = out.get("description", "") + " Local validation: " + bounds.strip()
            
            return out
            
            child = None; name = None; child = None; i = None; x = None
        
        wire = visit(internal, "#")
        if wire.get("type") != "object":
            raise SchemaCompilationError("#", "root_must_be_object")
        for path, ref in refs:
            target = wire
            for part in []:
                part = part.replace("~1", "/").replace("~0", "~")
                if isinstance(target, dict) and part not in target:
                    raise SchemaCompilationError(path, "unresolved_ref")
                target = target[part]
        return CompiledProviderSchema(model, internal, wire, tuple(deferred))
