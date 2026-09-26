"""校准记录业务规则：状态流转、字段校验、判定口径与台账联动都收在这里。

约定：
- 校准记录只追加、不覆盖：同一「校准编号」重复提交返回原记录，绝不产生第二条；
  设备台账只接收最新一次已判定校准的结论，历史校准行原样保留。
- 判定口径按记录自身的「校准方式」区分：实验室校准必须登记并实际使用标准物质，
  现场校准不依赖标准物质；合格率统计与列表、台账共用同一份判定结果。
- 已停用设备只允许查看历史校准记录，不能再登记或流转新校准。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.store import store

MODULE = "calibration"
INSTRUMENT_MODULE = "instrument"
REAGENT_MODULE = "reagent"

REQUIRED_FIELDS = ["校准编号", "关联设备", "校准方式"]
ALL_FIELDS = ["校准编号", "关联设备", "校准方式", "标准物质", "校准结果", "校准日期", "下次校准日", "校准状态"]

STATUS_ORDER = ["待校准", "校准中", "已合格", "不合格"]
ACTION_RULES = {"开始校准": "校准中", "判定合格": "已合格", "判定不合格": "不合格"}
NEGATIVE_ACTIONS = ["判定不合格"]

# 校准方式归一化：现场校准与实验室校准的判定口径不同，不能混在一套规则里。
LAB_METHOD = "实验室校准"
FIELD_METHOD = "现场校准"
METHOD_ALIASES = {
    "实验室校准": LAB_METHOD,
    "实验室": LAB_METHOD,
    "送检校准": LAB_METHOD,
    "现场校准": FIELD_METHOD,
    "现场": FIELD_METHOD,
    "在线校准": FIELD_METHOD,
}

# 标准物质使用后在试剂物料列表里的状态（试剂物料的终态之一）。
REAGENT_USED_STATUS = "已使用"

# 「校准周期」字段在示例数据里可能是自然语言，解析不出来时按 12 个月兜底。
_PERIOD_RE = re.compile(r"(\d+)")


class CalibrationService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("校准编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ------------------------------------------------------------------ 统计
    def stats(self) -> dict[str, int]:
        """合格率口径：同一台设备只取最近一次已判定校准，避免历史结果稀释合格率。

        列表、设备台账、这里的统计读的是同一份校准记录，结论天然一致。
        """
        rows = store.rows(MODULE)
        pending = sum(1 for row in rows if row.get("status") in ("待校准", "校准中"))

        latest_by_device: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("status") not in ("已合格", "不合格"):
                continue
            device = str(row.get("关联设备") or "")
            current = latest_by_device.get(device)
            if current is None or int(row.get("id", 0)) > int(current.get("id", 0)):
                latest_by_device[device] = row

        judged = list(latest_by_device.values())
        qualified = sum(1 for row in judged if row.get("status") == "已合格")
        failed = sum(1 for row in judged if row.get("status") == "不合格")
        # 用整数百分比；没有已判定记录时给 0，而不是让前端拿到写死的假值。
        rate = round(qualified * 100 / len(judged)) if judged else 0
        return {
            "pending": pending,
            "qualified_rate": rate,
            "failed_devices": failed,
        }

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        """登记校准记录。

        返回 (记录, 缺失字段, 业务说明)：同一校准编号重复提交时直接返回已有记录，
        已停用设备拒绝登记，保证历史记录不被新记录覆盖。
        """
        clean = {field: str(values.get(field) or "").strip() for field in ALL_FIELDS}
        missing = [field for field in REQUIRED_FIELDS if not clean[field]]
        if missing:
            return None, missing, ""

        rows = store.rows(MODULE)
        duplicate = next((row for row in rows if str(row.get("校准编号") or "") == clean["校准编号"]), None)
        if duplicate is not None:
            # 幂等：重复提交（含网络重试/双击）回到同一条记录，不新增。
            return duplicate, [], "校准编号已存在，未重复登记"

        instrument = self._find_instrument(clean["关联设备"])
        if instrument is not None and instrument.get("status") == "已停用":
            return None, [], f"设备「{clean['关联设备']}」已停用，历史校准记录保留，不再登记新校准"

        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ALL_FIELDS:
            entry[field] = clean[field]
        entry["校准方式"] = self._normalize_method(clean["校准方式"])
        entry["校准日期"] = clean["校准日期"] or _today()
        entry["校准结果"] = "待判定"
        entry["校准状态"] = STATUS_ORDER[0]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, [], "校准记录已登记"

    # ------------------------------------------------------------------ 动作
    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"校准记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于校准记录可执行范围"

        values = values or {}
        target = ACTION_RULES[action]

        # 已停用设备的历史记录禁止再流转（动作接口同样不能覆盖它）。
        instrument = self._find_instrument(str(entry.get("关联设备") or ""))
        if instrument is not None and instrument.get("status") == "已停用" and entry.get("status") != target:
            return None, f"设备「{entry.get('关联设备')}」已停用，历史校准记录只能查看，不能再执行{action}"

        # 幂等：重复点同一个动作直接回当前记录，不重复消耗标准物质、不改判定日期。
        if entry.get("status") == target:
            return entry, f"校准记录已{action}，请勿重复操作"

        if action == "开始校准":
            return self._start(entry, instrument)
        if action == "判定合格":
            return self._judge(entry, instrument, qualified=True, values=values)
        return self._judge(entry, instrument, qualified=False, values=values)

    # ------------------------------------------------------------------ 内部
    def _start(
        self,
        entry: dict[str, Any],
        instrument: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, str]:
        method = self._normalize_method(str(entry.get("校准方式") or ""))
        entry["校准方式"] = method
        material = str(entry.get("标准物质") or "").strip()
        if method == LAB_METHOD:
            if not material:
                return None, "实验室校准必须先登记标准物质；现场校准则不需要"
            reagent = self._find_reagent(material)
            if reagent is None:
                return None, f"标准物质「{material}」在试剂物料台账中不存在，无法标记为已使用"
            # 标准物质一旦投入本次校准即从物料列表移除「未使用」身份，且只扣一次。
            reagent["status"] = REAGENT_USED_STATUS
            reagent["物料状态"] = REAGENT_USED_STATUS
            reagent["pending"] = False
            reagent["abnormal"] = False
            try:
                reagent["结存数量"] = max(0, int(reagent.get("结存数量") or 0) - 1)
            except (TypeError, ValueError):
                pass
        entry["status"] = STATUS_ORDER[1]
        entry["校准状态"] = STATUS_ORDER[1]
        entry["校准结果"] = "校准中"
        entry["pending"] = True
        entry["abnormal"] = False
        return entry, "校准记录已开始校准，标准物质已标记为使用" if method == LAB_METHOD else "校准记录已开始校准"

    def _judge(
        self,
        entry: dict[str, Any],
        instrument: dict[str, Any] | None,
        *,
        qualified: bool,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        method = self._normalize_method(str(entry.get("校准方式") or ""))
        entry["校准方式"] = method
        material = str(entry.get("标准物质") or "").strip()

        # 判定口径必须按校准方式走，不能一律按实验室口径：
        # 实验室校准要求标准物质已实际投入使用，现场校准没有这项要求。
        if qualified and method == LAB_METHOD:
            if not material:
                return None, "实验室校准缺少标准物质，不能判定合格；如为现场校准请修改校准方式"
            reagent = self._find_reagent(material)
            if reagent is None:
                return None, f"标准物质「{material}」未在试剂物料台账中登记，不能按实验室校准判定合格"
            if reagent.get("status") != REAGENT_USED_STATUS:
                return None, "实验室校准的标准物质尚未投入使用，请先执行开始校准"

        calibration_date = str(values.get("校准日期") or entry.get("校准日期") or "").strip() or _today()
        next_date = self._next_calibration_date(calibration_date, instrument) if qualified else calibration_date

        result = "合格" if qualified else "不合格"
        target = "已合格" if qualified else "不合格"
        entry["校准日期"] = calibration_date
        entry["下次校准日"] = next_date
        entry["校准结果"] = result
        entry["校准状态"] = target
        entry["status"] = target
        entry["pending"] = False
        entry["abnormal"] = not qualified

        self._sync_instrument(instrument, entry)
        return entry, f"校准记录已判定{result}，下次校准日 {next_date}"

    def _sync_instrument(
        self,
        instrument: dict[str, Any] | None,
        entry: dict[str, Any],
    ) -> None:
        """把已判定校准的结论同步到设备台账。

        只更新对应设备的那一行，不碰其他设备，更不会删除/覆盖任何校准历史行；
        已停用设备在调用前已被拦下，台账保持停用状态不动。
        """
        if instrument is None:
            return
        instrument["校准到期日"] = entry["下次校准日"]
        instrument["最近校准日期"] = entry["校准日期"]
        instrument["最近校准方式"] = entry["校准方式"]
        instrument["最近校准结果"] = entry["校准结果"]
        if entry["校准结果"] == "合格":
            instrument["status"] = "在运正常"
            instrument["设备状态"] = "在运正常"
            instrument["pending"] = False
            instrument["abnormal"] = False
        else:
            instrument["status"] = "故障停机"
            instrument["设备状态"] = "故障停机"
            instrument["pending"] = False
            instrument["abnormal"] = True

    def _find_instrument(self, device_ref: str) -> dict[str, Any] | None:
        for row in store.rows(INSTRUMENT_MODULE):
            if device_ref and device_ref in (
                str(row.get("设备编号") or ""),
                str(row.get("设备名称") or ""),
            ):
                return row
        return None

    def _find_reagent(self, material_ref: str) -> dict[str, Any] | None:
        for row in store.rows(REAGENT_MODULE):
            if material_ref and material_ref in (
                str(row.get("物料编号") or ""),
                str(row.get("物料名称") or ""),
            ):
                return row
        return None

    @staticmethod
    def _normalize_method(method: str) -> str:
        method = method.strip()
        return METHOD_ALIASES.get(method, LAB_METHOD if "实验" in method else FIELD_METHOD if "现场" in method or "在线" in method else method)

    @staticmethod
    def _next_calibration_date(calibration_date: str, instrument: dict[str, Any] | None) -> str:
        months = 12
        if instrument is not None:
            match = _PERIOD_RE.search(str(instrument.get("校准周期") or ""))
            if match:
                months = max(1, int(match.group(1)))
        try:
            start = date.fromisoformat(calibration_date)
        except ValueError:
            start = date.today()
        year = start.year + months // 12
        month = start.month + months % 12
        if month > 12:
            year += 1
            month -= 12
        # 月末日期顺延（如 3 月 31 日加 12 个月不会变成 3 月 1 日）。
        last_day = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        return date(year, month, min(start.day, last_day)).isoformat()


def _today() -> str:
    return date.today().isoformat()
