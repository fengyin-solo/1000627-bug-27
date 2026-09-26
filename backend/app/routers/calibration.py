"""校准记录接口：维护校准记录，覆盖开始校准、判定合格、判定不合格等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.calibration import CalibrationService

router = APIRouter(prefix="/api/calibration", tags=["校准记录"])

service = CalibrationService()

LIST_FIELDS = ["校准编号", "关联设备", "校准方式", "标准物质", "标准物质状态", "校准结果", "校准日期", "下次校准日", "校准状态"]
STATUSES = ["待校准", "校准中", "已合格", "不合格"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按校准编号检索"),
    status: str | None = Query(default=None, description="待校准、校准中、已合格、不合格"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按校准编号与状态过滤校准记录列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats")
def calibration_stats() -> dict[str, Any]:
    """校准统计：待校准记录数、校准合格率（只算已判定记录）、不合格设备数（按设备去重）。"""
    return service.stats()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出校准记录清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "calibration", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条校准记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"校准记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条校准记录，缺字段、编号重复或校准方式非法时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    return ActionResult(ok=True, message="校准记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条校准记录执行开始校准、判定合格、判定不合格；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
