/*
  00_现场状态_只读.sql
  作用：只读取当前 xzfz 数据库状态，不修改任何表和数据。
  本修正版全包禁止使用 DELIMITER、BEGIN NOT ATOMIC、DECLARE、存储过程和匿名复合块。
*/
SET NAMES utf8mb4;
USE `xzfz`;

SELECT VERSION() AS mariadb_version, DATABASE() AS current_database;

SELECT
  `TABLE_NAME`,
  `ENGINE`,
  `TABLE_ROWS`
FROM `information_schema`.`TABLES`
WHERE `TABLE_SCHEMA` = 'xzfz'
  AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
ORDER BY `TABLE_NAME`;

SELECT
  SUM(`TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )) AS formal_tables,
  SUM(`TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'
  )) AS shadow_tables,
  SUM(`TABLE_NAME` IN (
    '__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo',
    '__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi'
  )) AS backup_tables
FROM `information_schema`.`TABLES`
WHERE `TABLE_SCHEMA` = 'xzfz';

SELECT
  `TABLE_NAME`,
  `COLUMN_NAME`,
  `COLUMN_TYPE`,
  `EXTRA`
FROM `information_schema`.`COLUMNS`
WHERE `TABLE_SCHEMA` = 'xzfz'
  AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
  AND `COLUMN_NAME` IN ('__xzfz_rg_row_sha512','__xzfz_rg_tmp_id')
ORDER BY `TABLE_NAME`,`ORDINAL_POSITION`;

SELECT
  `TABLE_NAME`,
  `INDEX_NAME`,
  `NON_UNIQUE`,
  `INDEX_TYPE`,
  `COLUMN_NAME`
FROM `information_schema`.`STATISTICS`
WHERE `TABLE_SCHEMA` = 'xzfz'
  AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
  AND `INDEX_NAME` = 'uq_xzfz_rg_fullrow_sha512'
ORDER BY `TABLE_NAME`,`SEQ_IN_INDEX`;

SELECT
  `TRIGGER_NAME`, `EVENT_MANIPULATION`, `EVENT_OBJECT_TABLE`, `ACTION_TIMING`
FROM `information_schema`.`TRIGGERS`
WHERE `TRIGGER_SCHEMA` = 'xzfz'
  AND `EVENT_OBJECT_TABLE` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
ORDER BY `EVENT_OBJECT_TABLE`,`TRIGGER_NAME`;
