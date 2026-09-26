"""校准记录业务规则：状态流转、字段校验与筛选口径都收在这里。

校准域约定（校准列表、设备台账、合格率统计三处口径必须一致）：
- 校准方式只支持「实验室校准」「现场校准」：实验室校准判定合格必须消耗标准物质，
  现场校准不消耗标准物质；判定结论按各自方式走，不能混用。
- 判定合格/不合格时一次性写齐 校准结果、校准日期、下次校准日，并同步设备台账；
  合格与不合格都会排下一次校准日。
- 校准记录只追加不覆盖，校准编号唯一，重复提交直接拒绝。
- 已停用设备的历史校准记录保留，且新的判定结果不回写其台账。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "calibration"
INSTRUMENT_MODULE = "instrument"
REQUIRED_FIELDS = ["校准编号", "关联设备", "校准方式"]
CALIBRATION_METHODS = ["实验室校准", "现场校准"]
STATUS_ORDER = ["待校准", "校准中", "已合格", "不合格"]
ACTION_RULES = {"开始校准": "校准中", "判定合格": "已合格", "判定不合格": "不合格"}
JUDGE_ACTIONS = {"判定合格": "合格", "判定不合格": "不合格"}
NEGATIVE_ACTIONS = ["判定不合格"]

RESULT_FIELDS = ["标准物质", "校准结果", "校准日期", "下次校准日", "校准状态"]


def _today() -> date:
    return date.today()


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    # 目标月份天数不足时（如 2 月没有 31 日），落到该月最后一天。
    last_day = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


def _next_calibration_date(base: date, period_text: str) -> str:
    """按设备台账的「校准周期」推算下次校准日；识别不出周期时按默认 12 个月。"""
    match = re.search(r"(\d+)", str(period_text or ""))
    amount = int(match.group(1)) if match else 12
    text = str(period_text or "")
    if "日" in text or "天" in text:
        target = base + timedelta(days=amount)
    elif "年" in text:
        target = _add_months(base, amount * 12)
    else:  # 默认按月（含「个月」「月」和只写数字的情况）
        target = _add_months(base, amount)
    return target.isoformat()


class CalibrationService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self._with_view_fields(row) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("校准编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_view_fields(entry) if entry is not None else None

    def _with_view_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        """补展示派生字段：校准状态始终跟 status 对齐，标准物质使用状态查台账。"""
        view = dict(row)
        view["校准状态"] = row.get("status", STATUS_ORDER[0])
        material = str(row.get("标准物质") or "").strip()
        if material:
            view["标准物质状态"] = "已使用" if store.is_reference_material_used(material) else "未使用"
        else:
            view["标准物质状态"] = ""
        return view

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, [f"缺少必填字段：{'、'.join(missing)}"]

        code = str(values.get("校准编号") or "").strip()
        method = str(values.get("校准方式") or "").strip()
        if method not in CALIBRATION_METHODS:
            return None, [f"校准方式仅支持：{'、'.join(CALIBRATION_METHODS)}"]
        if store.find_by_field(MODULE, "校准编号", code) is not None:
            # 同一校准编号重复提交不能产生第二条记录，直接幂等拒绝。
            return None, [f"校准编号「{code}」已存在，请勿重复提交"]

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 现场校准不登记标准物质；实验室校准登记的物质在判定前保持「未使用」。
        material = str(values.get("标准物质") or "").strip()
        entry["标准物质"] = material if method == "实验室校准" else ""
        for field in ("校准结果", "校准日期", "下次校准日"):
            entry[field] = ""
        entry["status"] = STATUS_ORDER[0]
        entry["校准状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._with_view_fields(entry), []

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"校准记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于校准记录可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        method = str(entry.get("校准方式") or "").strip()
        if action in JUDGE_ACTIONS:
            # 校准结果必须按所选校准方式的口径判定：
            # 实验室校准必须挂标准物质并在判定时消耗；现场校准不消耗标准物质。
            if method == "实验室校准" and not str(entry.get("标准物质") or "").strip():
                return None, "实验室校准需先登记标准物质，无法直接判定结论"

        entry["status"] = target
        entry["校准状态"] = target
        entry["pending"] = target == STATUS_ORDER[0] or target == STATUS_ORDER[1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS

        if action in JUDGE_ACTIONS:
            self._apply_judgment(entry, action, values or {})

        return self._with_view_fields(entry), f"校准记录已{action}"

    def _apply_judgment(self, entry: dict[str, Any], action: str, values: dict[str, Any]) -> None:
        result_text = JUDGE_ACTIONS[action]
        entry["校准结果"] = result_text

        # 校准日期/下次校准日只在首次判定时落定，重复点击或改判不重排周期，
        # 保证同一校准编号的历史结论不被覆盖。
        cal_date = _parse_date(entry.get("校准日期")) or _parse_date(values.get("校准日期")) or _today()
        entry["校准日期"] = cal_date.isoformat()

        instrument = store.find_by_field(INSTRUMENT_MODULE, "设备编号", entry.get("关联设备"))
        period = str(instrument.get("校准周期") or "") if instrument else ""
        entry["下次校准日"] = _next_calibration_date(cal_date, period)

        if str(entry.get("校准方式") or "").strip() == "实验室校准":
            # 标准物质在判定消耗后登记为已使用，现场校准不进这个台账。
            store.mark_reference_material_used(entry.get("标准物质"))

        self._sync_instrument(instrument, entry)

    def _sync_instrument(self, instrument: dict[str, Any] | None, entry: dict[str, Any]) -> None:
        if instrument is None:
            return
        # 已停用设备只保留历史校准记录，新判定结果不回写台账、不复活设备状态。
        if instrument.get("status") == "已停用" or str(instrument.get("设备状态") or "").strip() == "已停用":
            return
        instrument["校准到期日"] = entry["下次校准日"]
        if entry["status"] == "已合格":
            instrument["status"] = "在运正常"
            instrument["设备状态"] = "在运正常"
            instrument["abnormal"] = False
        elif entry["status"] == "不合格":
            instrument["status"] = "故障停机"
            instrument["设备状态"] = "故障停机"
            instrument["abnormal"] = True
        instrument["pending"] = False

    def stats(self) -> dict[str, int | float | str]:
        """合格率口径：只统计已出结论（已合格/不合格）的记录，待校准与校准中不进分母。"""
        rows = store.rows(MODULE)
        pending = sum(1 for row in rows if row.get("status") in {"待校准", "校准中"})
        qualified = sum(1 for row in rows if row.get("status") == "已合格")
        judged = sum(1 for row in rows if row.get("status") in {"已合格", "不合格"})
        rate = round(qualified * 100 / judged, 1) if judged else 0.0

        # 不合格设备按设备去重：取每台设备最近一条已判定记录的结论。
        latest_by_device: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("status") not in {"已合格", "不合格"}:
                continue
            device = str(row.get("关联设备") or "").strip()
            if not device:
                continue
            old = latest_by_device.get(device)
            if old is None or str(row.get("校准日期") or "") >= str(old.get("校准日期") or ""):
                latest_by_device[device] = row
        failed_devices = sum(1 for row in latest_by_device.values() if row.get("status") == "不合格")

        return {
            "待校准记录": pending,
            "校准合格率": rate,
            "不合格设备": failed_devices,
        }
