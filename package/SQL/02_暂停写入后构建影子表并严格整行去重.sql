/*
  02_暂停写入后构建影子表并严格整行去重.sql

  执行前必须暂停下列8张表的 INSERT / UPDATE / DELETE：
  huadan, tingtuiyanpan, xianyirendaji, tingtuixiansuo,
  butuixiansuo, duankaxiansuobiao, butuiyanpan, renyuanguanxi。

  只建立 __xzfz_rg_new_* 影子表，不改名、不删除正式表。
  重复定义：同一表中全部非生成业务列逐列、逐字节完全相同。
  摘要只用于缩小候选范围；DELETE 还会逐列执行 HEX(BINARY 值) 的 NULL 安全比较。
  如果极端情况下两个不同整行产生同一 SHA-512，最终唯一索引会失败并阻止换表，
  不会把这两行当作重复删除。

  V4 修复：临时 AUTO_INCREMENT 行号列不再先删除其唯一索引。
  现在直接 DROP COLUMN，MariaDB 会同时删除只包含该列的索引，避免错误 1075。

  本文件没有 DELIMITER、BEGIN NOT ATOMIC、DECLARE、过程、匿名块或循环。
*/
SET NAMES utf8mb4;
SET SESSION group_concat_max_len = 4194304;
SET SESSION max_statement_time = 0;
USE `xzfz`;

DROP TABLE IF EXISTS
  `__xzfz_rg_new_huadan`,
  `__xzfz_rg_new_tingtuiyanpan`,
  `__xzfz_rg_new_xianyirendaji`,
  `__xzfz_rg_new_tingtuixiansuo`,
  `__xzfz_rg_new_butuixiansuo`,
  `__xzfz_rg_new_duankaxiansuobiao`,
  `__xzfz_rg_new_butuiyanpan`,
  `__xzfz_rg_new_renyuanguanxi`;

/* ===== huadan：复制全部业务列、严格整行去重并建立永久防重索引 ===== */
SET @xz_table='huadan'; SET @xz_shadow='__xzfz_rg_new_huadan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'huadan' AS table_name,(SELECT COUNT(*) FROM `huadan`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_huadan`) AS shadow_rows,(SELECT COUNT(*) FROM `huadan`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_huadan`) AS removed_full_duplicates;

/* ===== tingtuiyanpan ===== */
SET @xz_table='tingtuiyanpan'; SET @xz_shadow='__xzfz_rg_new_tingtuiyanpan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'tingtuiyanpan' AS table_name,(SELECT COUNT(*) FROM `tingtuiyanpan`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_tingtuiyanpan`) AS shadow_rows,(SELECT COUNT(*) FROM `tingtuiyanpan`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_tingtuiyanpan`) AS removed_full_duplicates;

/* ===== xianyirendaji ===== */
SET @xz_table='xianyirendaji'; SET @xz_shadow='__xzfz_rg_new_xianyirendaji';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'xianyirendaji' AS table_name,(SELECT COUNT(*) FROM `xianyirendaji`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_xianyirendaji`) AS shadow_rows,(SELECT COUNT(*) FROM `xianyirendaji`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_xianyirendaji`) AS removed_full_duplicates;

/* ===== tingtuixiansuo ===== */
SET @xz_table='tingtuixiansuo'; SET @xz_shadow='__xzfz_rg_new_tingtuixiansuo';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'tingtuixiansuo' AS table_name,(SELECT COUNT(*) FROM `tingtuixiansuo`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_tingtuixiansuo`) AS shadow_rows,(SELECT COUNT(*) FROM `tingtuixiansuo`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_tingtuixiansuo`) AS removed_full_duplicates;

/* ===== butuixiansuo ===== */
SET @xz_table='butuixiansuo'; SET @xz_shadow='__xzfz_rg_new_butuixiansuo';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'butuixiansuo' AS table_name,(SELECT COUNT(*) FROM `butuixiansuo`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_butuixiansuo`) AS shadow_rows,(SELECT COUNT(*) FROM `butuixiansuo`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_butuixiansuo`) AS removed_full_duplicates;

/* ===== duankaxiansuobiao ===== */
SET @xz_table='duankaxiansuobiao'; SET @xz_shadow='__xzfz_rg_new_duankaxiansuobiao';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'duankaxiansuobiao' AS table_name,(SELECT COUNT(*) FROM `duankaxiansuobiao`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_duankaxiansuobiao`) AS shadow_rows,(SELECT COUNT(*) FROM `duankaxiansuobiao`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_duankaxiansuobiao`) AS removed_full_duplicates;

/* ===== butuiyanpan ===== */
SET @xz_table='butuiyanpan'; SET @xz_shadow='__xzfz_rg_new_butuiyanpan';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'butuiyanpan' AS table_name,(SELECT COUNT(*) FROM `butuiyanpan`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_butuiyanpan`) AS shadow_rows,(SELECT COUNT(*) FROM `butuiyanpan`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_butuiyanpan`) AS removed_full_duplicates;

/* ===== renyuanguanxi ===== */
SET @xz_table='renyuanguanxi'; SET @xz_shadow='__xzfz_rg_new_renyuanguanxi';
SELECT GROUP_CONCAT(CONCAT('`',REPLACE(`COLUMN_NAME`,'`','``'),'`') ORDER BY `ORDINAL_POSITION` SEPARATOR ',') INTO @xz_cols FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('IF(`',REPLACE(`COLUMN_NAME`,'`','``'),'` IS NULL,''N'',CONCAT(''V'',HEX(BINARY `',REPLACE(`COLUMN_NAME`,'`','``'),'`)))') ORDER BY `ORDINAL_POSITION` SEPARATOR ',''|'',') INTO @xz_hash_parts FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SELECT GROUP_CONCAT(CONCAT('HEX(BINARY a.`',REPLACE(`COLUMN_NAME`,'`','``'),'`) <=> HEX(BINARY b.`',REPLACE(`COLUMN_NAME`,'`','``'),'`)') ORDER BY `ORDINAL_POSITION` SEPARATOR ' AND ') INTO @xz_exact FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME`=@xz_table AND `COLUMN_NAME` NOT IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id') AND `EXTRA` NOT LIKE '%GENERATED%';
SET @xz_sql=CONCAT('CREATE TABLE `',@xz_shadow,'` LIKE `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_row_sha512` BINARY(64) GENERATED ALWAYS AS (UNHEX(SHA2(CONCAT(',@xz_hash_parts,'),512))) PERSISTENT INVISIBLE'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` ADD COLUMN `__xzfz_rg_tmp_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT, ADD UNIQUE KEY `uq_xzfz_rg_tmp_id` (`__xzfz_rg_tmp_id`), ADD KEY `ix_xzfz_rg_tmp_hash` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('INSERT INTO `',@xz_shadow,'` (',@xz_cols,') SELECT ',@xz_cols,' FROM `',@xz_table,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DELETE b FROM `',@xz_shadow,'` a JOIN `',@xz_shadow,'` b ON a.`__xzfz_rg_row_sha512`=b.`__xzfz_rg_row_sha512` AND a.`__xzfz_rg_tmp_id`<b.`__xzfz_rg_tmp_id` AND ',@xz_exact); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('ALTER TABLE `',@xz_shadow,'` DROP COLUMN `__xzfz_rg_tmp_id`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('DROP INDEX `ix_xzfz_rg_tmp_hash` ON `',@xz_shadow,'`'); EXECUTE IMMEDIATE @xz_sql;
SET @xz_sql=CONCAT('CREATE UNIQUE INDEX `uq_xzfz_rg_fullrow_sha512` USING BTREE ON `',@xz_shadow,'` (`__xzfz_rg_row_sha512`)'); EXECUTE IMMEDIATE @xz_sql;
SELECT 'renyuanguanxi' AS table_name,(SELECT COUNT(*) FROM `renyuanguanxi`) AS source_rows,(SELECT COUNT(*) FROM `__xzfz_rg_new_renyuanguanxi`) AS shadow_rows,(SELECT COUNT(*) FROM `renyuanguanxi`)-(SELECT COUNT(*) FROM `__xzfz_rg_new_renyuanguanxi`) AS removed_full_duplicates;

/* 汇总：必须是8张影子表、8个生成列、8个唯一BTREE索引。 */
SELECT
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi')) AS shadow_tables,
  (SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi')
    AND `COLUMN_NAME`='__xzfz_rg_row_sha512' AND `EXTRA` LIKE '%GENERATED%') AS generated_hash_columns,
  (SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`STATISTICS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi')
    AND `INDEX_NAME`='uq_xzfz_rg_fullrow_sha512' AND `NON_UNIQUE`=0 AND `INDEX_TYPE`='BTREE') AS unique_btree_indexes,
  IF(
    (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` LIKE '__xzfz_rg_new_%')=8
    AND (SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`STATISTICS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` LIKE '__xzfz_rg_new_%' AND `INDEX_NAME`='uq_xzfz_rg_fullrow_sha512' AND `NON_UNIQUE`=0)=8,
    'PASS_READY_FOR_03','FAIL_DO_NOT_RUN_03'
  ) AS result;
