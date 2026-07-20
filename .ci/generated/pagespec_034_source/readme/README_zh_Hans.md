# PageSpec 断网 HTML 生成器 0.3.4

[English](../README.md) | **简体中文**

输入是封闭的 PageSpec v1 JSON，输出是一个自包含断网 HTML。用户只描述内容和数据；HTML、CSS、JavaScript、库加载顺序、CSP 与资源内联全部由插件独家生成。

本工具没有任意 HTML/CSS/JavaScript 块，没有网址字段、库选择器，也没有 ECharts `option` 逃逸口。它是有限页面语言的编译器，不再解析和修改用户 HTML。

## 可靠性模型

整条流水线按事务处理：

1. 有界传输解析器让标准 JSON 零改动直通；随后兼容 Dify/Jinja/Python 常见包装、转义字符串、代码围栏、注释、标点变体、重复键和有界截断。若仍有多个含义，就按固定评分选择最可能的 PageSpec，并记录原文、候选、选中结果、理由与置信度；绝不把输入当代码执行。
2. 规范化层只做有明确规则的别名和标量转换；语义层再检查表格行列、关系图端点、真实日期、图表数据形状、插槽范围、资源预算、Unicode，以及颜色/宽度等安全语法。
3. 缺字段、字段冲突或语义模糊时，按固定评分、默认值、补齐、合并或裁剪继续生成并写 WARN；未知/空块至少变成可见提示。只有 Mermaid URL/click 等安全逃逸或硬安全边界才原位错误卡/fallback，其他块继续。
4. 所有进入可信脚本的数据都经过可阻断 `</script>` 的 JSON 编码。Markdown 原始 HTML 只当文字显示。每个文件使用随机 CSP nonce，只授权插件与内置库脚本，并禁止网络连接。
5. 交付前由独立 HTML 终审器再次检查：CSP 必须唯一且位于 head 第一项、可执行脚本必须有正确 nonce、文档必须闭合、不得有事件属性/嵌入标签/非内联资源。失败就回滚成小型说明页，绝不交付半成品。

文件内含机器可读静态报告和运行期错误报告。可见表格最多展开 2,000 条，但内嵌 JSON 保留每一条决定的 id 和 JSON Pointer，不会只写“省略若干条”。不添加右下角浮标或遮挡页面的固定层。

## 最小写法

```json
{
  "version": 1,
  "doc": {"title": "季度报告", "theme": "dark", "toc": true},
  "blocks": [
    {"type": "heading", "text": "营收", "level": 1},
    {"type": "chart", "kind": "bar", "categories": ["一季度", "二季度"],
     "series": [{"name": "百万元", "data": [12, 18]}]}
  ]
}
```

`pagespec.schema.json` 是严格的标准写法契约。运行时容错层比 Schema 更宽，但任何归一或猜测都会写入报告。28 种用户内容块为：

`heading`、`text`、`markdown`、`callout`、`quote`、`kv`、`tags`、`code`、`formula`、`divider`、`stat_row`、`table`、`chart`、`wordcloud`、`graph`、`mermaid`、`timeline`、`progress`、`calendar`、`image`、`gallery`、`qrcode`、`barcode`、`section`、`card`、`columns`、`tabs`、`collapse`。

第 29 种 `catalog_demo` 是固定发布验证块，只接收 1–4 的卷号，用插件自带且不可由用户改写的语料验证完整 172 库；它不是库选择器，也不是代码逃逸口。

未来版本的新块可附 `"fallback":"一段普通文字"`；旧版插件遇到未知块时优先按别名、拼写与字段形状猜测，仍无法安全构造时才显示 fallback 并记录降级。

## 参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `spec` | string，必填 | 完整 PageSpec v1 JSON 文本。 |
| `filename` | string，选填 | 输出名；工具层未指定或仍为默认名时采用 `doc.filename`。 |
| `slot1` … `slot20` | file，选填 | 图片只能由 `image`/`gallery` 的数字插槽字段引用。 |

无效或缺失图片会变为带标签占位图，并同时写进文件内报告和 Dify 文本结果。

## 硬限制

- 输入不超过 2,000,000 UTF-8 字节、800 个块、六层容器。
- 表格最多 3,000 行、50 列、50,000 单元格。
- 单图表最多 20,000 点；关系图最多 2,000 节点/4,000 边。
- 图片原始总量最多 20 MiB；最终文件超过 26 MiB 警告，超过 28 MiB 回滚。
- 浏览器发布目标：Chromium 109 与当前 Chromium。
- 输入解析兼容：已研究 Dify 0.6–1.16/current 的模板、代码、LLM、原生变量与 API 包装序列化形态。
- 声明目标：本变体面向 Dify 1.7.1、Python 3.12，依赖声明严格写为 `dify_plugin>=0.9.0`。发布验收环境精确安装 SDK 0.9.1，并在真实 SDK harness 中检查配置加载、注册、`main.py` stdio 启动和调用；用户私有 Dify 上的真实安装仍是单独部署验收，不冒充本地已经完成。

插件能对封闭语言承诺：HTML 结构有效、所有无法处理项可见、资源边界断网。它不能保证用户数据事实正确，也不能保证排版审美必然符合个人偏好；在用户私有 Dify 上真实安装仍属于部署验收。

## 复用而非照搬

本实现借用了成熟模式：Editor.js 的块/渲染器注册表、Portable Text 的结构化内容与“不存 HTML”、Adaptive Cards 的版本/fallback、Vega-Lite/ECharts 的声明式图表数据、MJML 的目标环境编译门禁。上述任何一个项目都不能原样覆盖 Dify + Python + 断网单文件交互报告，因此只复用模式和成熟渲染部件，不把不合适的整套运行时硬塞进插件。

## 打包与供应链

插件依赖声明只允许精确一行 `dify_plugin>=0.9.0`，不写等号锁定和上限；发布门禁使用精确 SDK 0.9.1 环境生成可复核证据。内置浏览器资源按 `vendor/vendor_map.json` 的精确版本、大小和 SHA-256 校验；完整交付包附 SBOM、许可证和测试证据。Dify 本地安装包是否要求签名由管理员策略决定。
