/*
  04_切换后只读验收.sql
  只读检查，不修改任何对象。
  只有最后 result=PASS_RESUME_WRITES 才恢复8张目标表写入。
*/
SET NAMES utf8mb4;
USE `xzfz`;

SELECT
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi')) AS formal_tables,
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo',
    '__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi')) AS backup_tables,
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi')) AS shadow_tables;

SELECT
  `TABLE_NAME`,
  `COLUMN_NAME`,
  `COLUMN_TYPE`,
  `EXTRA`
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA`='xzfz'
  AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
  AND `COLUMN_NAME`='__xzfz_rg_row_sha512'
ORDER BY `TABLE_NAME`;

SELECT
  `TABLE_NAME`, `INDEX_NAME`, `NON_UNIQUE`, `INDEX_TYPE`, `COLUMN_NAME`
FROM `information_schema`.`STATISTICS`
WHERE `TABLE_SCHEMA`='xzfz'
  AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
  AND `INDEX_NAME`='uq_xzfz_rg_fullrow_sha512'
ORDER BY `TABLE_NAME`,`SEQ_IN_INDEX`;

SELECT 'huadan' AS table_name,(SELECT COUNT(*) FROM `__xzfz_rg_old_huadan`) AS before_rows,(SELECT COUNT(*) FROM `huadan`) AS after_rows,(SELECT COUNT(*) FROM `__xzfz_rg_old_huadan`)-(SELECT COUNT(*) FROM `huadan`) AS removed_full_duplicates
UNION ALL SELECT 'tingtuiyanpan',(SELECT COUNT(*) FROM `__xzfz_rg_old_tingtuiyanpan`),(SELECT COUNT(*) FROM `tingtuiyanpan`),(SELECT COUNT(*) FROM `__xzfz_rg_old_tingtuiyanpan`)-(SELECT COUNT(*) FROM `tingtuiyanpan`)
UNION ALL SELECT 'xianyirendaji',(SELECT COUNT(*) FROM `__xzfz_rg_old_xianyirendaji`),(SELECT COUNT(*) FROM `xianyirendaji`),(SELECT COUNT(*) FROM `__xzfz_rg_old_xianyirendaji`)-(SELECT COUNT(*) FROM `xianyirendaji`)
UNION ALL SELECT 'tingtuixiansuo',(SELECT COUNT(*) FROM `__xzfz_rg_old_tingtuixiansuo`),(SELECT COUNT(*) FROM `tingtuixiansuo`),(SELECT COUNT(*) FROM `__xzfz_rg_old_tingtuixiansuo`)-(SELECT COUNT(*) FROM `tingtuixiansuo`)
UNION ALL SELECT 'butuixiansuo',(SELECT COUNT(*) FROM `__xzfz_rg_old_butuixiansuo`),(SELECT COUNT(*) FROM `butuixiansuo`),(SELECT COUNT(*) FROM `__xzfz_rg_old_butuixiansuo`)-(SELECT COUNT(*) FROM `butuixiansuo`)
UNION ALL SELECT 'duankaxiansuobiao',(SELECT COUNT(*) FROM `__xzfz_rg_old_duankaxiansuobiao`),(SELECT COUNT(*) FROM `duankaxiansuobiao`),(SELECT COUNT(*) FROM `__xzfz_rg_old_duankaxiansuobiao`)-(SELECT COUNT(*) FROM `duankaxiansuobiao`)
UNION ALL SELECT 'butuiyanpan',(SELECT COUNT(*) FROM `__xzfz_rg_old_butuiyanpan`),(SELECT COUNT(*) FROM `butuiyanpan`),(SELECT COUNT(*) FROM `__xzfz_rg_old_butuiyanpan`)-(SELECT COUNT(*) FROM `butuiyanpan`)
UNION ALL SELECT 'renyuanguanxi',(SELECT COUNT(*) FROM `__xzfz_rg_old_renyuanguanxi`),(SELECT COUNT(*) FROM `renyuanguanxi`),(SELECT COUNT(*) FROM `__xzfz_rg_old_renyuanguanxi`)-(SELECT COUNT(*) FROM `renyuanguanxi`);

SET @xz_formal_count=(SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN ('huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo','butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'));
SET @xz_backup_count=(SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN ('__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo','__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi'));
SET @xz_shadow_count=(SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN ('__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo','__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'));
SET @xz_hash_column_count=(SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`COLUMNS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN ('huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo','butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi') AND `COLUMN_NAME`='__xzfz_rg_row_sha512' AND `COLUMN_TYPE`='binary(64)' AND `EXTRA` LIKE '%GENERATED%');
SET @xz_unique_index_count=(SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`STATISTICS` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN ('huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo','butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi') AND `INDEX_NAME`='uq_xzfz_rg_fullrow_sha512' AND `NON_UNIQUE`=0 AND `INDEX_TYPE`='BTREE' AND `COLUMN_NAME`='__xzfz_rg_row_sha512');

SELECT
  @xz_formal_count AS formal_tables,
  @xz_backup_count AS backup_tables,
  @xz_shadow_count AS shadow_tables,
  @xz_hash_column_count AS generated_hash_columns,
  @xz_unique_index_count AS unique_btree_indexes,
  IF(@xz_formal_count=8 AND @xz_backup_count=8 AND @xz_shadow_count=0 AND @xz_hash_column_count=8 AND @xz_unique_index_count=8,
     'PASS_RESUME_WRITES','FAIL_KEEP_WRITES_PAUSED') AS result;
