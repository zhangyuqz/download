/*
  01_八表完整流程兼容测试_仅临时表.sql

  只在当前连接中建立 TEMPORARY TABLE；不会修改正式表、影子表或业务数据。
  每张表都实际演练与正式脚本相同的完整链路：
  真实结构克隆 → 动态生成整行摘要列 → 临时行号/索引 → 复制一行并制造完全重复
  → 严格逐列二进制比对去重 → 建立最终 UNIQUE BTREE。

  兼容性修复：全文件只有普通 SQL 和 EXECUTE IMMEDIATE；没有 DELIMITER、
  BEGIN NOT ATOMIC、DECLARE、IF/LOOP、存储过程或匿名复合块。
*/
SET NAMES utf8mb4;
SET SESSION group_concat_max_len = 4194304;
SET SESSION max_statement_time = 0;
USE `xzfz`;

DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_huadan`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_tingtuiyanpan`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_xianyirendaji`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_tingtuixiansuo`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_butuixiansuo`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_duankaxiansuobiao`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_butuiyanpan`;
DROP TEMPORARY TABLE IF EXISTS `__xzfz_rg_probe_renyuanguanxi`;

/* ===== huadan ===== */
SET @xz_table = 'huadan';
SET @xz_probe = '__xzfz_rg_probe_huadan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table
  AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id')
  AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table
  AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id')
  AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table
  AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id')
  AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== tingtuiyanpan ===== */
SET @xz_table = 'tingtuiyanpan';
SET @xz_probe = '__xzfz_rg_probe_tingtuiyanpan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== xianyirendaji ===== */
SET @xz_table = 'xianyirendaji';
SET @xz_probe = '__xzfz_rg_probe_xianyirendaji';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== tingtuixiansuo ===== */
SET @xz_table = 'tingtuixiansuo';
SET @xz_probe = '__xzfz_rg_probe_tingtuixiansuo';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== butuixiansuo ===== */
SET @xz_table = 'butuixiansuo';
SET @xz_probe = '__xzfz_rg_probe_butuixiansuo';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== duankaxiansuobiao ===== */
SET @xz_table = 'duankaxiansuobiao';
SET @xz_probe = '__xzfz_rg_probe_duankaxiansuobiao';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== butuiyanpan ===== */
SET @xz_table = 'butuiyanpan';
SET @xz_probe = '__xzfz_rg_probe_butuiyanpan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

/* ===== renyuanguanxi ===== */
SET @xz_table = 'renyuanguanxi';
SET @xz_probe = '__xzfz_rg_probe_renyuanguanxi';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TEMPORARY TABLE `',@xz_probe,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_probe,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_probe,'` LIMIT 1'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_probe,'` a JOIN `',@xz_probe,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `uq_xzfz_rg_tmp_id` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_probe,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_probe,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('SELECT ''',@xz_table,''' AS table_name, COUNT(*) AS probe_rows, COUNT(DISTINCT `__xzfz_rg_row_sha512`) AS distinct_hashes, IF(COUNT(*)=COUNT(DISTINCT `__xzfz_rg_row_sha512`),''PASS'',''FAIL'') AS result FROM `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP TEMPORARY TABLE `',@xz_probe,'`'); EXECUTE IMMEDIATE @xz_sql;

SELECT 'PASS_CONDITION' AS item, '以上8张表各返回一行且 result 均为 PASS，并且执行记录没有红色错误' AS required_result;
