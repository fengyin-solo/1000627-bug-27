"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 已在已判定的实验室校准中消耗过的标准物质，按名称去重登记。
        self._used_reference_materials: set[str] = set()
        for row in self._tables.get("calibration", []):
            if row.get("status") in {"已合格", "不合格"} and str(row.get("校准方式") or "") == "实验室校准":
                self.mark_reference_material_used(row.get("标准物质"))

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        """按业务编号字段查唯一记录（如校准编号、设备编号），用于去重与关联回写。"""
        text = str(value or "").strip()
        if not text:
            return None
        for row in self.rows(module):
            if str(row.get(field) or "").strip() == text:
                return row
        return None

    # 标准物质使用台账：同一种标准物质只要在某次已判定的实验室校准中用过，就登记为已使用。
    def mark_reference_material_used(self, name: Any) -> None:
        text = str(name or "").strip()
        if text:
            self._used_reference_materials.add(text)

    def is_reference_material_used(self, name: Any) -> bool:
        return str(name or "").strip() in self._used_reference_materials

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
