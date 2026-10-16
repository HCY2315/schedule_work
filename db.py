# -*- coding: utf-8 -*-
import os
import sqlite3
import sys


def _base_dir():
    """运行目录：源码=本目录；PyInstaller=可执行文件所在目录；pyz=压缩包所在目录。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return os.path.dirname(os.path.abspath(sys.executable))
    p = os.path.abspath(__file__)
    marker = ".pyz" + os.sep
    if marker in p:  # zipapp 单文件运行
        return os.path.dirname(p.split(marker)[0] + ".pyz")
    return os.path.dirname(p)


def _pyz_file():
    p = os.path.abspath(__file__)
    marker = ".pyz" + os.sep
    if marker in p:
        return p.split(marker)[0] + ".pyz"
    return None


BASE_DIR = _base_dir()
PYZ_FILE = _pyz_file()
DB_PATH = os.path.join(BASE_DIR, "data.db")

DEFAULT_PERMISSIONS = ["A", "B", "C", "D", "E"]
DEFAULT_CATEGORIES = [
    # (名称, 默认权限索引列表, 默认偏好权限索引)
    ("一类", [0, 1, 2, 3, 4], 0),
    ("二类", [1, 2, 3, 4], 1),
    ("三类", [2, 3, 4], 2),
    ("四类", [3, 4], 3),
    ("五类", [4], 4),
    ("六类", [4], 4),
]

SLOT_DEFAULT_TEMPLATE = [(i * 2, i * 2 + 2) for i in range(12)]

# 考勤类型「默认值」：仅用于首次建库时写入 att_kinds 表作为初始配置。
# 运行期的可编辑配置以数据库 att_kinds 表为准（可在「设置」页增删改），表结构见 init_db。
DEFAULT_ATT_KINDS = {
    "normal": {"label": "正常", "weight": 1.0, "unavailable": False},
    "a": {"label": "A事", "weight": 0.8, "unavailable": True},
    "b": {"label": "B事", "weight": 0.5, "unavailable": True},
    "absent": {"label": "缺勤", "weight": 0.0, "unavailable": True},
}


_KINDS_CACHE = None
_SETTINGS_CACHE = None


def _read_kinds(conn):
    out = {}
    for r in conn.execute(
        "SELECT kind, label, weight, unavailable FROM att_kinds ORDER BY sort, kind"
    ):
        out[r["kind"]] = {
            "label": r["label"],
            "weight": r["weight"],
            "unavailable": bool(r["unavailable"]),
        }
    return out


def att_kinds_map(conn=None):
    """运行期考勤类型配置（内存缓存）。首次访问或配置变更后从 att_kinds 表加载。"""
    global _KINDS_CACHE
    if _KINDS_CACHE is None:
        reload_att_kinds(conn)
    return _KINDS_CACHE


def reload_att_kinds(conn=None):
    global _KINDS_CACHE
    own = conn is None
    c = conn or get_conn()
    try:
        loaded = _read_kinds(c)
    finally:
        if own:
            c.close()
    _KINDS_CACHE = loaded or {k: dict(v) for k, v in DEFAULT_ATT_KINDS.items()}
    return _KINDS_CACHE


def save_att_kinds(conn, items):
    conn.execute("DELETE FROM att_kinds")
    for i, it in enumerate(items):
        conn.execute(
            "INSERT INTO att_kinds(kind, label, weight, unavailable, sort) VALUES(?,?,?,?,?)",
            (it["kind"], it["label"], float(it["weight"]), 1 if it["unavailable"] else 0, i),
        )
    conn.commit()
    return reload_att_kinds(conn)


def norm_kind(kind):
    return kind if kind in att_kinds_map() else "normal"


DEFAULT_SETTINGS = {
    "multi_shift": "0",
}


def settings_map():
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is None:
        conn = get_conn()
        try:
            loaded = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        finally:
            conn.close()
        _SETTINGS_CACHE = dict(DEFAULT_SETTINGS, **loaded)
    return _SETTINGS_CACHE


def get_setting(key, default=None):
    if default is None:
        default = DEFAULT_SETTINGS.get(key, "")
    return settings_map().get(key, default)


def set_setting(key, value):
    global _SETTINGS_CACHE
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
    finally:
        conn.close()
    _SETTINGS_CACHE = None


def multi_shift_enabled():
    return get_setting("multi_shift", "0").lower() in ("1", "true", "yes", "on")


def hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # per-connection pragmas（journal_mode 是持久化的，放在 init_db 里执行一次）
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db():
    conn = get_conn()
    # WAL：读写可并发，写锁粒度从库级降为事务级，多人同时保存不易 "database is locked"
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS permissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            perm_ids TEXT NOT NULL DEFAULT '[]',
            preferred_perm_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS rosters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS persons(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roster_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            category_id INTEGER,
            perm_ids TEXT NOT NULL DEFAULT '[]',
            preferred_perm_id INTEGER,
            remark TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            roster_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS slots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            idx INTEGER NOT NULL,
            start TEXT NOT NULL,
            "end" TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS positions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            perm_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS schedules(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            slot_id INTEGER NOT NULL,
            position_id INTEGER NOT NULL,
            person_id INTEGER NOT NULL,
            cover_of INTEGER,
            UNIQUE(task_id, date, position_id, person_id)
        );
        CREATE TABLE IF NOT EXISTS attendance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'absent',
            reason TEXT DEFAULT '',
            UNIQUE(person_id, date)
        );
        CREATE TABLE IF NOT EXISTS att_kinds(
            kind TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0,
            unavailable INTEGER NOT NULL DEFAULT 0,
            sort INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_sched_date       ON schedules(date);
        CREATE INDEX IF NOT EXISTS idx_sched_person_dt  ON schedules(person_id, date);
        CREATE INDEX IF NOT EXISTS idx_sched_task_dt    ON schedules(task_id, date);
        CREATE INDEX IF NOT EXISTS idx_sched_slot       ON schedules(slot_id);
        CREATE INDEX IF NOT EXISTS idx_sched_position   ON schedules(position_id);
        CREATE INDEX IF NOT EXISTS idx_sched_date_pos   ON schedules(date, position_id);
        CREATE INDEX IF NOT EXISTS idx_att_date         ON attendance(date);
        CREATE INDEX IF NOT EXISTS idx_att_person_dt    ON attendance(person_id, date);
        CREATE INDEX IF NOT EXISTS idx_persons_roster   ON persons(roster_id);
        CREATE INDEX IF NOT EXISTS idx_slots_task       ON slots(task_id);
        CREATE INDEX IF NOT EXISTS idx_positions_slot   ON positions(slot_id);
        """
    )
    _seed(conn)
    reload_att_kinds(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    n = conn.execute("SELECT COUNT(*) c FROM permissions").fetchone()["c"]
    if n == 0:
        for i, name in enumerate(DEFAULT_PERMISSIONS):
            conn.execute(
                "INSERT INTO permissions(name, sort) VALUES(?, ?)", (name, i)
            )
    n = conn.execute("SELECT COUNT(*) c FROM categories").fetchone()["c"]
    if n == 0:
        perms = conn.execute(
            "SELECT id FROM permissions ORDER BY sort, id"
        ).fetchall()
        perm_ids = [p["id"] for p in perms]
        for name, idxs, pref in DEFAULT_CATEGORIES:
            ids = [perm_ids[i] for i in idxs if i < len(perm_ids)]
            pid = perm_ids[pref] if pref < len(perm_ids) else None
            conn.execute(
                "INSERT INTO categories(name, perm_ids, preferred_perm_id) VALUES(?,?,?)",
                (name, _dumps(ids), pid),
            )
    n = conn.execute("SELECT COUNT(*) c FROM att_kinds").fetchone()["c"]
    if n == 0:
        for i, (kind, v) in enumerate(DEFAULT_ATT_KINDS.items()):
            conn.execute(
                "INSERT INTO att_kinds(kind, label, weight, unavailable, sort) VALUES(?,?,?,?,?)",
                (kind, v["label"], v["weight"], 1 if v["unavailable"] else 0, i),
            )


def _dumps(v):
    import json

    return json.dumps(v, ensure_ascii=False)


def _loads(s, default=None):
    import json

    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except Exception:
        return default if default is not None else []


def rows_to_dicts(rows):
    return [dict(r) for r in rows]
