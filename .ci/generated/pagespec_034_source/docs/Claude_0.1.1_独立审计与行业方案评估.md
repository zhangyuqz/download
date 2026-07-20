# Claude PageSpec 0.1.1 独立审计与行业方案评估

## 结论

Claude 对“自由 HTML → 封闭 JSON 块语言 → 宿主渲染器”的架构方向判断正确；这一方向确实能从结构上消灭此前最大的一类无限修复：解析、猜测和重写任意 HTML/CDN/脚本顺序。

但所交付的 0.1.1 不能据此直接判为可靠。行业先例只能证明路线合理，不能替实现背书。对实际安装包逐字节审计和双浏览器复现后，0.1.1 至少存在以下非排版缺陷：

1. **P0 脚本边界逃逸**：普通 JSON 字符串中的 `</script><script>…` 被直接拼进可信脚本；CSP 又允许所有内联脚本。Chromium 109 与当前 Chromium 均实际执行，插件报告仍为零。
2. **Schema 没有进入运行链**：安装包不含运行时 Schema 调用；未知字段、缺字段和多种非法语义可静默通过。
3. **Markdown 仍能带入原始 HTML/CSS/外部属性**：默认 marked + DOMPurify 配置不等于封闭内容语言，样式甚至可隐藏警告。
4. **跨字段语义遗漏**：表格行列错位可造成运行异常；无效日期、空图、关系边悬空、进度上限、资源预算等未完整检查。
5. **运行错误不回写报告**：组件错误虽可能出现错误卡，但文件内 JSON 报告和 Dify 静态计数仍可显示零。
6. **图片公开参数缺失且调用 API 错位**：tool YAML 未声明 slot1–slot20；包装层把实际二返回值的图片校验函数按三返回值解包，真实图片会错误降级。
7. **契约和说明漂移**：manifest 重复 YAML key 覆盖描述；provider/README 仍写旧 0.0.8 HTML 模式；工具提示仍列已删除的 html 块；文档对报告位置、浮标、块数量和字段能力的说明与代码不一致。
8. **资源与编码边界**：重复键类型混淆、NaN/Infinity、Unicode 代理字符、多个资源型块无预算；部分输入会整工具崩溃或静默改值。

因此 0.1.1 的“强化故障注入 10/10、全块渲染”等自述不能作为发布证据；交付包也没有附可重跑测试程序和对应报告。

## 对行业对照的评价

| 先例 | 官方事实 | 适合直接搬什么 | 不能直接替代什么 |
|---|---|---|---|
| Editor.js | 块式编辑器，保存通用 JSON；Tools 可扩展 | 人工编辑界面、`type+data` 信封、注册表 | 不是本插件的离线报告编译器；其段落数据仍可含 HTML |
| Portable Text | JSON 块数组，可渲染多种目标；支持自定义块 | 不存 HTML、结构化 mark、未知类型策略 | 规范仍是 Working Draft；图表报告能力仍需自建 |
| Adaptive Cards | 有版本、requires、元素 fallback/drop、顶层 fallbackText | 版本协商与显式 fallback 思想 | 并非“自动显示未知原文”；官方无 Python renderer，词汇也不覆盖整页报告 |
| Vega-Lite | 高层声明式可视化 JSON | 图表意图分层、数据驱动 | 只替代图表子系统，不是整页编译器；换掉已锁定 ECharts 是工程选择，不是原则要求 |
| MJML | 语义组件编译邮件 HTML，支持 JSON 与校验模式 | 编译器、strict/soft、目标客户端矩阵 | 面向邮件且保留 `mj-raw`/HTML 逃逸；“Python 一定不能用”不准确，官方列社区 Python 绑定 |
| JSON Schema | 标准结构/类型/枚举/附加字段契约 | 应直接采用为严格机器契约 | `default` 不会自动填值，format 通常只是 annotation；不能替容错、重复键、跨字段语义或浏览器终验 |

Dify 官方工具插件教程能确认本插件选择 Python 3.12 路线，但不能证明运行环境“绝对没有 Node”。所以 Claude 关于“Node 被彻底堵死”的说法应改为：即便可带 Node/MJML，其邮件领域与逃逸口仍不符合本需求。

最合理的复用边界是：PageSpec 自有封闭语义层 + JSON Schema 严格契约 + 有界容错解析 + 跨字段语义验证 + 现有锁定离线渲染库；借 Adaptive Cards 的 fallback、Editor.js 的注册表/可选编辑器、MJML 的目标环境门禁。没有现成项目能原样覆盖“Dify Python 插件 + 断网单文件 + 交互报告 + 高容错 + 无任意 HTML/JS”。

## 0.3.0 的处理

- 所有用户值经 script-safe JSON 编码；唯一 nonce CSP；最终 HTML 独立终审。
- Canonical Schema 由构建器生成，并用包内 Ajv 对反例与 28 种块测试；Schema、validator、renderer 注册表交叉比对。
- 容错传输、语义验证、渲染分层；有界修复截断；有歧义也按固定评分猜测，并把候选、选择、原因、置信度写入审计；坏块可见降级。
- Markdown raw HTML 文字化；Mermaid 禁动作/URL；图片只走 20 个公开 slot。
- 运行失败同步写错误卡与 JSON 运行报告；不添加浮标。
- 安装包只暴露 JSON 工具；旧任意 HTML 工具不注册。

## 已执行验证

- Python 契约、容错、语义、真实工具入口、172 库目录与九种图片：64/64。
- 变异门禁覆盖截断、歧义、类型重复键、表格、图、日期、进度、CSS 宽度、Mermaid 外部动作、fallback 与脚本闭合；传输歧义的正确结果是“确定性猜测并留痕”，不是拒绝。
- 固定种子异常输入：10,000 份全部生成有效页面，未捕获异常 0。
- 四卷固定覆盖 35/63/41/33，共 172 个库；每个候选文件都经过当前 Chromium 与 Chromium 109 的桌面/390px、零网络、控制台/页面错误、可见性、布局和故障注入门禁。最终数字只以交付包内冻结哈希对应的报告为准。

这些数字只对应随包脚本和冻结候选；用户私有 Dify 1.7.1 的真实签名安装仍须在该部署上验收，不能由本地测试冒充。

## 官方资料

- Editor.js：https://editorjs.io/ 、https://editorjs.io/saving-data/
- Portable Text：https://www.portabletext.org/specification/ 、https://www.portabletext.org/rendering/
- Adaptive Cards：https://learn.microsoft.com/en-us/adaptive-cards/rendering-cards/implement-a-renderer
- Vega-Lite：https://vega.github.io/vega-lite/docs/
- MJML：https://documentation.mjml.io/
- JSON Schema：https://json-schema.org/specification
- Dify Tool Plugin：https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/tool-plugin
