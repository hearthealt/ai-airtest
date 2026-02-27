# -*- encoding=utf8 -*-
"""应用导航结构的SQLite缓存管理器。"""

import os
import sqlite3
import logging
from typing import List, Optional

from .models import MenuItemInfo, MenuStructure

logger = logging.getLogger(__name__)


class NavCacheDB:
    """应用导航结构的SQLite缓存，用于跳过重复的AI发现阶段。"""

    def __init__(self, db_path: str):
        """连接数据库，自动建表。"""
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"导航缓存数据库已连接: {db_path}")

    def _create_tables(self):
        """创建表和索引（如不存在）"""
        cur = self.conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS app_nav_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_package TEXT NOT NULL,
                l_class TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL,
                item_level INTEGER NOT NULL,
                parent_name TEXT DEFAULT '',
                element_text TEXT DEFAULT '',
                element_name TEXT DEFAULT '',
                coord_x REAL NOT NULL,
                coord_y REAL NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(app_package, l_class, item_name, item_level, parent_name)
            );

            CREATE TABLE IF NOT EXISTS block_test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_package TEXT NOT NULL,
                l_class TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_level INTEGER NOT NULL,
                parent_name TEXT DEFAULT '',
                result TEXT NOT NULL,
                description TEXT DEFAULT '',
                screenshot_path TEXT DEFAULT '',
                tested_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS popup_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_package TEXT NOT NULL,
                l_class TEXT NOT NULL DEFAULT '',
                button_text TEXT NOT NULL,
                coord_x REAL NOT NULL,
                coord_y REAL NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(app_package, l_class, button_text)
            );

            CREATE INDEX IF NOT EXISTS idx_nav_app ON app_nav_cache(app_package, l_class);
            CREATE INDEX IF NOT EXISTS idx_result_app_class ON block_test_results(app_package, l_class);
            CREATE INDEX IF NOT EXISTS idx_popup_app ON popup_cache(app_package, l_class);
        """)
        self.conn.commit()

    def has_cache(self, app_package: str, l_class: str) -> bool:
        """该应用+小类是否有缓存的导航数据（至少有L1记录）"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM app_nav_cache WHERE app_package=? AND l_class=? AND item_level=1",
            (app_package, l_class)
        )
        count = cur.fetchone()[0]
        return count > 0

    def load_menu_structure(self, app_package: str, l_class: str) -> MenuStructure:
        """从数据库加载MenuStructure（L1列表 + 每个L1的L2列表）"""
        menu = MenuStructure()
        cur = self.conn.cursor()

        # 加载L1
        cur.execute(
            "SELECT * FROM app_nav_cache WHERE app_package=? AND l_class=? AND item_level=1 ORDER BY sort_order",
            (app_package, l_class)
        )
        for row in cur.fetchall():
            menu.l1_items.append(MenuItemInfo(
                name=row["item_name"],
                element_text=row["element_text"],
                element_name=row["element_name"],
                coordinates=(row["coord_x"], row["coord_y"]),
                level=1,
            ))

        # 加载每个L1的L2
        for l1 in menu.l1_items:
            cur.execute(
                "SELECT * FROM app_nav_cache WHERE app_package=? AND l_class=? AND item_level=2 AND parent_name=? ORDER BY sort_order",
                (app_package, l_class, l1.name)
            )
            l2_items = []
            for row in cur.fetchall():
                l2_items.append(MenuItemInfo(
                    name=row["item_name"],
                    element_text=row["element_text"],
                    element_name=row["element_name"],
                    coordinates=(row["coord_x"], row["coord_y"]),
                    level=2,
                ))
            menu.l2_map[l1.name] = l2_items

        return menu

    def save_menu_structure(self, app_package: str, l_class: str, menu: MenuStructure):
        """保存/更新整个MenuStructure到数据库（REPLACE方式）"""
        cur = self.conn.cursor()

        # 保存L1
        for i, l1 in enumerate(menu.l1_items):
            cur.execute("""
                INSERT OR REPLACE INTO app_nav_cache
                (app_package, l_class, item_name, item_level, parent_name, element_text, element_name, coord_x, coord_y, sort_order, updated_at)
                VALUES (?, ?, ?, 1, '', ?, ?, ?, ?, ?, datetime('now','localtime'))
            """, (app_package, l_class, l1.name, l1.element_text, l1.element_name,
                  l1.coordinates[0], l1.coordinates[1], i))

        # 保存L2
        for l1_name, l2_items in menu.l2_map.items():
            for j, l2 in enumerate(l2_items):
                cur.execute("""
                    INSERT OR REPLACE INTO app_nav_cache
                    (app_package, l_class, item_name, item_level, parent_name, element_text, element_name, coord_x, coord_y, sort_order, updated_at)
                    VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                """, (app_package, l_class, l2.name, l1_name, l2.element_text, l2.element_name,
                      l2.coordinates[0], l2.coordinates[1], j))

        self.conn.commit()
        total = len(menu.l1_items) + sum(len(v) for v in menu.l2_map.values())
        logger.info(f"导航缓存已保存: {app_package} l_class={l_class} ({total}条记录)")

    def update_l2_for_l1(self, app_package: str, l_class: str, l1_name: str, l2_items: List[MenuItemInfo]):
        """更新某个L1下的L2列表（AI重新发现后调用）"""
        cur = self.conn.cursor()

        # 先删除该L1下的旧L2
        cur.execute(
            "DELETE FROM app_nav_cache WHERE app_package=? AND l_class=? AND item_level=2 AND parent_name=?",
            (app_package, l_class, l1_name)
        )

        # 插入新L2
        for j, l2 in enumerate(l2_items):
            cur.execute("""
                INSERT INTO app_nav_cache
                (app_package, l_class, item_name, item_level, parent_name, element_text, element_name, coord_x, coord_y, sort_order)
                VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?)
            """, (app_package, l_class, l2.name, l1_name, l2.element_text, l2.element_name,
                  l2.coordinates[0], l2.coordinates[1], j))

        self.conn.commit()
        logger.info(f"缓存已更新: {app_package} l_class={l_class} L1'{l1_name}'的L2({len(l2_items)}个)")

    def delete_cache(self, app_package: str, l_class: str):
        """删除某应用某小类的全部缓存（强制下次重新发现）"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM app_nav_cache WHERE app_package=? AND l_class=?", (app_package, l_class))
        self.conn.commit()
        logger.info(f"缓存已删除: {app_package} l_class={l_class}")

    def save_test_result(self, app_package: str, l_class: str,
                         item_name: str, item_level: int, parent_name: str,
                         result: str, description: str, screenshot_path: str):
        """记录一条测试结果"""
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO block_test_results
            (app_package, l_class, item_name, item_level, parent_name, result, description, screenshot_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (app_package, l_class, item_name, item_level, parent_name,
              result, description, screenshot_path))
        self.conn.commit()

    def get_last_results(self, app_package: str, l_class: str) -> List[dict]:
        """获取某应用某规则的最近测试结果"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM block_test_results WHERE app_package=? AND l_class=? ORDER BY tested_at DESC",
            (app_package, l_class)
        )
        return [dict(row) for row in cur.fetchall()]

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()

    # ==================== 弹窗缓存 ====================

    def save_popup(self, app_package: str, l_class: str, button_text: str, coord_x: float, coord_y: float):
        """缓存一个弹窗按钮（点击成功后调用）"""
        cur = self.conn.cursor()
        # 检查是否已存在，已存在则只更新坐标，不改变顺序
        cur.execute(
            "SELECT sort_order FROM popup_cache WHERE app_package=? AND l_class=? AND button_text=?",
            (app_package, l_class, button_text)
        )
        row = cur.fetchone()
        if row:
            cur.execute("""
                UPDATE popup_cache SET coord_x=?, coord_y=?
                WHERE app_package=? AND l_class=? AND button_text=?
            """, (coord_x, coord_y, app_package, l_class, button_text))
        else:
            # 新弹窗，排到最后
            cur.execute("SELECT COUNT(*) FROM popup_cache WHERE app_package=? AND l_class=?", (app_package, l_class))
            order = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO popup_cache
                (app_package, l_class, button_text, coord_x, coord_y, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (app_package, l_class, button_text, coord_x, coord_y, order))
        self.conn.commit()
        logger.info(f"弹窗缓存已保存: {app_package} l_class={l_class} '{button_text}' ({coord_x:.3f}, {coord_y:.3f})")

    def load_popups(self, app_package: str, l_class: str) -> List[dict]:
        """加载某应用某小类缓存的弹窗按钮列表（按出现顺序排列）"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT button_text, coord_x, coord_y FROM popup_cache WHERE app_package=? AND l_class=? ORDER BY sort_order",
            (app_package, l_class)
        )
        return [dict(row) for row in cur.fetchall()]

    def has_popups(self, app_package: str, l_class: str) -> bool:
        """该应用某小类是否有缓存的弹窗数据"""
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM popup_cache WHERE app_package=? AND l_class=?", (app_package, l_class))
        return cur.fetchone()[0] > 0

    def delete_popups(self, app_package: str, l_class: str):
        """删除某应用某小类的弹窗缓存"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM popup_cache WHERE app_package=? AND l_class=?", (app_package, l_class))
        self.conn.commit()
        logger.info(f"弹窗缓存已删除: {app_package} l_class={l_class}")
