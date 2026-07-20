/*
  02R_仅清理未上线影子表.sql
  仅在 02 尚未执行 03 原子切换时使用。
  只删除 __xzfz_rg_new_* 影子表，不触碰8张正式表、旧表备份或其他表。
*/
SET NAMES utf8mb4;
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

SELECT
  COUNT(*) AS remaining_shadow_tables,
  IF(COUNT(*)=0,'CLEANED','NOT_CLEAN') AS result
FROM `information_schema`.`TABLES`
WHERE `TABLE_SCHEMA`='xzfz'
  AND `TABLE_NAME` IN (
    '__xzfz_rg_new_huadan','__xzfz_rg_new_tingtuiyanpan','__xzfz_rg_new_xianyirendaji','__xzfz_rg_new_tingtuixiansuo',
    '__xzfz_rg_new_butuixiansuo','__xzfz_rg_new_duankaxiansuobiao','__xzfz_rg_new_butuiyanpan','__xzfz_rg_new_renyuanguanxi'
  );
