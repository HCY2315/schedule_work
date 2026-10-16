# -*- coding: utf-8 -*-
"""自动排班核心：逐日轮动、权限匹配、公平性、顶班建议、统计。"""
import datetime
import json
import sqlite3
from collections import Counter

from db import (
    get_conn,
    att_kinds_map,
)


def norm_kind(kind):
    return kind if kind in att_kinds_map() else "normal"


def weight_of(kind):
    return att_kinds_map()[norm_kind(kind)]["weight"]


def label_of(kind):
    return att_kinds_map()[norm_kind(kind)]["label"]


def is_absent(kind):
    return att_kinds_map()[norm_kind(kind)]["unavailable"]


def _fromiso(date):
    try:
        return datetime.date.fromisoformat(date)
    except AttributeError:  # Python < 3.7
        return datetime.datetime.strptime(date, "%Y-%m-%d").date()


def now_year():
    return datetime.date.today().year


def load_person(conn, pid):
    r = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["perm_ids"] = json.loads(d["perm_ids"] or "[]")
    return d


def load_roster(conn, roster_id):
    out = []
    for r in conn.execute(
        "SELECT * FROM persons WHERE roster_id=? ORDER BY id", (roster_id,)
    ):
        d = dict(r)
        d["perm_ids"] = json.loads(d["perm_ids"] or "[]")
        out.append(d)
    return out


def load_task(conn, task_id):
    t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        return None
    task = dict(t)
    slots = []
    for s in conn.execute("SELECT * FROM slots WHERE task_id=?", (task_id,)):
        sd = dict(s)
        sd["positions"] = [
            dict(p)
            for p in conn.execute(
                "SELECT * FROM positions WHERE slot_id=? ORDER BY id", (sd["id"],)
            )
        ]
        slots.append(sd)
    slots.sort(key=lambda x: _tomin(x["start"]))
    task["slots"] = slots
    return task


def _tomin(hhmm):
    hhmm = (hhmm or "").strip()
    if hhmm == "24:00":
        return 24 * 60
    parts = hhmm.split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
    except Exception:
        return 0
    return h * 60 + m


def slot_range(slot):
    a = _tomin(slot["start"])
    b = _tomin(slot["end"])
    if b <= a:
        b = a + 1
    return a, b


def _overlaps(r1, r2):
    a1, b1 = _tomin(r1[0]), _tomin(r1[1])
    a2, b2 = _tomin(r2[0]), _tomin(r2[1])
    if b1 <= a1:
        b1 = a1 + 1
    if b2 <= a2:
        b2 = a2 + 1
    return a1 < b2 and a2 < b1


def busy_intervals(conn, person_id, date, exclude_task_id=None, exclude_position_id=None):
    out = []
    q = (
        "SELECT s.start, s.\"end\", sc.task_id, sc.position_id "
        "FROM schedules sc JOIN slots s ON s.id = sc.slot_id "
        "WHERE sc.person_id=? AND sc.date=?"
    )
    for r in conn.execute(q, (person_id, date)):
        if (
            exclude_task_id is not None
            and r["task_id"] == exclude_task_id
            and r["position_id"] == exclude_position_id
        ):
            continue
        out.append((r["start"], r["end"], r["task_id"], r["position_id"]))
    return out


def free_at(conn, person_id, date, start, end, exclude_task_id=None, exclude_position_id=None):
    for b in busy_intervals(conn, person_id, date, exclude_task_id, exclude_position_id):
        if _overlaps((start, end), (b[0], b[1])):
            return False
    return True


def busy_any(conn, person_id, date, exclude_task_id=None, exclude_position_id=None):
    """当天是否已有任何排班（每人每天只上一班）。"""
    return len(busy_intervals(conn, person_id, date, exclude_task_id, exclude_position_id)) > 0


def attendance_kind(conn, person_id, date):
    r = conn.execute(
        "SELECT kind FROM attendance WHERE person_id=? AND date=?", (person_id, date)
    ).fetchone()
    return r["kind"] if r else None


def row_weight(conn, person_id, date):
    return weight_of(attendance_kind(conn, person_id, date))


def year_scores(conn, year=None):
    if year is None:
        year = now_year()
    y0 = "%04d-01-01" % year
    y1 = "%04d-12-31" % year
    result = {}
    rows = conn.execute(
        "SELECT person_id, date FROM schedules WHERE date>=? AND date<=? ORDER BY date",
        (y0, y1),
    ).fetchall()
    att = {
        (a["person_id"], a["date"]): a["kind"]
        for a in conn.execute(
            "SELECT person_id, date, kind FROM attendance WHERE date>=? AND date<=?", (y0, y1)
        )
    }
    for r in rows:
        k = norm_kind(att.get((r["person_id"], r["date"])))
        d = result.setdefault(r["person_id"], {kk: 0 for kk in att_kinds_map()})
        d.setdefault("total", 0.0)
        d[k] += 1
        d["total"] += weight_of(k)
    return result


def person_total(score_map, pid):
    d = score_map.get(pid)
    return d["total"] if d else 0.0


# ---------------------------------------------------------------- 排班生成


def _earlier_slot(slot_id, slot_ids):
    i = slot_ids.index(slot_id)
    return slot_ids[(i - 1) % len(slot_ids)]


def _prev_assignments(conn, task_id, date):
    rows = conn.execute(
        "SELECT person_id, slot_id FROM schedules "
        "WHERE task_id=? AND date<? ORDER BY date, id",
        (task_id, date),
    ).fetchall()
    prev = {}
    for r in rows:
        prev[r["person_id"]] = r["slot_id"]
    return prev


def _yesterday_slots(conn, task_id, date):
    yd = (_fromiso(date) - datetime.timedelta(days=1)).isoformat()
    rows = conn.execute(
        "SELECT person_id, slot_id FROM schedules WHERE task_id=? AND date=?",
        (task_id, yd),
    ).fetchall()
    return {r["person_id"]: r["slot_id"] for r in rows}


def _perm_ok(person, position):
    pid = position.get("perm_id")
    if pid is None:
        return True
    return pid in (person.get("perm_ids") or [])


def _unavailable(conn, person_id, date):
    return is_absent(attendance_kind(conn, person_id, date))


def generate_month(task_id, year, month):
    conn = get_conn()
    try:
        task = load_task(conn, task_id)
        if not task:
            raise ValueError("任务不存在")
        if not task["slots"]:
            raise ValueError("任务尚未配置班次时段")
        roster = load_roster(conn, task["roster_id"])
        if not roster:
            raise ValueError("任务所选名单为空，请先录入人员")

        start = datetime.date(year, month, 1)
        if month == 12:
            end = datetime.date(year + 1, 1, 1)
        else:
            end = datetime.date(year, month + 1, 1)
        dates = []
        cur = start
        while cur < end:
            dates.append(cur.isoformat())
            cur += datetime.timedelta(days=1)

        conn.execute(
            "DELETE FROM schedules WHERE task_id=? AND date>=? AND date<?",
            (task_id, dates[0], dates[-1] + "zz"),
        )

        slot_ids = [s["id"] for s in task["slots"]]
        gen_count = Counter()
        warnings = []
        for date in dates:
            warnings += _generate_day(conn, task, roster, date, slot_ids, gen_count)
        conn.commit()
        return {"days": len(dates), "warnings": warnings[:50]}
    finally:
        conn.close()


def _generate_day(conn, task, roster, date, slot_ids, gen_count):
    """当日贪心排班。

    与旧实现的关键区别：候选集是"全体可用人员"，而不是"以往排过班的人"。
    候选排序键（越小越优先）：
        本月已排次数 → 偏好匹配 → 轮动目标 → 年度总分 → 人员ID
    本月已排次数做主键保证同月内人人均分（不再出现某人整月 0 班）；
    年度总分做次键保证跨月公平。岗位按"可选人数"从少到多处理，
    优先满足权限受限的岗位，避免被普通岗位抢占唯一合格人选。
    """
    slots = task["slots"]
    slot_by_id = {s["id"]: s for s in slots}
    warnings = []

    prev = _prev_assignments(conn, task["id"], date)
    yslots = _yesterday_slots(conn, task["id"], date)
    scores = year_scores(conn)
    busy_today = {
        r["person_id"]
        for r in conn.execute("SELECT DISTINCT person_id FROM schedules WHERE date=?", (date,))
    }
    available = [p for p in roster if not _unavailable(conn, p["id"], date)]
    assigned = set()

    def rotation_rank(pid, sid):
        last = prev.get(pid)
        if last is None or last not in slot_ids or last == sid:
            return 1
        return 0 if _earlier_slot(last, slot_ids) == sid else 2

    def eligible(p, sid, pos):
        pid = p["id"]
        if pid in assigned or pid in busy_today:
            return False
        if not _perm_ok(p, pos):
            return False
        return yslots.get(pid) != sid

    def key(p, sid, pos):
        match = 0 if (p.get("preferred_perm_id") and p["preferred_perm_id"] == pos.get("perm_id")) else 1
        return (
            gen_count[p["id"]],
            match,
            rotation_rank(p["id"], sid),
            person_total(scores, p["id"]),
            p["id"],
        )

    jobs = []
    for sid in slot_ids:
        for pos in slot_by_id[sid]["positions"]:
            n = sum(1 for p in available if eligible(p, sid, pos))
            jobs.append((n, sid, pos))
    jobs.sort(key=lambda x: x[0])

    for _, sid, pos in jobs:
        cands = sorted((p for p in available if eligible(p, sid, pos)), key=lambda p: key(p, sid, pos))
        if not cands:
            slot = slot_by_id[sid]
            warnings.append(
                "%s %s-%s 岗位「%s」无人可排" % (date, slot["start"], slot["end"], pos["name"])
            )
            continue
        p = cands[0]
        assigned.add(p["id"])
        gen_count[p["id"]] += 1
        _insert(conn, task["id"], date, sid, pos["id"], p["id"], None)
    conn.commit()
    return warnings


def _insert(conn, task_id, date, slot_id, position_id, person_id, cover_of):
    try:
        conn.execute(
            "INSERT INTO schedules(task_id, date, slot_id, position_id, person_id, cover_of) "
            "VALUES(?,?,?,?,?,?)",
            (task_id, date, slot_id, position_id, person_id, cover_of),
        )
    except sqlite3.IntegrityError:
        pass


# ---------------------------------------------------------------- 顶班


def suggest_covers(conn, task_id, date, position_id, limit=None):
    """顶班候选（返回契约）：名单内所有具备岗位权限、当天非不在岗者。

    每项含 busy/risky/eligible，并把当前可顶班且今年总班次最少者标 suggested。
    排序：建议 → 其他可顶班 → 不可顶班，组内按 (total, person_id)。
    """
    pos_row = conn.execute(
        "SELECT * FROM positions WHERE id=?", (position_id,)
    ).fetchone()
    if not pos_row:
        return []
    pos = dict(pos_row)
    slot = conn.execute("SELECT * FROM slots WHERE id=?", (pos["slot_id"],)).fetchone()
    slot = dict(slot)
    if slot["task_id"] != task_id:
        return []

    roster = load_roster(conn, conn.execute(
        "SELECT roster_id FROM tasks WHERE id=?", (task_id,)
    ).fetchone()["roster_id"])
    yslots = _yesterday_slots(conn, task_id, date)
    scores = year_scores(conn)
    base_row = conn.execute(
        "SELECT person_id FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NULL",
        (task_id, date, position_id),
    ).fetchone()
    base_pid = base_row["person_id"] if base_row else None

    out = []
    for p in roster:
        if p["id"] == base_pid:
            continue
        if _unavailable(conn, p["id"], date):
            continue
        if not _perm_ok(p, pos):
            continue
        busy = busy_any(conn, p["id"], date)
        risky = yslots.get(p["id"]) == slot["id"]
        out.append(
            {
                "person_id": p["id"],
                "name": p["name"],
                "code": p["code"],
                "total": person_total(scores, p["id"]),
                "busy": busy,
                "risky": risky,
                "eligible": not busy and not risky,
                "suggested": False,
            }
        )

    free = [c for c in out if c["eligible"]]
    if free:
        best = min(free, key=lambda x: (x["total"], x["person_id"]))
        best["suggested"] = True

    out.sort(key=lambda x: (not x["suggested"], not x["eligible"], x["total"], x["person_id"]))
    return out[:limit] if limit else out


def apply_cover(conn, task_id, date, position_id, person_id):
    row = conn.execute(
        "SELECT * FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NULL",
        (task_id, date, position_id),
    ).fetchone()
    if not row:
        raise ValueError("该岗位当天没有需要顶替的排班")
    base_pid = row["person_id"]
    if person_id == base_pid:
        raise ValueError("不能安排本人顶自己的班")
    if _unavailable(conn, person_id, date):
        raise ValueError("该人员当天已登记不在岗")
    covers = conn.execute(
        "SELECT * FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of=? "
        "ORDER BY id",
        (task_id, date, position_id, base_pid),
    ).fetchall()
    if covers:
        latest = covers[-1]
        if not _unavailable(conn, latest["person_id"], date):
            raise ValueError("该岗位已有顶班人员")
        if person_id == latest["person_id"]:
            raise ValueError("该人员已是当前顶班人")
    p = load_person(conn, person_id)
    if p is None:
        raise ValueError("人员不存在")
    pos = dict(conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone())
    if not _perm_ok(p, pos):
        raise ValueError("该人员没有此岗位所需权限")
    slot = dict(conn.execute("SELECT * FROM slots WHERE id=?", (row["slot_id"],)).fetchone())
    # 允许选择当天已排人员：同任务内自动移入本岗位，跨任务仍按「每人每天一班」拦截；昨天同班次不再硬拦截。
    for b in busy_intervals(conn, person_id, date):
        if b[2] != task_id:
            raise ValueError("该人员当天已在其他任务排班")
    conn.execute(
        "DELETE FROM schedules WHERE person_id=? AND date=? AND task_id=?",
        (person_id, date, task_id),
    )
    conn.execute(
        "INSERT INTO schedules(task_id, date, slot_id, position_id, person_id, cover_of) "
        "VALUES(?,?,?,?,?,?)",
        (task_id, date, row["slot_id"], position_id, person_id, base_pid),
    )
    conn.commit()


def remove_cover(conn, task_id, date, position_id, base_person_id):
    conn.execute(
        "DELETE FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of=?",
        (task_id, date, position_id, base_person_id),
    )
    conn.commit()
