#!/usr/bin/env python3
"""Generate the strict, canonical PageSpec v1 JSON Schema.

The runtime also has a tolerant transport/normalisation layer. This schema is
deliberately stricter: a producer that wants zero normalisation warnings should
emit documents accepted here. Cross-field semantics that JSON Schema cannot
express are enforced by tools/pagespec_validate.py and release tests.
"""
from __future__ import annotations

import json
from pathlib import Path


S = {"type": "string"}
NONEMPTY = {"type": "string", "minLength": 1, "pattern": r"\S"}
FALLBACK = {"type": "string", "maxLength": 2000}
NUMBER = {"type": "number"}
SCALAR = {"type": ["string", "number", "boolean", "null"]}


def obj(properties, required=(), *, additional=False):
    value = {"type": "object", "additionalProperties": additional,
             "properties": properties}
    if required:
        value["required"] = list(required)
    return value


def arr(items, *, minimum=0, maximum=None):
    value = {"type": "array", "items": items}
    if minimum:
        value["minItems"] = minimum
    if maximum is not None:
        value["maxItems"] = maximum
    return value


def block(name, properties=None, required=()):
    fields = {"type": {"const": name}, "fallback": FALLBACK}
    fields.update(properties or {})
    return obj(fields, ("type", *required))


kv_item = obj({"label": {**NONEMPTY, "maxLength": 500},
               "value": {**S, "maxLength": 20000}}, ("label", "value"))
stat_item = obj({"label": {**NONEMPTY, "maxLength": 500},
                 "value": {**S, "maxLength": 2000},
                 "unit": {**S, "maxLength": 100},
                 "delta": {**S, "maxLength": 100}}, ("label", "value"))
progress_item = obj({"label": {**NONEMPTY, "maxLength": 500},
                     "value": {"type": "number", "minimum": 0},
                     "max": {"type": "number", "exclusiveMinimum": 0}},
                    ("label", "value"))
timeline_item = obj({"time": {**NONEMPTY, "maxLength": 500},
                     "title": {**NONEMPTY, "maxLength": 1000},
                     "desc": {**S, "maxLength": 20000}}, ("time", "title"))

column = {
    "oneOf": [
        {**NONEMPTY, "maxLength": 500},
        obj({"label": {**NONEMPTY, "maxLength": 500},
             "key": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
             "align": {"enum": ["left", "center", "right"]}}, ("label",)),
    ]
}
table_row = {
    "oneOf": [
        arr(SCALAR, maximum=50),
        {"type": "object", "additionalProperties": SCALAR},
    ]
}

chart_point = {
    "oneOf": [
        NUMBER,
        arr(NUMBER, minimum=2, maximum=3),
        obj({"name": {**NONEMPTY, "maxLength": 500}, "value": NUMBER},
            ("name", "value")),
    ]
}
chart_series = obj({"name": {**S, "maxLength": 500},
                    "data": arr(chart_point, minimum=1, maximum=20000)}, ("data",))
sankey_node = obj({"name": {**NONEMPTY, "maxLength": 500}}, ("name",))
sankey_link = obj({"source": {**NONEMPTY, "maxLength": 500},
                   "target": {**NONEMPTY, "maxLength": 500},
                   "value": {"type": "number", "minimum": 0}},
                  ("source", "target", "value"))

graph_node = obj({"id": {**NONEMPTY, "maxLength": 500},
                  "label": {**S, "maxLength": 500},
                  "group": {**S, "maxLength": 200}}, ("id",))
graph_edge = obj({"from": {**NONEMPTY, "maxLength": 500},
                  "to": {**NONEMPTY, "maxLength": 500},
                  "label": {**S, "maxLength": 500}}, ("from", "to"))

event = obj({"date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
             "title": {**NONEMPTY, "maxLength": 1000}}, ("date", "title"))

definitions = {
    "heading": block("heading", {"text": {**NONEMPTY, "maxLength": 200000},
                                  "level": {"type": "integer", "minimum": 1, "maximum": 4}},
                     ("text",)),
    "text": block("text", {"text": {"oneOf": [
        {**NONEMPTY, "maxLength": 200000},
        arr({**NONEMPTY, "maxLength": 5000}, minimum=1, maximum=2000),
    ]}}, ("text",)),
    "markdown": block("markdown", {"text": {**NONEMPTY, "maxLength": 200000}}, ("text",)),
    "callout": block("callout", {"text": {**NONEMPTY, "maxLength": 200000},
                                  "title": {**S, "maxLength": 500},
                                  "style": {"enum": ["info", "success", "warning", "danger"]}},
                     ("text",)),
    "quote": block("quote", {"text": {**NONEMPTY, "maxLength": 200000},
                              "source": {**S, "maxLength": 500}}, ("text",)),
    "kv": block("kv", {"items": arr(kv_item, minimum=1, maximum=2000),
                        "columns": {"type": "integer", "minimum": 1, "maximum": 4}}, ("items",)),
    "tags": block("tags", {"items": arr({**NONEMPTY, "maxLength": 5000}, minimum=1, maximum=2000)}, ("items",)),
    "code": block("code", {"code": {**NONEMPTY, "maxLength": 200000},
                            "language": {**S, "maxLength": 30}}, ("code",)),
    "formula": block("formula", {"latex": {**NONEMPTY, "maxLength": 20000},
                                  "display": {"type": "boolean"}}, ("latex",)),
    "divider": block("divider"),
    "stat_row": block("stat_row", {"items": arr(stat_item, minimum=1, maximum=200)}, ("items",)),
    "table": block("table", {"columns": arr(column, minimum=1, maximum=50),
                              "rows": arr(table_row, maximum=3000),
                              "features": {"type": "array", "uniqueItems": True,
                                           "items": {"enum": ["search", "sort"]}}},
                   ("columns", "rows")),
    "chart": block("chart", {
        "kind": {"enum": ["bar", "line", "area", "pie", "donut", "scatter", "radar",
                           "gauge", "funnel", "heatmap", "sankey"]},
        "title": {**S, "maxLength": 500},
        "categories": arr({**NONEMPTY, "maxLength": 5000}, maximum=20000),
        "y_categories": arr({**NONEMPTY, "maxLength": 5000}, maximum=20000),
        "series": arr(chart_series, minimum=1, maximum=20000),
        "stacked": {"type": "boolean"}, "horizontal": {"type": "boolean"},
        "unit": {**S, "maxLength": 500},
        "height": {"type": "integer", "minimum": 120, "maximum": 800},
        "nodes": arr(sankey_node, minimum=1, maximum=20000),
        "links": arr(sankey_link, minimum=1, maximum=20000),
    }, ("kind",)),
    "wordcloud": block("wordcloud", {"items": arr(obj({
        "text": {**NONEMPTY, "maxLength": 500},
        "weight": {"type": "number", "minimum": 0, "maximum": 10000}}, ("text", "weight")),
        minimum=1, maximum=2000)}, ("items",)),
    "graph": block("graph", {"nodes": arr(graph_node, minimum=1, maximum=2000),
                              "edges": arr(graph_edge, maximum=4000),
                              "layout": {"enum": ["force", "circle", "grid", "dagre"]},
                              "height": {"type": "integer", "minimum": 160, "maximum": 900}},
                   ("nodes",)),
    "mermaid": block("mermaid", {"code": {**NONEMPTY, "maxLength": 20000}}, ("code",)),
    "timeline": block("timeline", {"items": arr(timeline_item, minimum=1, maximum=2000)}, ("items",)),
    "progress": block("progress", {"items": arr(progress_item, minimum=1, maximum=2000)}, ("items",)),
    "calendar": block("calendar", {"events": arr(event, minimum=1, maximum=5000),
                                    "initial_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}},
                       ("events",)),
    "image": block("image", {"slot": {"type": "integer", "minimum": 1, "maximum": 20},
                              "caption": {**S, "maxLength": 2000}, "zoom": {"type": "boolean"},
                              "width": {"type": "string", "pattern": r"^(?:(?:100|[1-9]?\d)(?:%|vw)|[1-9]\d{0,3}(?:px|rem|em))$"}},
                   ("slot",)),
    "gallery": block("gallery", {"slots": {"type": "array", "minItems": 1, "maxItems": 20,
                                                  "uniqueItems": True,
                                                  "items": {"type": "integer", "minimum": 1, "maximum": 20}},
                                  "captions": arr({**S, "maxLength": 2000}, maximum=20)}, ("slots",)),
    "qrcode": block("qrcode", {"text": {**NONEMPTY, "maxLength": 2000},
                                "caption": {**S, "maxLength": 2000},
                                "size": {"type": "integer", "minimum": 96, "maximum": 512}}, ("text",)),
    "barcode": block("barcode", {"text": {**NONEMPTY, "maxLength": 256},
                                  "format": {"enum": ["CODE128", "CODE39", "EAN13", "EAN8", "UPC", "ITF14", "MSI", "PHARMACODE"]}},
                     ("text",)),
}

child_blocks = arr({"$ref": "#/definitions/block"}, minimum=1, maximum=800)
definitions.update({
    "section": block("section", {"title": {**NONEMPTY, "maxLength": 1000}, "blocks": child_blocks},
                     ("title", "blocks")),
    "card": block("card", {"title": {**S, "maxLength": 1000}, "blocks": child_blocks}, ("blocks",)),
    "columns": block("columns", {
        "blocks": arr(child_blocks, minimum=1, maximum=6),
        "ratio": arr({"type": "number", "minimum": 0.01, "maximum": 100}, minimum=1, maximum=6),
    }, ("blocks",)),
    "tabs": block("tabs", {"items": arr(obj({
        "label": {**NONEMPTY, "maxLength": 500}, "blocks": child_blocks}, ("label", "blocks")),
        minimum=1, maximum=50)}, ("items",)),
    "collapse": block("collapse", {"items": arr(obj({
        "label": {**NONEMPTY, "maxLength": 500}, "blocks": child_blocks,
        "open": {"type": "boolean"}}, ("label", "blocks")), minimum=1, maximum=50)}, ("items",)),
    "catalog_demo": block("catalog_demo", {
        "volume": {"type": "integer", "minimum": 1, "maximum": 4},
    }, ("volume",)),
})

definitions["chart"]["allOf"] = [{
    "if": {"properties": {"kind": {"const": "sankey"}}, "required": ["kind"]},
    "then": {"required": ["nodes", "links"]},
    "else": {"required": ["series"]},
}]

block_names = list(definitions)
definitions["block"] = {"oneOf": [{"$ref": f"#/definitions/{name}"} for name in block_names]}

doc = obj({
    "title": {**S, "maxLength": 1000},
    "filename": {**S, "maxLength": 240},
    "theme": {"enum": ["dark", "light"]},
    "accent": {"type": "string", "pattern": r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$"},
    "toc": {"type": "boolean"},
    "header": obj({"title": {**S, "maxLength": 1000},
                   "subtitle": {**S, "maxLength": 2000},
                   "badges": arr({**NONEMPTY, "maxLength": 5000}, maximum=100)}),
    "footer": {**S, "maxLength": 10000},
})

schema = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "https://example.invalid/pagespec/v1/schema.json",
    "title": "PageSpec v1 canonical schema (closed, no HTML/CSS/JavaScript escape hatch)",
    "description": "Strict producer contract. Tolerant aliases and repair forms are intentionally excluded.",
    "type": "object",
    "additionalProperties": False,
    "required": ["version", "blocks"],
    "properties": {
        "version": {"const": 1},
        "profile": {"const": "catalog-verification"},
        "doc": doc,
        "blocks": arr({"$ref": "#/definitions/block"}, minimum=1, maximum=800),
    },
    "allOf": [{
        "if": {
            "properties": {
                "blocks": {"contains": {"$ref": "#/definitions/catalog_demo"}},
            },
            "required": ["blocks"],
        },
        "then": {
            "required": ["profile"],
            "properties": {
                "profile": {"const": "catalog-verification"},
                "blocks": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {"$ref": "#/definitions/catalog_demo"},
                },
            },
        },
        "else": {"not": {"required": ["profile"]}},
    }],
    "definitions": definitions,
}


if __name__ == "__main__":
    path = Path(__file__).with_name("pagespec.schema.json")
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
