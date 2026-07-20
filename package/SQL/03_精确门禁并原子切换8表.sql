/*
  03_精确门禁并原子切换8表.sql

  执行前：8张目标表必须继续保持暂停写入。
  仅当以下条件同时满足时才执行一条多表 RENAME TABLE：
  - 8张正式表存在；
  - 8张影子表存在；
  - 8张旧表备份名均不存在；
  - 8张影子表均有摘要生成列；
  - 8张影子表均有 uq_xzfz_rg_fullrow_sha512 唯一BTREE索引。

  条件不满足时只返回 NOT_READY_DO_NOT_SWITCH，不执行改名，也不故意制造SQL错误。
  全文件没有 DELIMITER 或任何复合块。
*/
SET NAMES utf8mb4;
USE `xzfz`;

SET @xz_formal_count = (
  SELECT COUNT(*) FROM `information_schema`.`TABLES`
  WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo',
    'butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi'
  )
);
SET @xz_shadow_count = (
  SELECT COUNT(*) FROM `information_schema`.`TABLES`
  WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'
  )
);
SET @xz_backup_count = (
  SELECT COUNT(*) FROM `information_schema`.`TABLES`
  WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo',
    '__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi'
  )
);
SET @xz_hash_column_count = (
  SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`COLUMNS`
  WHERE `TABLE_SCHEMA`='xzfz'
    AND `TABLE_NAME` IN (
      '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
      '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'
    )
    AND `COLUMN_NAME`='__xzfz_rg_row_sha512'
    AND `COLUMN_TYPE`='binary(64)'
    AND `EXTRA` LIKE '%GENERATED%'
);
SET @xz_unique_index_count = (
  SELECT COUNT(DISTINCT `TABLE_NAME`) FROM `information_schema`.`STATISTICS`
  WHERE `TABLE_SCHEMA`='xzfz'
    AND `TABLE_NAME` IN (
      '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
      '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'
    )
    AND `INDEX_NAME`='uq_xzfz_rg_fullrow_sha512'
    AND `NON_UNIQUE`=0
    AND `INDEX_TYPE`='BTREE'
    AND `COLUMN_NAME`='__xzfz_rg_row_sha512'
);

SET @xz_ready = IF(
  @xz_formal_count=8
  AND @xz_shadow_count=8
  AND @xz_backup_count=0
  AND @xz_hash_column_count=8
  AND @xz_unique_index_count=8,
  1,0
);

SELECT
  @xz_formal_count AS formal_tables,
  @xz_shadow_count AS shadow_tables,
  @xz_backup_count AS backup_tables,
  @xz_hash_column_count AS generated_hash_columns,
  @xz_unique_index_count AS unique_btree_indexes,
  IF(@xz_ready=1,'READY_TO_SWITCH','NOT_READY_DO_NOT_SWITCH') AS result;

SET @xz_switch_sql = IF(
  @xz_ready=1,
  'RENAME TABLE
    `huadan` TO `__xzfz_rg_old_huadan`, `__xzfz_rg_new_huadan` TO `huadan`,
    `tingtuiyanpan` TO `__xzfz_rg_old_tingtuiyanpan`, `__xzfz_rg_new_tingtuiyanpan` TO `tingtuiyanpan`,
    `xianyirendaji` TO `__xzfz_rg_old_xianyirendaji`, `__xzfz_rg_new_xianyirendaji` TO `xianyirendaji`,
    `tingtuixiansuo` TO `__xzfz_rg_old_tingtuixiansuo`, `__xzfz_rg_new_tingtuixiansuo` TO `tingtuixiansuo`,
    `butuixiansuo` TO `__xzfz_rg_old_butuixiansuo`, `__xzfz_rg_new_butuixiansuo` TO `butuixiansuo`,
    `duankaxiansuobiao` TO `__xzfz_rg_old_duankaxiansuobiao`, `__xzfz_rg_new_duankaxiansuobiao` TO `duankaxiansuobiao`,
    `butuiyanpan` TO `__xzfz_rg_old_butuiyanpan`, `__xzfz_rg_new_butuiyanpan` TO `butuiyanpan`,
    `renyuanguanxi` TO `__xzfz_rg_old_renyuanguanxi`, `__xzfz_rg_new_renyuanguanxi` TO `renyuanguanxi`',
  'SELECT ''NOT_READY_DO_NOT_SWITCH'' AS result'
);
EXECUTE IMMEDIATE @xz_switch_sql;

SELECT
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    'huadan','tingtuiyanpan','xianyirendaji','tingtuixiansuo','butuixiansuo','duankaxiansuobiao','butuiyanpan','renyuanguanxi')) AS formal_tables,
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo',
    '__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi')) AS backup_tables,
  (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi')) AS remaining_shadow_tables,
  IF(
    (SELECT COUNT(*) FROM `information_schema`.`TABLES` WHERE `TABLE_SCHEMA`='xzfz' AND `TABLE_NAME` IN (
      '__xzfz_rg_old_huadan','__xzfz_rg_old_tingtuiyanpan','__xzfz_rg_old_xianyirendaji','__xzfz_rg_old_tingtuixiansuo',
      '__xzfz_rg_old_butuixiansuo','__xzfz_rg_old_duankaxiansuobiao','__xzfz_rg_old_butuiyanpan','__xzfz_rg_old_renyuanguanxi'))=8,
    'SWITCHED_RUN_04','NOT_SWITCHED'
  ) AS final_state;
