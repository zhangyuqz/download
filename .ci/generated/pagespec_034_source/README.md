# PageSpec Offline HTML Renderer 0.3.4

**English** | [Simplified Chinese](readme/README_zh_Hans.md)

Compiles a closed PageSpec v1 JSON document into one self-contained offline HTML file. The caller describes content and data; the plugin exclusively authors HTML, CSS, JavaScript, library loading order, CSP, and resource embedding.

There is no arbitrary HTML/CSS/JavaScript block, URL field, library selector, or ECharts `option` escape hatch. This is a compiler for a finite page language, not an HTML rewriter.

## Reliability model

The pipeline is transactional:

1. A bounded transport parser accepts canonical JSON without modification, then recovers common Dify/Jinja/Python representations, wrappers, escaped strings, fences, comments, punctuation variants, duplicate keys, and bounded truncation. If several meanings remain plausible, it deterministically selects the highest-scoring PageSpec candidate and records the alternatives, choice, reason, and confidence. It never evaluates input as code.
2. The canonicaliser applies documented aliases and safe scalar conversions. A semantic validator checks cross-field rules such as table width, graph endpoints, real dates, chart shape, slot range, resource budgets, Unicode, and safe length/colour/width grammars.
3. Missing, conflicting, or ambiguous fields are resolved by deterministic defaults, scoring, padding, merging, or clipping and recorded as warnings. Unknown/empty blocks remain visible as explanatory content. A block becomes an error card or explicit `fallback` only at a safety boundary (for example forbidden Mermaid URL/click syntax); all other blocks continue.
4. All dynamic values embedded into trusted scripts use an HTML-script-safe JSON encoder. Raw Markdown HTML is displayed as text. A unique CSP nonce authorises only compiler/vendor scripts; network connections are denied.
5. Before delivery, an independent final-output parser requires exactly one first-in-head CSP, nonce-bearing executable scripts, a closed document, no event attributes, no forbidden embedding tags, and no non-inline resource attributes. A failing transaction produces a small explanation HTML instead of a partial page.

The generated file contains a machine-readable static report and a runtime-error report. The visible table expands at most 2,000 rows, while the embedded JSON retains every decision with an id and JSON Pointer. No fixed badge or overlay is added.

## Canonical input

```json
{
  "version": 1,
  "doc": {"title": "Quarterly report", "theme": "dark", "toc": true},
  "blocks": [
    {"type": "heading", "text": "Revenue", "level": 1},
    {"type": "chart", "kind": "bar", "categories": ["Q1", "Q2"],
     "series": [{"name": "USD m", "data": [12, 18]}]}
  ]
}
```

`pagespec.schema.json` is the strict producer contract. The runtime compatibility layer is intentionally broader, but every normalization or guess is visible in the report. The 28 user content block types are:

`heading`, `text`, `markdown`, `callout`, `quote`, `kv`, `tags`, `code`, `formula`, `divider`, `stat_row`, `table`, `chart`, `wordcloud`, `graph`, `mermaid`, `timeline`, `progress`, `calendar`, `image`, `gallery`, `qrcode`, `barcode`, `section`, `card`, `columns`, `tabs`, and `collapse`.

Unknown future blocks may include `"fallback":"plain explanation"`; an older renderer shows that text and records the downgrade.

The 29th type, `catalog_demo`, is a fixed release-verification block. It accepts only a volume number from 1 to 4 and runs plugin-owned, immutable meaningful demos for the complete 172-library catalogue. It is not a library selector or code escape hatch.

## Tool parameters

| Parameter | Type | Meaning |
|---|---|---|
| `spec` | string, required | Complete PageSpec v1 JSON text. |
| `filename` | string, optional | Output name. `doc.filename` is used when the tool-level name is absent/default. |
| `slot1` … `slot20` | file, optional | Uploaded images referenced only by numeric `image`/`gallery` slot fields. |

Invalid or missing uploaded images become labelled placeholders and are reported inside the file and in the Dify text result.

## Limits

- Input: 2,000,000 UTF-8 bytes; 800 blocks; six container levels.
- Tables: 3,000 rows, 50 columns, 50,000 cells.
- Charts: 20,000 points; graphs: 2,000 nodes/4,000 edges.
- Images: 20 MiB aggregate raw input; final file warns above 26 MiB and rolls back above 28 MiB.
- Browser release targets: Chromium 109 and current Chromium.
- Historical input compatibility: Dify Template/Code/LLM/native/API envelope forms from the researched 0.6–1.16/current serialization families.
- Declared target for this variant: Dify 1.7.1, Python 3.12, and `dify_plugin>=0.9.0`. Release acceptance additionally installs SDK 0.9.1 exactly and exercises configuration loading, registration, `main.py` stdio startup, and invocation in the real SDK harness. Installation on the user's private Dify deployment remains a separate deployment acceptance step and is not falsely reported as locally completed.

The compiler guarantees structural validity, visible handling, and an offline resource boundary for its closed language. It does not promise that user-supplied data is factually correct or that the chosen layout matches every aesthetic preference. Exact installation on a private Dify deployment remains a deployment acceptance step.

## Architecture sources

The implementation borrows established patterns rather than embedding their full runtimes: block/renderer registries (Editor.js), structured content without stored HTML (Portable Text), version/fallback negotiation (Adaptive Cards), declarative chart data (Vega-Lite/ECharts), and target-environment compilation gates (MJML). None of those projects alone supplies this specific Dify + Python + offline single-file report runtime.

## Packaging

The release package declares exactly `dify_plugin>=0.9.0`, without an equality pin or upper bound. The release gate itself installs SDK 0.9.1 exactly so its evidence is reproducible. Bundled browser assets are versioned and SHA-256 checked against `vendor/vendor_map.json`; SBOM and licence records are delivered with the complete package. Local-package signature policy is controlled by the Dify administrator.

```bash
dify plugin package ./html_offline_exporter_PageSpec_0.3.4_Dify1.7.1
```
