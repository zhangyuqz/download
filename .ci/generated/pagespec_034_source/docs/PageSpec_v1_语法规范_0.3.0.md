# PageSpec v1 语法规范（0.3.4 实现）

PageSpec 是一个封闭 JSON 页面语言。用户写“要什么内容”，插件独家生成 HTML、CSS、JavaScript、库顺序、CSP 和资源内联。标准语言没有 `html`、`css`、`javascript`、URL、任意图表 option 或库选择字段。

## 1. 最小输入

```json
{"version":1,"blocks":[{"type":"text","text":"你好"}]}
```

顶层只允许：

| 字段 | 必填 | 写法 |
|---|---:|---|
| `version` | 是 | 整数 `1` |
| `blocks` | 是 | 至少一个块的数组 |
| `doc` | 否 | 文档外观和文件名对象 |

`doc` 可用字段：`title`、`filename`、`theme`（`dark`/`light`）、`accent`（`#RGB`/`#RRGGBB`）、`toc`、`header`、`footer`。`header` 只允许 `title`、`subtitle`、`badges`。

严格机器契约见根目录 `pagespec.schema.json`。通过该 Schema 的标准输入应当零归一、零警告、零降级；跨字段语义再由运行时检查。

## 2. 29 种块（28 种内容块 + 1 种固定验证块）

所有块都可选 `fallback` 字符串。旧插件遇到未来新块或当前块无法识别时，显示 fallback 并记录降级。

| `type` | 必填字段 | 主要可选字段 |
|---|---|---|
| `heading` | `text` | `level` 1–4 |
| `text` | `text`（字符串或字符串数组） | — |
| `markdown` | `text` | 原始 HTML 只显示为文字 |
| `callout` | `text` | `title`、`style`: info/success/warning/danger |
| `quote` | `text` | `source` |
| `kv` | `items:[{label,value}]` | `columns` 1–4 |
| `tags` | `items:[string]` | — |
| `code` | `code` | `language` |
| `formula` | `latex` | `display` |
| `divider` | — | — |
| `stat_row` | `items:[{label,value}]` | item 可含 `unit`、`delta` |
| `table` | `columns`、`rows` | `features`: search/sort |
| `chart` | `kind`；除 sankey 外还需 `series` | 见下一节 |
| `wordcloud` | `items:[{text,weight}]` | — |
| `graph` | `nodes:[{id}]` | `edges`、`layout`、`height` |
| `mermaid` | `code` | 禁配置指令、click/href、URL |
| `timeline` | `items:[{time,title}]` | item 可含 `desc` |
| `progress` | `items:[{label,value}]` | item 可含正数 `max`，且 value≤max |
| `calendar` | `events:[{date,title}]` | `initial_date`，日期为真实 YYYY-MM-DD |
| `image` | `slot` 1–20 | `caption`、`zoom`、安全 `width` |
| `gallery` | `slots` 1–20 | `captions` |
| `qrcode` | `text` | `caption`、`size` 96–512 |
| `barcode` | `text` | `format`（默认 CODE128） |
| `section` | `title`、`blocks` | — |
| `card` | `blocks` | `title` |
| `columns` | `blocks`（每栏一个块数组） | `ratio` 与栏数一致 |
| `tabs` | `items:[{label,blocks}]` | — |
| `collapse` | `items:[{label,blocks}]` | item 可含 `open` |
| `catalog_demo` | `volume`（1–4） | 只用于固定 172 库发布验证，不能传库名、代码、HTML/CSS/JS 或 option |

完整、可运行示例见 `examples/pagespec_complete.json`。

## 3. 图表写法

可用 `kind`：`bar`、`line`、`area`、`pie`、`donut`、`scatter`、`radar`、`gauge`、`funnel`、`heatmap`、`sankey`。

普通分类图：

```json
{"type":"chart","kind":"bar","categories":["甲","乙"],
 "series":[{"name":"数值","data":[12,18]}]}
```

- bar/line/area/radar：每个 series.data 与 categories 等长。
- pie/donut/funnel：数据可为数字（配 categories）或 `{name,value}`。
- scatter：每点为 `[x,y]`。
- heatmap：每点为 `[x索引,y索引,值]`，同时提供 `categories` 与 `y_categories`。
- gauge：第一个 series.data 恰好一个数字。
- sankey：不用 series，改为 `nodes:[{name}]` 与 `links:[{source,target,value}]`；端点必须存在。

用户不能传 ECharts option，因此不能绕过资源和样式边界。需要新图表能力时，应新增受控字段并发布新 Schema，而不是增加逃逸口。

## 4. 高容错规则

标准 JSON 永远优先直通。随后尽量自动转换，并逐项留痕；即使有歧义也不拒绝，而是按固定评分选择最可能结果：

| 可转换 | 示例 |
|---|---|
| JSON 围栏 | `````json … ````` |
| 已知或未知包装 | `{"data":{PageSpec}}`、`{"answer":"…"}`、`{"custom":{"output":{PageSpec}}}`；有界递归寻找候选 |
| 结构化输出 | 同时有 `structured_output` 和展示文本时，优先选择可识别的结构化 PageSpec |
| 单元素数组包装 | `[PageSpec]`、`["{\"version\":1,…}"]` |
| 完整 JSON 字符串包装 | `"{\"version\":1,…}"` |
| 多重转义 | JSON 字符串再套 JSON 字符串，或模板剥掉外引号后留下反斜杠；最多八层 |
| 多重 HTML 实体 | `&amp;quot;` 等最多八层逐层恢复；严格 JSON 正文里的实体保持原文 |
| 尾逗号 | 对象/数组最后一个逗号 |
| 受限 Python/Jinja 字面量 | tuple、单引号、True/False/None，以及单引号与小写 true/false/null 混合；禁止表达式和调用 |
| 裸 key / 裸字符串值 | `title: 月报` → `"title":"月报"` |
| 字符串破损 | 字符串中的原始换行、Windows 路径/正则中的无效反斜杠按上下文转义 |
| 原生传输对象 | dict/list/tuple/bytes/bytearray/memoryview；嵌套 bytes 与非字符串标量 key 递归归一 |
| 字节编码 | 识别 UTF-32/UTF-16/UTF-8 BOM；再尝试 UTF-8、GB18030 与替换解码 |
| 重复键 | 固定采用最后一个值，并记录全部候选与选择 |
| 中英文字段别名 | 例如 `版本`→`version`、`类型`→`type` |
| 明确标量转换 | `"2"`→数字 2、`"true"`→布尔 true |
| 安全宽度 | `"480"`→`"480px"`（因此它不是严格标准写法） |

还支持 `output`、`outputs.output`、`data.outputs.output`、`structured_output`、`answer`、`message`、`message.content`、`result`、`choices[0].message.content` 等 Dify/模型包装，以及未知同构信封；外层元数据里的 `version`/`type` 不会抢走真正 PageSpec。候选按载荷质量、PageSpec 根结构、blocks、版本、已知块数量、修复次数、包装深度和出现顺序评分；并列也有固定次序。NaN/Infinity 归一为 null 或字段默认值并留痕。大小、深度或内容完全不可恢复时仍输出有效说明页。

字段语义有歧义时也采用确定性猜测，而不是拒绝：数字字段中 `"1,2"` 按小数 `1.2`，`"1,234"` 按千位分组 `1234`；表格中数值键 `1` 与字符串键 `"1"` 冲突时保留并重命名后者，短行补空、长行截断；枚举先用别名和相似度，线索仍不足就采用该字段固定默认值；布尔值同样采用固定默认值；越界数字稳定裁剪。每次猜测都记录位置、原值、候选、选择、原因与置信度。

单块字段错误、未知块或跨字段不一致不拖垮整页：先按别名、近似拼写、字段形状、固定默认值、补齐或裁剪生成可用结果；未知/空块至少变成可见提示。只有 Mermaid URL/click/HTML 等安全逃逸或硬安全边界才原位错误卡/fallback，其余块继续。未知字段不会被执行，而是删除并写入 **WARN**。

## 5. 警告在哪里

每个输出文件有三种可核验记录：

1. HTML 源码中的静态计数注释；
2. 页尾折叠报告（正常布局流，不是悬浮遮挡层）；
3. `#__ofx-report-data` 与 `#__ofx-runtime-data` 两个 JSON 数据节点。

运行期组件失败会原位变为错误卡，并同步追加到运行报告与报告标题计数。Dify 节点文本输出也列出静态归一/警告/降级。可见表格最多展开前 2,000 条以免撑坏页面，但 `#__ofx-report-data` 保存**全部**记录；每条带连续 `id` 和 JSON Pointer，不以“省略若干条”代替具体路径。

## 6. 断网与安全边界

- 所有脚本中的用户数据均用可阻断 `</script>` 的 JSON 编码。
- Markdown 先把 `<>&` 变成文字，再经 marked 和显式 DOMPurify 白名单。
- CSP 使用每文件随机 nonce；无 `unsafe-inline` script；`connect-src 'none'`。
- 图片只能来自 slot1–slot20，经真实字节签名和结构校验；错误图片使用带说明占位图。
- 交付前终审器拒绝无 nonce 可执行脚本、事件属性、base/iframe/object/embed/link、外部资源属性、非唯一/非首位 CSP 或不闭合文档。
- 终审、UTF-8 编码或 28 MiB 体积门禁失败时，生成事务回滚，只输出说明页。

## 7. 资源限制

输入 2,000,000 UTF-8 字节；800 块；六层容器；表格 3,000 行/50 列/50,000 单元格；图表 20,000 点；关系图 2,000 节点/4,000 边；图片原始总量 20 MiB；最终文件 26 MiB 警告、28 MiB 回滚。

## 8. 可兑现的保证

在封闭 PageSpec 内，可以对“结构有效、断网资源边界、无静默处理、版本/库由插件固定、局部失败不破坏整页”建立构造式保证和发布测试。不能承诺用户数据事实正确，也不能承诺排版审美完全符合个人偏好；这正对应 DOCX 插件仍可能出现的格式排版差异。
