# Dify 输入传输兼容矩阵（PageSpec 0.3.4）

## 结论

插件不把截图中的转义文本当成“Dify 1.7.1 独有协议”。Dify 的模板转换节点长期把渲染结果放在名为 `output` 的字符串变量中；运行结果面板再把整个 outputs 对象做 JSON 展示，于是界面会看到 `{"output":"{\n ...}"}`。真正需要兼容的是变量类型、节点之间的字符串化、UI/API 外层信封、Jinja/Python 表示，以及模型生成的非标准 JSON。

“兼容全部 Dify 版本”在工程上按以下可验证边界执行：逐 tag 审计 Dify `0.6.0`–`1.16.0` 的 99 个 stable tags，并覆盖其中可证实的传输家族；同时对同构的未知/未来包装做通用有界解包。尚未发布且改变语义的新协议不能凭空承诺，出现时必须加入此矩阵和回归测试。

## 已覆盖的输入家族

| 家族 | 典型输入 | 处理 |
|---|---|---|
| 原生变量 | Python `dict`/`list`/`tuple`/字符串/bytes/bytearray/memoryview/None | 递归归一；tuple 转 list；非字符串标量 key 确定性转字符串；根部和嵌套 bytes 均按 BOM（UTF-32/UTF-16/UTF-8）、UTF-8、GB18030、替换解码的顺序处理 |
| 模板节点字符串 | `{"version":1,"blocks":[...]}` | 标准 JSON 零改动直通 |
| 运行面板截图形式 | `{"output":"{\n  \"version\":1...}"}` | 解 `output`，再解完整 JSON 字符串 |
| 嵌套节点/API 信封 | `structured_output`、`outputs.output`、`data.outputs.output`、`data.output`、`result.output`、`answer`、`message`、`message.content`、`choices[0].message.content` | 固定路径与结构质量共同评分；结构化 PageSpec 优先于同信封中的展示文本；冲突时选定并留痕 |
| 通用/未知信封 | `spec`、`pagespec`、`payload`、`body`、`content`、`text`、`value`，以及任意未知对象/数组外壳 | 有界递归寻找 PageSpec 候选；元数据里的 `version`/`type` 不会被误判为页面根；单元素数组自动剥壳 |
| 多重字符串化 | JSON 字符串内再放 JSON 字符串，或模板剥掉外引号后留下反斜杠层 | 最多八层，逐层解码并记录 |
| 多重 HTML 实体 | `&amp;quot;` 等反复编码 | 最多八层，逐层解码；严格 JSON 正文中的实体不改写 |
| Python/Jinja repr | 单引号、tuple、True/False/None，以及单引号与小写 true/false/null 混合 | 先做受控常量归一，再只用 AST 白名单读取字面量；禁止调用、属性和表达式，绝不 `eval` |
| LLM 常见输出 | Markdown 围栏、说明文字夹 JSON、JSONC 注释、尾逗号 | 提取候选并有界修复 |
| 中文输入变体 | 中文字段别名、智能引号、全角括号/逗号/冒号 | 归一并逐项留痕 |
| 常见破损 | 裸 key、裸字符串值、字符串内原始换行/制表、Windows 路径或正则中的无效反斜杠、明显缺逗号、缺少不超过八个闭合符、NUL/BOM | 仅在字符串/值边界内做有界修复后继续；每步记录修复理由 |
| 重复键/非有限数 | 重复字段、NaN、Infinity | 重复键 last-wins；非有限数归一为 null；候选与选择可见 |
| 多个候选 | 一段文本内出现多份 JSON | 按根结构、版本、已知块、修复数、包装深度、出现顺序评分；永不因歧义拒绝 |
| 纯正文 | 完全不是 JSON | 猜测为 `text` 块，仍生成有效页面 |

命名包装冲突首先比较载荷质量：可直接识别的 `structured_output` PageSpec 优先于同一信封中的普通 `text`/`output` 展示字符串；其后才按命名路径、根结构评分、包装深度和出现顺序确定唯一结果。外层即使含 `version`、`type`、`doc` 或 `profile` 元数据，也会与深层候选一起评分；未知路径让位给深层 PageSpec 必须写 WARN。任何非零改动都会把位置、原值/候选、最终选择、原因和置信度写入文件内审计。

## 语义歧义的确定性猜测

传输层成功后，字段归一同样遵守“尽量处理，不因歧义拒绝”的规则：

- 数字字符串 `"1,2"` 在数字字段中解释为小数 `1.2`；符合三位分组的 `"1,234"` 解释为 `1234`。候选、选择、原因和置信度全部留痕。
- 表格同时出现数字键 `1` 与字符串键 `"1"` 时不静默覆盖：保留第一个列名，后续冲突列确定性重命名为 `1__2`、`1__3`；对象行按原始键精确匹配，无法精确匹配时再按列顺序 first-wins，并记录映射。
- 表格数组行短于列数时补空字符串，长于列数时截断；页面继续生成并显示警告。
- 枚举值先走显式别名，再按固定评分猜最接近的合法值；即使相似度很低也采用字段固定默认值或稳定排序首项，并列候选不交给运行时随机决定。布尔、数字和跨字段数量不一致同样采用固定默认、补齐、合并或裁剪并 WARN。

## 硬边界

只有资源或安全边界可以停止内容页：输入超过 2,000,000 UTF-8 字节、包装或字符串化超过八层、树深/节点量超过硬上限。此时仍返回结构有效的说明 HTML，而不是损坏文件。语法歧义本身不是拒绝条件。

## 回归证据

- 专项矩阵覆盖截图双层字符串、结构化/文本冲突、任意递归包装、单元素数组、多层转义、UTF-16/UTF-32/UTF-8 BOM、GB18030/NUL、原始换行、无效反斜杠、裸值、混合 Python/JSON 字面量、tuple/嵌套 bytes/非字符串 key、JSONC、截断、重复键、非有限数、多个候选与类型/字段猜测。
- 固定种子 fuzz 10,000 份：10,000 份均生成有效页面，未捕获异常 0。
- 最终安装包使用真实 `dify_plugin==0.9.1` 完成 SDK 配置加载、provider/tool 注册、`main.py` stdio 启动、工具消息与 HTML blob 烟测；发布依赖声明保持审核要求的 `dify_plugin>=0.9.0`。

## 官方源码依据

- Dify 1.7.1 Template Transform 节点：`https://github.com/langgenius/dify/blob/1.7.1/api/core/workflow/nodes/template_transform/template_transform_node.py`
- Dify 1.7.1 运行结果面板：`https://github.com/langgenius/dify/blob/1.7.1/web/app/components/workflow/run/result-panel.tsx`
- 当前 Graphon Template Transform：`https://github.com/langgenius/graphon/blob/main/src/graphon/nodes/template_transform/template_transform_node.py`
- Dify 1.7.1 环境长度配置：`https://github.com/langgenius/dify/blob/1.7.1/docker/.env.example`
