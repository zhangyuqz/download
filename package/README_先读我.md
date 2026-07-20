# xzfz 8表整行去重防重：Navicat纯SQL修正版 V3

## 先停用旧包

截图中的 `1064` 发生在：

```sql
DELIMITER $$
BEGIN NOT ATOMIC
```

这不是业务数据错误，而是旧脚本把 MariaDB 命令行客户端的分隔符命令和匿名复合块交给 Navicat 查询执行通道。旧包、V2包及其 SQL 均停止使用；不要跳过旧 `01` 继续执行旧 `02/03`。

从截图所示位置看，在没有继续执行后续文件的前提下，成功执行的只有会话设置、选库和临时表操作；8张正式表没有被修改。

## 本修正版的宏观修复

全套 SQL 已删除：

- `DELIMITER`
- `BEGIN NOT ATOMIC`
- `BEGIN ... END`
- `DECLARE`
- 存储过程、匿名复合块、IF/LOOP 控制块

只使用 Navicat 可按分号拆分执行的普通 SQL，以及 MariaDB 服务端原生的 `EXECUTE IMMEDIATE`。兼容测试不再只测一个玩具表达式，而是在8张真实表结构的临时副本上完整演练：生成摘要、复制一行、制造整行重复、严格去重、建立唯一BTREE。

摘要表达式也删除了曾导致 `1901` 的 `DATE_FORMAT()`；现在按每个业务列的二进制字节生成固定列序的 SHA-512。`NULL`、空字符串、`nan`、大小写和尾随空格均保持区别。

## 只实现四项要求

1. 删除8张目标表中原有的整行完全重复，每组保留一行。
2. 以后阻止整行全部业务字段完全相同的数据再次写入。
3. 干扰字符清理由上一包的 Dify YML 执行，本次 SQL 修复不改变该业务逻辑。
4. 单行容错仍由上一包的 Dify YML 执行，本次 SQL 修复不增加任何拒绝规则。

仅处理：

```text
huadan
tingtuiyanpan
xianyirendaji
tingtuixiansuo
butuixiansuo
duankaxiansuobiao
butuiyanpan
renyuanguanxi
```

不会修改 `huadan_copy1`、任何 `fujing_kaohe_*` 表或其他非白名单表；不增加业务字段、主键、外键、NOT NULL、CHECK、单字段唯一限制或部分字段联合唯一限制。

## 重复判定

只有同一张表中全部非生成业务列都相同才是重复。

摘要编码对每列固定顺序写入：

```text
NULL      -> N
非NULL值  -> V + 原值二进制字节的HEX
列之间    -> |
```

摘要相同后，删除旧重复前还会对每个业务列执行：

```sql
HEX(BINARY a.`字段`) <=> HEX(BINARY b.`字段`)
```

因此某几个字段相同、手机号相同、身份证相同、案件编号相同都不会被当作重复。若极端情况下不同整行发生 SHA-512 碰撞，逐列比较不会删除它们；随后唯一索引创建会失败并阻止换表。

## Navicat执行顺序

### 0. 关闭旧查询页

关闭截图中的旧查询窗口，不再执行旧文件。

### 1. 执行只读状态

使用 Navicat 的“执行 SQL 文件”运行：

```text
00_现场状态_只读.sql
```

### 2. 运行完整临时演练

运行：

```text
01_八表完整流程兼容测试_仅临时表.sql
```

该文件只使用当前连接的临时表。必须看到8张表各返回一行且 `result=PASS`，执行记录中没有红色错误。

### 3. 暂停8表写入

暂停8张目标表的 Dify 导入、定时任务、同步脚本、人工 INSERT/UPDATE/DELETE，并保持暂停直到 `04` 返回通过。

### 4. 构建影子表并去重

运行：

```text
02_暂停写入后构建影子表并严格整行去重.sql
```

`huadan` 数据量最大，此文件会耗时。最后必须返回：

```text
shadow_tables = 8
generated_hash_columns = 8
unique_btree_indexes = 8
result = PASS_READY_FOR_03
```

如 `02` 中断，不要执行 `03`；运行 `02R_仅清理未上线影子表.sql` 后，再重新运行 `01 -> 02`。

### 5. 原子切换

运行：

```text
03_精确门禁并原子切换8表.sql
```

脚本会在服务端重新核对8张影子表、摘要列和唯一BTREE。条件不满足时只返回 `NOT_READY_DO_NOT_SWITCH`，不会换表；满足时用一条多表 `RENAME TABLE` 切换，并把原表保留为 `__xzfz_rg_old_*`。

### 6. 切换后验收

运行：

```text
04_切换后只读验收.sql
```

只有最后结果为：

```text
PASS_RESUME_WRITES
```

才能恢复8表写入。

## YML说明

当前报错位于 Navicat SQL，Dify YML 没有参与这一条语句。本修正版不再擅自改动已经按四项要求生成的 YML；继续使用上一“仅四项终版”包内：

```text
YML/将表格存入MySQL数据库_仅四项容错版_Dify1.7.1_20260720.yml
```

但旧包内全部 SQL 作废，只使用本修正版 SQL。

## 设计边界

- 使用固定64字节 SHA-512 技术摘要列和普通 UNIQUE BTREE，不给任何业务字段加唯一限制。
- 普通 `SELECT *` 不返回不可见摘要列。
- 原表在切换后继续作为备份保留，本包不自动删除。
- SQL 不清洗业务正文；干扰字符清理由 YML 按既定四项要求处理。
- 现场兼容性的最终依据是 `01` 在实际 MariaDB 服务器上的8表临时演练。
