# -*- coding: utf-8 -*-
"""本地 Web 服务：Python 标准库实现，麒麟系统 Python3 可直接运行。"""
import calendar
import datetime
import json
import os
import re
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:  # Python < 3.7
    from http.server import HTTPServer
    from socketserver import ThreadingMixIn

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

from urllib.parse import urlparse, parse_qs

from db import get_conn, init_db, _dumps, _loads, PYZ_FILE
import db
import scheduler as sc


def att_kinds():
    return [
        {"kind": k, "label": v["label"], "weight": v["weight"], "unavailable": v["unavailable"]}
        for k, v in db.att_kinds_map().items()
    ]


def absent_labels():
    return "/".join(v["label"] for v in db.att_kinds_map().values() if v["unavailable"])


def _fromiso(date):
    try:
        return datetime.date.fromisoformat(date)
    except AttributeError:  # Python < 3.7
        return datetime.datetime.strptime(date, "%Y-%m-%d").date()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.environ.get("WORKDAY_PORT", "8686"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

TIME_RE = re.compile(r"^([01]?\d|2[0-4]):[0-5]\d$")


def _hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def _ok(data=None):
    return 200, data if data is not None else {"ok": True}


def _err(msg, code=400):
    return code, {"error": str(msg)}


def _int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 权限/类别


def get_permissions():
    conn = get_conn()
    try:
        return _ok(sc_rows(conn.execute("SELECT * FROM permissions ORDER BY sort, id")))
    finally:
        conn.close()


def post_permissions(body):
    name = (body.get("name") or "").strip()
    if not name:
        return _err("权限名称不能为空")
    conn = get_conn()
    try:
        mx = conn.execute("SELECT COALESCE(MAX(sort),-1) m FROM permissions").fetchone()["m"]
        cur = conn.execute("INSERT INTO permissions(name, sort) VALUES(?,?)", (name, mx + 1))
        conn.commit()
        return _ok({"id": cur.lastrowid, "name": name})
    finally:
        conn.close()


def put_permissions(pid, body):
    name = (body.get("name") or "").strip()
    if not name:
        return _err("权限名称不能为空")
    conn = get_conn()
    try:
        conn.execute("UPDATE permissions SET name=? WHERE id=?", (name, pid))
        conn.commit()
        return _ok()
    finally:
        conn.close()


def delete_permissions(pid):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM permissions WHERE id=?", (pid,))
        for r in conn.execute("SELECT id, perm_ids FROM persons").fetchall():
            ids = [i for i in _loads(r["perm_ids"]) if i != pid]
            conn.execute("UPDATE persons SET perm_ids=? WHERE id=?", (_dumps(ids), r["id"]))
            conn.execute(
                "UPDATE persons SET preferred_perm_id=NULL WHERE id=? AND preferred_perm_id=?",
                (r["id"], pid),
            )
        for r in conn.execute("SELECT id, perm_ids FROM categories").fetchall():
            ids = [i for i in _loads(r["perm_ids"]) if i != pid]
            conn.execute("UPDATE categories SET perm_ids=? WHERE id=?", (_dumps(ids), r["id"]))
        conn.execute("UPDATE categories SET preferred_perm_id=NULL WHERE preferred_perm_id=?", (pid,))
        conn.execute("UPDATE positions SET perm_id=NULL WHERE perm_id=?", (pid,))
        conn.commit()
        return _ok()
    finally:
        conn.close()


def get_categories():
    conn = get_conn()
    try:
        out = []
        for r in conn.execute("SELECT * FROM categories ORDER BY id"):
            d = dict(r)
            d["perm_ids"] = _loads(d["perm_ids"])
            out.append(d)
        return _ok(out)
    finally:
        conn.close()


def put_categories(cid, body):
    conn = get_conn()
    try:
        name = (body.get("name") or "").strip()
        if name:
            conn.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
        if "perm_ids" in body:
            conn.execute(
                "UPDATE categories SET perm_ids=? WHERE id=?",
                (_dumps(body.get("perm_ids") or []), cid),
            )
        if "preferred_perm_id" in body:
            conn.execute(
                "UPDATE categories SET preferred_perm_id=? WHERE id=?",
                (body.get("preferred_perm_id"), cid),
            )
        conn.commit()
        return _ok()
    finally:
        conn.close()


# ---------------------------------------------------------------- 名单/人员


def get_rosters():
    conn = get_conn()
    try:
        out = []
        for r in conn.execute("SELECT * FROM rosters ORDER BY id"):
            d = dict(r)
            d["count"] = conn.execute(
                "SELECT COUNT(*) c FROM persons WHERE roster_id=?", (d["id"],)
            ).fetchone()["c"]
            out.append(d)
        return _ok(out)
    finally:
        conn.close()


def post_rosters(body):
    name = (body.get("name") or "").strip()
    if not name:
        return _err("名单名称不能为空")
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO rosters(name) VALUES(?)", (name,))
        conn.commit()
        return _ok({"id": cur.lastrowid, "name": name})
    finally:
        conn.close()


def put_rosters(rid, body):
    name = (body.get("name") or "").strip()
    if not name:
        return _err("名单名称不能为空")
    conn = get_conn()
    try:
        conn.execute("UPDATE rosters SET name=? WHERE id=?", (name, rid))
        conn.commit()
        return _ok()
    finally:
        conn.close()


def delete_rosters(rid):
    conn = get_conn()
    try:
        used = conn.execute("SELECT COUNT(*) c FROM tasks WHERE roster_id=?", (rid,)).fetchone()["c"]
        if used:
            return _err("该名单正被 %d 个排班任务使用，不能删除" % used)
        pids = [r["id"] for r in conn.execute("SELECT id FROM persons WHERE roster_id=?", (rid,))]
        for pid in pids:
            conn.execute("DELETE FROM schedules WHERE person_id=?", (pid,))
            conn.execute("DELETE FROM attendance WHERE person_id=?", (pid,))
        conn.execute("DELETE FROM persons WHERE roster_id=?", (rid,))
        conn.execute("DELETE FROM rosters WHERE id=?", (rid,))
        conn.commit()
        return _ok()
    finally:
        conn.close()


def get_persons(rid):
    conn = get_conn()
    try:
        out = []
        sql = "SELECT * FROM persons WHERE roster_id=?"
        if not db.show_disabled_enabled():
            sql += " AND status='active'"
        sql += " ORDER BY id"
        for r in conn.execute(sql, (rid,)):
            d = dict(r)
            d["perm_ids"] = _loads(d["perm_ids"])
            out.append(d)
        return _ok(out)
    finally:
        conn.close()


def _person_from_body(body, roster_id):
    name = (body.get("name") or "").strip()
    if not name:
        raise ValueError("姓名不能为空")
    return (
        roster_id,
        name,
        (body.get("code") or "").strip(),
        (body.get("phone") or "").strip(),
        body.get("category_id"),
        _dumps(body.get("perm_ids") or []),
        body.get("preferred_perm_id"),
        (body.get("remark") or "").strip(),
    )


def post_persons(body):
    try:
        roster_id = _int(body.get("roster_id"))
        if not roster_id:
            return _err("缺少名单")
        values = _person_from_body(body, roster_id)
    except ValueError as e:
        return _err(e)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO persons(roster_id, name, code, phone, category_id, perm_ids, preferred_perm_id, remark) "
            "VALUES(?,?,?,?,?,?,?,?)",
            values,
        )
        conn.commit()
        return _ok({"id": cur.lastrowid})
    finally:
        conn.close()


def put_persons(pid, body):
    conn = get_conn()
    try:
        old = conn.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
        if not old:
            return _err("人员不存在")
        roster_id = _int(body.get("roster_id"), old["roster_id"])
        values = _person_from_body(body, roster_id)
        conn.execute(
            "UPDATE persons SET roster_id=?, name=?, code=?, phone=?, category_id=?, "
            "perm_ids=?, preferred_perm_id=?, remark=? WHERE id=?",
            values + (pid,),
        )
        conn.commit()
        return _ok()
    except ValueError as e:
        return _err(e)
    finally:
        conn.close()


def delete_persons(pid):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT name, status FROM persons WHERE id=?", (pid,)).fetchone()
        if not cur:
            return _err("人员不存在")
        if cur["status"] == "disabled":
            return _err("该人员已停用")
        conn.execute(
            "UPDATE persons SET status='disabled', deleted_at=? WHERE id=?",
            (_now(), pid),
        )
        conn.commit()
        return _ok({"name": cur["name"]})
    finally:
        conn.close()


def restore_persons(pid):
    conn = get_conn()
    try:
        cur = conn.execute("SELECT name, status, roster_id FROM persons WHERE id=?", (pid,)).fetchone()
        if not cur:
            return _err("人员不存在")
        if cur["status"] != "disabled":
            return _err("该人员未停用，无需恢复")
        conn.execute(
            "UPDATE persons SET status='active', deleted_at=NULL WHERE id=?", (pid,)
        )
        conn.commit()
        return _ok({"name": cur["name"]})
    finally:
        conn.close()


# ---------------------------------------------------------------- 任务


def get_tasks():
    conn = get_conn()
    try:
        roster_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM rosters")}
        slot_counts = {
            r["task_id"]: r["c"]
            for r in conn.execute("SELECT task_id, COUNT(*) c FROM slots GROUP BY task_id")
        }
        person_counts = {
            r["roster_id"]: r["c"]
            for r in conn.execute("SELECT roster_id, COUNT(*) c FROM persons GROUP BY roster_id")
        }
        out = []
        for t in conn.execute("SELECT * FROM tasks ORDER BY id"):
            d = dict(t)
            d["roster_name"] = roster_names.get(d["roster_id"], "?")
            d["slot_count"] = slot_counts.get(d["id"], 0)
            d["person_count"] = person_counts.get(d["roster_id"], 0)
            out.append(d)
        return _ok(out)
    finally:
        conn.close()


def _validate_slots(slots):
    if not isinstance(slots, list) or len(slots) == 0:
        raise ValueError("至少需要一个班次时段")
    norm = []
    for i, s in enumerate(slots):
        start = (s.get("start") or "").strip()
        end = (s.get("end") or "").strip()
        if not TIME_RE.match(start) or not TIME_RE.match(end):
            raise ValueError("第%d个时段格式应为 HH:MM（如 08:00）" % (i + 1))
        sm, em = sc._tomin(start), sc._tomin(end)
        if em <= sm:
            raise ValueError("第%d个时段的结束时间必须晚于开始时间" % (i + 1))
        start = _hhmm(sm)
        end = _hhmm(em)
        positions = s.get("positions") or []
        if not positions:
            raise ValueError("第%d个时段至少需要一个岗位" % (i + 1))
        pnorm = []
        for j, p in enumerate(positions):
            pname = (p.get("name") or "").strip() or ("岗位%d" % (j + 1))
            pnorm.append({"id": p.get("id"), "name": pname, "perm_id": p.get("perm_id")})
        norm.append({"id": s.get("id"), "start": start, "end": end, "positions": pnorm})
    return norm


def _write_task_config(conn, task_id, name, roster_id, slots):
    if not (name or "").strip():
        raise ValueError("任务名称不能为空")
    if not roster_id:
        raise ValueError("请选择名单")
    if conn.execute("SELECT COUNT(*) c FROM rosters WHERE id=?", (roster_id,)).fetchone()["c"] == 0:
        raise ValueError("名单不存在")
    conn.execute("UPDATE tasks SET name=?, roster_id=? WHERE id=?", (name.strip(), roster_id, task_id))

    existing = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM slots WHERE task_id=?", (task_id,))}
    kept_slot_ids = set()
    for idx, s in enumerate(slots):
        if s.get("id") and s["id"] in existing:
            sid = s["id"]
            conn.execute("UPDATE slots SET idx=?, start=?, \"end\"=? WHERE id=?", (idx, s["start"], s["end"], sid))
        else:
            cur = conn.execute(
                "INSERT INTO slots(task_id, idx, start, \"end\") VALUES(?,?,?,?)",
                (task_id, idx, s["start"], s["end"]),
            )
            sid = cur.lastrowid
        kept_slot_ids.add(sid)
        _write_positions(conn, sid, s["positions"])

    for sid in existing:
        if sid not in kept_slot_ids:
            pids = [r["id"] for r in conn.execute("SELECT id FROM positions WHERE slot_id=?", (sid,))]
            for pid in pids:
                conn.execute("DELETE FROM schedules WHERE position_id=?", (pid,))
                conn.execute("DELETE FROM positions WHERE id=?", (pid,))
            conn.execute("DELETE FROM slots WHERE id=?", (sid,))


def _write_positions(conn, slot_id, positions):
    existing = {r["id"] for r in conn.execute("SELECT id FROM positions WHERE slot_id=?", (slot_id,))}
    kept = set()
    for p in positions:
        pid = p.get("id")
        if pid and pid in existing:
            conn.execute("UPDATE positions SET name=?, perm_id=? WHERE id=?", (p["name"], p["perm_id"], pid))
            kept.add(pid)
        else:
            cur = conn.execute(
                "INSERT INTO positions(slot_id, name, perm_id) VALUES(?,?,?)",
                (slot_id, p["name"], p["perm_id"]),
            )
            kept.add(cur.lastrowid)
    for pid in existing - kept:
        conn.execute("DELETE FROM schedules WHERE position_id=?", (pid,))
        conn.execute("DELETE FROM positions WHERE id=?", (pid,))


def post_tasks(body):
    conn = get_conn()
    try:
        slots = _validate_slots(body.get("slots"))
        cur = conn.execute(
            "INSERT INTO tasks(name, roster_id) VALUES(?,?)",
            ((body.get("name") or "").strip() or "未命名任务", _int(body.get("roster_id"))),
        )
        _write_task_config(conn, cur.lastrowid, body.get("name"), _int(body.get("roster_id")), slots)
        conn.commit()
        return _ok({"id": cur.lastrowid})
    except ValueError as e:
        conn.rollback()
        return _err(e)
    finally:
        conn.close()


def get_task(tid):
    conn = get_conn()
    try:
        task = sc.load_task(conn, tid)
        if not task:
            return _err("任务不存在", 404)
        task["roster_name"] = (conn.execute("SELECT name FROM rosters WHERE id=?", (task["roster_id"],)).fetchone() or {"name": "?"})["name"]
        return _ok(task)
    finally:
        conn.close()


def put_task(tid, body):
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) c FROM tasks WHERE id=?", (tid,)).fetchone()["c"] == 0:
            return _err("任务不存在", 404)
        slots = _validate_slots(body.get("slots"))
        _write_task_config(conn, tid, body.get("name"), _int(body.get("roster_id")), slots)
        conn.commit()
        return _ok()
    except ValueError as e:
        conn.rollback()
        return _err(e)
    finally:
        conn.close()


def delete_task(tid):
    conn = get_conn()
    try:
        pids = [
            r["id"]
            for r in conn.execute(
                "SELECT p.id FROM positions p JOIN slots s ON s.id=p.slot_id WHERE s.task_id=?", (tid,)
            )
        ]
        for pid in pids:
            conn.execute("DELETE FROM schedules WHERE position_id=?", (pid,))
            conn.execute("DELETE FROM positions WHERE id=?", (pid,))
        conn.execute("DELETE FROM slots WHERE task_id=?", (tid,))
        conn.execute("DELETE FROM schedules WHERE task_id=?", (tid,))
        conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.commit()
        return _ok()
    finally:
        conn.close()


# ---------------------------------------------------------------- 排班


def generate(tid, body):
    year = _int(body.get("year"))
    month = _int(body.get("month"))
    if not year or not month or not (1 <= month <= 12):
        return _err("请指定年份和月份")
    start_date = (body.get("start_date") or "").strip() or None
    try:
        return _ok(sc.generate_month(tid, year, month, start_date))
    except ValueError as e:
        return _err(e)
    except Exception as e:
        traceback.print_exc()
        return _err("排班生成失败: %s" % e, 500)


def get_schedule(tid, qs):
    year = _int((qs.get("year") or [""])[0])
    month = _int((qs.get("month") or [""])[0])
    if not year or not month:
        return _err("请指定年份和月份")
    conn = get_conn()
    try:
        task = sc.load_task(conn, tid)
        if not task:
            return _err("任务不存在", 404)
        import calendar
        import datetime as dt

        n = calendar.monthrange(year, month)[1]
        days = []
        for d in range(1, n + 1):
            date = dt.date(year, month, d)
            days.append({"date": date.isoformat(), "day": d, "weekday": date.weekday()})
        d0, d1 = days[0]["date"], days[-1]["date"] + "z"
        rows = conn.execute(
            "SELECT sc.date, sc.slot_id, sc.position_id, sc.person_id, sc.cover_of, "
            "pe.name AS person_name, pe.code AS person_code "
            "FROM schedules sc JOIN persons pe ON pe.id=sc.person_id "
            "WHERE sc.task_id=? AND sc.date>=? AND sc.date<? ORDER BY sc.date, sc.id",
            (tid, d0, d1),
        ).fetchall()
        cells = {}
        att = {
            (a["person_id"], a["date"]): a["kind"]
            for a in conn.execute(
                "SELECT person_id, date, kind FROM attendance WHERE date>=? AND date<?", (d0, d1)
            )
        }
        for r in rows:
            d = cells.setdefault(r["date"], {})
            kind = att.get((r["person_id"], r["date"]))
            d.setdefault(str(r["position_id"]), []).append(
                {
                    "person_id": r["person_id"],
                    "name": r["person_name"],
                    "cover_of": r["cover_of"],
                    "slot_id": r["slot_id"],
                    "kind": sc.norm_kind(kind),
                    "kind_label": sc.label_of(kind),
                }
            )
        slots = []
        for s in task["slots"]:
            slots.append(
                {
                    "id": s["id"],
                    "start": s["start"],
                    "end": s["end"],
                    "positions": [
                        {
                            "id": p["id"],
                            "name": p["name"],
                            "perm_id": p["perm_id"],
                            "perm_name": _perm_name(conn, p["perm_id"]),
                        }
                        for p in s["positions"]
                    ],
                }
            )
        return _ok({"days": days, "slots": slots, "cells": cells, "task": {"id": task["id"], "name": task["name"]}})
    finally:
        conn.close()


def _perm_name(conn, perm_id):
    if perm_id is None:
        return ""
    r = conn.execute("SELECT name FROM permissions WHERE id=?", (perm_id,)).fetchone()
    return r["name"] if r else ""


def assign(tid, body):
    date = body.get("date")
    position_id = _int(body.get("position_id"))
    person_id = _int(body.get("person_id"))
    if not date or not position_id:
        return _err("缺少参数")
    conn = get_conn()
    try:
        pos = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        if not pos:
            return _err("岗位不存在", 404)
        slot = conn.execute("SELECT * FROM slots WHERE id=?", (pos["slot_id"],)).fetchone()
        if not slot or slot["task_id"] != tid:
            return _err("岗位不属于该任务")
        task = sc.load_task(conn, tid)
        roster_ids = {p["id"] for p in sc.load_roster(conn, task["roster_id"])}

        cur = conn.execute(
            "SELECT * FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NULL",
            (tid, date, position_id),
        ).fetchone()

        if person_id is None:
            if cur:
                conn.execute(
                    "DELETE FROM schedules WHERE task_id=? AND date=? AND position_id=? "
                    "AND (person_id=? OR cover_of=?)",
                    (tid, date, position_id, cur["person_id"], cur["person_id"]),
                )
            conn.commit()
            return _ok()

        if person_id not in roster_ids:
            return _err("该人员不在此任务名单中")
        person = sc.load_person(conn, person_id)
        if not person:
            return _err("人员不存在", 404)
        if person.get("status") == "disabled":
            return _err("该人员已停用，请先恢复后再排班")
        if sc._unavailable(conn, person_id, date):
            return _err("该人员当天已登记不在岗（%s）" % absent_labels())
        if not sc._perm_ok(person, dict(pos)):
            return _err("该人员没有此岗位所需权限「%s」" % _perm_name(conn, pos["perm_id"]))
        multi = db.multi_shift_enabled()
        other = [
            b
            for b in sc.busy_intervals(conn, person_id, date)
            if b[2] != tid
        ]
        if sc.has_conflict(other, slot["start"], slot["end"], multi=multi):
            return _err("该人员该时段已有排班" if multi else "该人员当天已有排班")
        yd = (dt_date(date) - dt_delta()).isoformat()
        yrow = conn.execute(
            "SELECT slot_id FROM schedules WHERE task_id=? AND date=? AND person_id=?",
            (tid, yd, person_id),
        ).fetchone()
        if yrow and yrow["slot_id"] == pos["slot_id"] and not (cur and cur["person_id"] == person_id):
            return _err("该人员昨天上的是同一班次，需保持轮动")

        conn.execute(
            "DELETE FROM schedules WHERE task_id=? AND date=? AND person_id=? "
            "AND NOT (position_id=? AND cover_of IS NULL)",
            (tid, date, person_id, position_id),
        )
        if cur and cur["person_id"] != person_id:
            conn.execute(
                "DELETE FROM schedules WHERE task_id=? AND date=? AND position_id=? "
                "AND (person_id=? OR cover_of=?)",
                (tid, date, position_id, cur["person_id"], cur["person_id"]),
            )
            cur = None
        if not cur:
            conn.execute(
                "INSERT INTO schedules(task_id, date, slot_id, position_id, person_id, cover_of) "
                "VALUES(?,?,?,?,?,NULL)",
                (tid, date, pos["slot_id"], position_id, person_id),
            )
        conn.commit()
        return _ok()
    finally:
        conn.close()


def dt_date(date):
    return _fromiso(date)


def dt_delta():
    import datetime

    return datetime.timedelta(days=1)


# ---------------------------------------------------------------- 考勤/顶班


def get_attendance(qs):
    date = (qs.get("date") or [""])[0]
    if not date:
        return _err("缺少日期")
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT sc.task_id, t.name AS task_name, t.roster_id AS roster_id, "
            "ro.name AS roster_name, sc.slot_id, sc.position_id, sc.person_id, "
            "sc.cover_of, s.start, s.\"end\", po.name AS position_name, po.perm_id, "
            "pe.name AS person_name "
            "FROM schedules sc "
            "JOIN tasks t ON t.id=sc.task_id "
            "LEFT JOIN rosters ro ON ro.id=t.roster_id "
            "JOIN slots s ON s.id=sc.slot_id "
            "JOIN positions po ON po.id=sc.position_id "
            "JOIN persons pe ON pe.id=sc.person_id "
            "WHERE sc.date=? ORDER BY t.id, s.idx, po.id, sc.id",
            (date,),
        ).fetchall()
        grouped = {}
        order = []
        perms = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM permissions")}
        for r in rows:
            key = (r["task_id"], r["position_id"])
            if key not in grouped:
                grouped[key] = {
                    "task_id": r["task_id"],
                    "task_name": r["task_name"],
                    "roster_id": r["roster_id"],
                    "roster_name": r["roster_name"] or "",
                    "slot_id": r["slot_id"],
                    "start": r["start"],
                    "end": r["end"],
                    "position_id": r["position_id"],
                    "position_name": r["position_name"],
                    "perm_name": perms.get(r["perm_id"], ""),
                    "base": None,
                    "covers": [],
                }
                order.append(key)
            item = {
                "person_id": r["person_id"],
                "name": r["person_name"],
                "kind": None,
                "reason": "",
            }
            if r["cover_of"] is None:
                grouped[key]["base"] = item
            else:
                grouped[key]["covers"].append(item)

        recs = conn.execute(
            "SELECT a.person_id, a.kind, a.reason, p.name, p.roster_id, "
            "ro.name AS roster_name FROM attendance a "
            "JOIN persons p ON p.id=a.person_id "
            "LEFT JOIN rosters ro ON ro.id=p.roster_id WHERE a.date=?",
            (date,),
        ).fetchall()
        rec_map = {r["person_id"]: dict(r) for r in recs}
        for k in order:
            g = grouped[k]
            for it in ([g["base"]] if g["base"] else []) + g["covers"]:
                rec = rec_map.get(it["person_id"])
                if rec:
                    it["kind"] = rec["kind"]
                    it["reason"] = rec["reason"]

        # 没有任何排班、但当天有记录的人
        scheduled_pids = set()
        for k in order:
            g = grouped[k]
            if g["base"]:
                scheduled_pids.add(g["base"]["person_id"])
            for c in g["covers"]:
                scheduled_pids.add(c["person_id"])
        extra = []
        for pid, rec in rec_map.items():
            if pid in scheduled_pids:
                continue
            extra.append(
                {
                    "person_id": pid,
                    "name": rec["name"],
                    "roster_id": rec["roster_id"],
                    "roster_name": rec["roster_name"] or "",
                    "kind": rec["kind"],
                    "reason": rec["reason"],
                }
            )
        return _ok({"date": date, "groups": [grouped[k] for k in order], "extra_records": extra})
    finally:
        conn.close()


def _valid_kind(kind):
    return kind in db.att_kinds_map()


def get_att_kinds():
    return _ok(att_kinds())


def put_att_kinds(body):
    items = body.get("kinds")
    if not isinstance(items, list) or not items:
        return _err("参数错误：kinds 不能为空")
    clean = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            return _err("参数错误：配置项格式不正确")
        kind = str(it.get("kind") or "").strip()
        label = str(it.get("label") or "").strip()
        if not kind or not label:
            return _err("类型代码与名称不能为空")
        if kind != "normal" and not re.fullmatch(r"[A-Za-z0-9_]+", kind):
            return _err("类型代码「%s」只能用字母、数字、下划线" % kind)
        if kind in seen:
            return _err("类型代码重复：%s" % kind)
        seen.add(kind)
        try:
            weight = float(it.get("weight"))
        except (TypeError, ValueError):
            return _err("「%s」的分数必须是数字" % label)
        unavailable = bool(it.get("unavailable")) if kind != "normal" else False
        clean.append({"kind": kind, "label": label, "weight": weight, "unavailable": unavailable})
    if "normal" not in seen:
        return _err("必须保留「正常」类型（kind=normal）")
    conn = get_conn()
    try:
        db.save_att_kinds(conn, clean)
    finally:
        conn.close()
    return _ok(att_kinds())


def get_settings():
    return _ok(dict(db.settings_map()))


def put_settings(body):
    if not isinstance(body, dict):
        return _err("参数错误")
    known = set(db.DEFAULT_SETTINGS)
    unknown = [k for k in body if k not in known]
    if unknown:
        return _err("未知设置项：%s" % "、".join(sorted(unknown)))
    for k, v in body.items():
        if k == "multi_shift" and str(v).lower() not in ("0", "1", "true", "false", "on", "off", "yes", "no"):
            return _err("multi_shift 只能取 0/1")
        if k == "show_disabled" and str(v).lower() not in ("0", "1", "true", "false", "on", "off", "yes", "no"):
            return _err("show_disabled 只能取 0/1")
        if k == "cover_multi_shift" and str(v).lower() not in ("0", "1", "true", "false", "on", "off", "yes", "no"):
            return _err("cover_multi_shift 只能取 0/1")
        db.set_setting(k, v)
    return _ok(dict(db.settings_map()))


def repair_covers():
    conn = get_conn()
    try:
        removed, promoted = sc.repair_orphan_covers(conn)
        return _ok({"removed": removed, "promoted": promoted})
    finally:
        conn.close()


def selfcheck():
    conn = get_conn()
    try:
        checks = []

        def add(name, items, sample=10):
            checks.append({"name": name, "count": len(items), "items": items[:sample]})

        slots = {r["id"]: dict(r) for r in conn.execute('SELECT * FROM slots')}
        tasks = {r["id"] for r in conn.execute("SELECT id FROM tasks")}
        persons = {r["id"] for r in conn.execute("SELECT id FROM persons")}
        positions = {r["id"]: dict(r) for r in conn.execute("SELECT id, slot_id, perm_id FROM positions")}
        perms = {r["id"] for r in conn.execute("SELECT id FROM permissions")}
        cats = [dict(r) for r in conn.execute("SELECT * FROM categories")]
        multi = db.multi_shift_enabled()

        orphan = []
        for sc_row in conn.execute("SELECT * FROM schedules"):
            r = dict(sc_row)
            if r["task_id"] not in tasks:
                orphan.append({"表": "schedules", "行": r["id"], "原因": "task_id=%s 不存在" % r["task_id"]})
            if r["slot_id"] not in slots:
                orphan.append({"表": "schedules", "行": r["id"], "原因": "slot_id=%s 不存在" % r["slot_id"]})
            if r["position_id"] not in positions:
                orphan.append({"表": "schedules", "行": r["id"], "原因": "position_id=%s 不存在" % r["position_id"]})
            elif r["slot_id"] in slots and positions[r["position_id"]]["slot_id"] != r["slot_id"]:
                orphan.append({"表": "schedules", "行": r["id"],
                               "原因": "position %s 不属于 slot %s" % (r["position_id"], r["slot_id"])})
            if r["person_id"] not in persons:
                orphan.append({"表": "schedules", "行": r["id"], "原因": "person_id=%s 不存在" % r["person_id"]})
        for p in conn.execute("SELECT id, roster_id FROM persons"):
            if conn.execute("SELECT 1 FROM rosters WHERE id=?", (p["roster_id"],)).fetchone() is None:
                orphan.append({"表": "persons", "行": p["id"], "原因": "roster_id=%s 不存在" % p["roster_id"]})
        for s in conn.execute("SELECT id, task_id FROM slots"):
            if s["task_id"] not in tasks:
                orphan.append({"表": "slots", "行": s["id"], "原因": "task_id=%s 不存在" % s["task_id"]})
        add("孤立引用（记录指向已删除对象）", orphan)

        by_person_day = {}
        for r in conn.execute(
            "SELECT sc.id, sc.person_id, sc.date, sc.task_id, sc.position_id, sc.cover_of, "
            's.start, s."end" FROM schedules sc JOIN slots s ON s.id=sc.slot_id'
        ):
            by_person_day.setdefault((r["person_id"], r["date"]), []).append(dict(r))

        cross_task = []
        overlaps = []
        multi_ok = []
        for (pid, date), rows in by_person_day.items():
            tids = {r["task_id"] for r in rows}
            if len(tids) > 1:
                if not multi:
                    cross_task.append({"person_id": pid, "date": date,
                                       "说明": "同一天 %d 个任务，当前规则「每人每天最多一班」" % len(tids),
                                       "任务": sorted(tids)})
                else:
                    multi_ok.append({"person_id": pid, "date": date, "任务": sorted(tids)})
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    a, b = rows[i], rows[j]
                    if sc._overlaps((a["start"], a["end"]), (b["start"], b["end"])):
                        overlaps.append({"person_id": pid, "date": date,
                                         "时段1": "%s %s-%s" % (a["task_id"], a["start"], a["end"]),
                                         "时段2": "%s %s-%s" % (b["task_id"], b["start"], b["end"])})
        add("跨任务同日冲突（违反「每人每天最多一班」）", cross_task)
        add("时间区间重叠（两种模式下都应为 0）", overlaps)
        add("已启用多班次：跨任务同日（非重叠，属正常）", multi_ok)

        dup_base = []
        for r in conn.execute(
            "SELECT task_id, date, position_id, COUNT(*) c FROM schedules "
            "WHERE cover_of IS NULL GROUP BY task_id, date, position_id HAVING c>1"
        ):
            dup_base.append({"task_id": r["task_id"], "date": r["date"],
                             "position_id": r["position_id"], "条数": r["c"]})
        add("同一岗位同一天存在多条底班", dup_base)

        dangling = []
        for r in conn.execute(
            "SELECT id, task_id, date, position_id, person_id, cover_of "
            "FROM schedules WHERE cover_of IS NOT NULL"
        ):
            base = conn.execute(
                "SELECT 1 FROM schedules WHERE task_id=? AND date=? AND position_id=? "
                "AND person_id=? AND cover_of IS NULL",
                (r["task_id"], r["date"], r["position_id"], r["cover_of"]),
            ).fetchone()
            if base is None:
                dangling.append({
                    "schedules.id": r["id"],
                    "日期": r["date"],
                    "岗位id": r["position_id"],
                    "顶班人id": r["person_id"],
                    "被顶人id(cover_of)": r["cover_of"],
                    "原因": "同任务/日/岗位下找不到被顶人 %s 的底班" % r["cover_of"],
                })
        add("顶班记录指向不存在的底班", dangling)

        bad_kind = []
        kinds = set(db.att_kinds_map())
        for r in conn.execute("SELECT id, person_id, date, kind FROM attendance"):
            if r["kind"] not in kinds:
                bad_kind.append({"attendance.id": r["id"], "person_id": r["person_id"],
                                 "date": r["date"], "kind": r["kind"],
                                 "原因": "不在当前考勤类型配置中"})
        add("考勤类型代码未在「设置」中定义", bad_kind)

        bad_perm = []
        for r in conn.execute("SELECT id, perm_ids, preferred_perm_id FROM persons"):
            for pid in _loads(r["perm_ids"], []):
                if pid not in perms:
                    bad_perm.append({"表": "persons", "行": r["id"], "原因": "perm_ids 含已删权限 %s" % pid})
            if r["preferred_perm_id"] is not None and r["preferred_perm_id"] not in perms:
                bad_perm.append({"表": "persons", "行": r["id"],
                                 "原因": "preferred_perm_id=%s 已删除" % r["preferred_perm_id"]})
        for c in cats:
            for pid in _loads(c["perm_ids"], []):
                if pid not in perms:
                    bad_perm.append({"表": "categories", "行": c["id"], "原因": "perm_ids 含已删权限 %s" % pid})
            if c["preferred_perm_id"] is not None and c["preferred_perm_id"] not in perms:
                bad_perm.append({"表": "categories", "行": c["id"],
                                 "原因": "preferred_perm_id=%s 已删除" % c["preferred_perm_id"]})
        for pid, p in positions.items():
            if p["perm_id"] is not None and p["perm_id"] not in perms:
                bad_perm.append({"表": "positions", "行": pid, "原因": "perm_id=%s 已删除" % p["perm_id"]})
        add("权限引用失效", bad_perm)

        counts = {}
        for t in ("schedules", "attendance", "persons", "tasks", "slots", "positions", "rosters", "permissions"):
            counts[t] = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]

        total = sum(c["count"] for c in checks if not c["name"].startswith("已启用多班次"))
        return _ok({
            "ok": total == 0,
            "issues": total,
            "settings": dict(db.settings_map()),
            "checks": checks,
            "counts": counts,
        })
    finally:
        conn.close()


def post_attendance(body):
    person_id = _int(body.get("person_id"))
    date = body.get("date")
    kind = body.get("kind")
    if not person_id or not date or not _valid_kind(kind):
        return _err("参数错误")
    conn = get_conn()
    try:
        if kind == "normal":
            conn.execute("DELETE FROM attendance WHERE person_id=? AND date=?", (person_id, date))
            conn.execute("DELETE FROM schedules WHERE cover_of=? AND date=?", (person_id, date))
        else:
            reason = (body.get("reason") or "").strip()
            cur = conn.execute(
                "UPDATE attendance SET kind=?, reason=? WHERE person_id=? AND date=?",
                (kind, reason, person_id, date),
            )
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO attendance(person_id, date, kind, reason) VALUES(?,?,?,?)",
                    (person_id, date, kind, reason),
                )
        conn.commit()
        return _ok()
    finally:
        conn.close()


def post_attendance_range(body):
    person_id = _int(body.get("person_id"))
    kind = body.get("kind")
    if not person_id or kind not in db.att_kinds_map():
        return _err("参数错误")
    try:
        d0 = _fromiso((body.get("start") or "").strip())
        d1 = _fromiso((body.get("end") or "").strip())
    except Exception:
        return _err("日期格式应为 YYYY-MM-DD")
    if d1 < d0:
        return _err("结束日期不能早于开始日期")
    if (d1 - d0).days > 366:
        return _err("时间范围过大（最多 366 天）")
    reason = (body.get("reason") or "").strip()
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) c FROM persons WHERE id=?", (person_id,)).fetchone()["c"] == 0:
            return _err("人员不存在")
        days = []
        cur = d0
        while cur <= d1:
            days.append(cur.isoformat())
            cur += datetime.timedelta(days=1)
        for date in days:
            if kind == "normal":
                conn.execute("DELETE FROM attendance WHERE person_id=? AND date=?", (person_id, date))
                continue
            changed = conn.execute(
                "UPDATE attendance SET kind=?, reason=? WHERE person_id=? AND date=?",
                (kind, reason, person_id, date),
            )
            if changed.rowcount == 0:
                conn.execute(
                    "INSERT INTO attendance(person_id, date, kind, reason) VALUES(?,?,?,?)",
                    (person_id, date, kind, reason),
                )
        conn.commit()
        return _ok({"days": len(days)})
    finally:
        conn.close()


def delete_attendance(qs):
    person_id = _int((qs.get("person_id") or [""])[0])
    date = (qs.get("date") or [""])[0]
    if not person_id or not date:
        return _err("缺少参数")
    conn = get_conn()
    try:
        conn.execute("DELETE FROM attendance WHERE person_id=? AND date=?", (person_id, date))
        conn.execute("DELETE FROM schedules WHERE cover_of=? AND date=?", (person_id, date))
        conn.commit()
        return _ok()
    finally:
        conn.close()


def cover_suggest(qs):
    task_id = _int((qs.get("task_id") or [""])[0])
    date = (qs.get("date") or [""])[0]
    position_id = _int((qs.get("position_id") or [""])[0])
    if not task_id or not date or not position_id:
        return _err("缺少参数")
    conn = get_conn()
    try:
        return _ok(sc.suggest_covers(conn, task_id, date, position_id))
    finally:
        conn.close()


def cover_apply(body):
    task_id = _int(body.get("task_id"))
    date = body.get("date")
    position_id = _int(body.get("position_id"))
    person_id = _int(body.get("person_id"))
    if not task_id or not date or not position_id or not person_id:
        return _err("缺少参数")
    conn = get_conn()
    try:
        sc.apply_cover(conn, task_id, date, position_id, person_id)
        return _ok()
    except ValueError as e:
        return _err(e)
    finally:
        conn.close()


def cover_remove(qs):
    task_id = _int((qs.get("task_id") or [""])[0])
    date = (qs.get("date") or [""])[0]
    position_id = _int((qs.get("position_id") or [""])[0])
    if not task_id or not date or not position_id:
        return _err("缺少参数")
    conn = get_conn()
    try:
        conn.execute(
            "DELETE FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NOT NULL",
            (task_id, date, position_id),
        )
        conn.commit()
        return _ok()
    finally:
        conn.close()


# ---------------------------------------------------------------- 统计


def _stats_range(qs):
    year = _int((qs.get("year") or [""])[0])
    month = _int((qs.get("month") or [""])[0])
    if year and year > 0:
        if month and 1 <= month <= 12:
            last = calendar.monthrange(year, month)[1]
            return "%04d-%02d-01" % (year, month), "%04d-%02d-%02d" % (year, month, last)
        return "%04d-01-01" % year, "%04d-12-31" % year
    return "0000-01-01", "9999-12-31"


def get_stats(qs):
    y0, y1 = _stats_range(qs)
    conn = get_conn()
    try:
        persons = [
            dict(r)
            for r in conn.execute(
                "SELECT p.*, r.name AS roster_name FROM persons p "
                "LEFT JOIN rosters r ON r.id=p.roster_id ORDER BY p.roster_id, p.id"
            )
        ]
        agg = {}
        kinds = list(db.att_kinds_map())
        att = {
            (a["person_id"], a["date"]): a["kind"]
            for a in conn.execute(
                "SELECT person_id, date, kind FROM attendance WHERE date>=? AND date<=?", (y0, y1)
            )
        }

        def blank():
            return {"kinds": {kk: 0 for kk in kinds}, "total": 0.0}

        # 一个「排班表」= 一个排班任务：班次/分数按 (人员, 排班表) 分开累计，不跨排班表合并。
        for r in conn.execute(
            "SELECT person_id, task_id, date FROM schedules WHERE date>=? AND date<=? "
            "ORDER BY task_id, person_id, date, id",
            (y0, y1),
        ):
            k = sc.norm_kind(att.get((r["person_id"], r["date"])))
            d = agg.setdefault((r["task_id"], r["person_id"]), blank())
            d["kinds"][k] += 1
            d["total"] += sc.weight_of(k)

        def make_row(p, task_id, task_name, roster_name):
            d = agg.get((task_id, p["id"])) or blank()
            row = {
                "person_id": p["id"],
                "name": p["name"],
                "code": p["code"],
                "roster_id": p["roster_id"],
                "roster_name": roster_name or "",
                "task_id": task_id,
                "task_name": task_name or "",
                "count": sum(d["kinds"].values()),
                "total": round(d["total"], 2),
            }
            for kk in kinds:
                row[kk] = d["kinds"][kk]
            return row

        by_roster = {}
        for p in persons:
            by_roster.setdefault(p["roster_id"], []).append(p)
        out = []
        used = set()
        for t in conn.execute(
            "SELECT t.id, t.name, t.roster_id, COALESCE(r.name, '') AS roster_name "
            "FROM tasks t LEFT JOIN rosters r ON r.id=t.roster_id ORDER BY t.id"
        ):
            for p in by_roster.get(t["roster_id"], []):
                used.add(p["id"])
                out.append(make_row(p, t["id"], t["name"], t["roster_name"]))
        for p in persons:
            if p["id"] not in used:
                out.append(make_row(p, 0, "", p.get("roster_name") or ""))
        return _ok(out)
    finally:
        conn.close()


def get_stats_detail(qs):
    person_id = _int((qs.get("person_id") or [""])[0])
    if not person_id:
        return _err("缺少人员")
    y0, y1 = _stats_range(qs)
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT sc.date, t.name AS task_name, s.start, s.\"end\", po.name AS position_name, "
            "sc.cover_of, pe.name AS cover_by "
            "FROM schedules sc "
            "JOIN tasks t ON t.id=sc.task_id "
            "JOIN slots s ON s.id=sc.slot_id "
            "JOIN positions po ON po.id=sc.position_id "
            "JOIN persons pe ON pe.id=sc.person_id "
            "WHERE sc.person_id=? AND sc.date>=? AND sc.date<=? ORDER BY sc.date, s.idx",
            (person_id, y0, y1),
        ).fetchall()
        out = []
        for r in rows:
            kind = sc.norm_kind(sc.attendance_kind(conn, person_id, r["date"]))
            out.append(
                {
                    "date": r["date"],
                    "task_name": r["task_name"],
                    "time": "%s-%s" % (r["start"], r["end"]),
                    "position_name": r["position_name"],
                    "kind": kind,
                    "kind_label": sc.label_of(kind),
                    "weight": sc.weight_of(kind),
                    "is_cover": r["cover_of"] is not None,
                }
            )
        return _ok(out)
    finally:
        conn.close()


def sc_rows(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 路由


def dispatch(method, path, qs, body):
    try:
        m = re.fullmatch(r"/api/permissions/(\d+)", path)
        if m:
            if method == "PUT":
                return put_permissions(int(m.group(1)), body)
            if method == "DELETE":
                return delete_permissions(int(m.group(1)))
        m = re.fullmatch(r"/api/categories/(\d+)", path)
        if m and method == "PUT":
            return put_categories(int(m.group(1)), body)
        m = re.fullmatch(r"/api/rosters/(\d+)", path)
        if m:
            if method == "PUT":
                return put_rosters(int(m.group(1)), body)
            if method == "DELETE":
                return delete_rosters(int(m.group(1)))
        m = re.fullmatch(r"/api/rosters/(\d+)/persons", path)
        if m and method == "GET":
            return get_persons(int(m.group(1)))
        m = re.fullmatch(r"/api/persons/(\d+)", path)
        if m:
            if method == "PUT":
                return put_persons(int(m.group(1)), body)
            if method == "DELETE":
                return delete_persons(int(m.group(1)))
        m = re.fullmatch(r"/api/persons/(\d+)/restore", path)
        if m and method == "POST":
            return restore_persons(int(m.group(1)))
        m = re.fullmatch(r"/api/tasks/(\d+)", path)
        if m:
            if method == "GET":
                return get_task(int(m.group(1)))
            if method == "PUT":
                return put_task(int(m.group(1)), body)
            if method == "DELETE":
                return delete_task(int(m.group(1)))
        m = re.fullmatch(r"/api/tasks/(\d+)/generate", path)
        if m and method == "POST":
            return generate(int(m.group(1)), body)
        m = re.fullmatch(r"/api/tasks/(\d+)/schedule", path)
        if m and method == "GET":
            return get_schedule(int(m.group(1)), qs)

        if path == "/api/meta" and method == "GET":
            status, data = get_permissions()
            if status != 200:
                return status, data
            perms = data
            status, data = get_categories()
            if status != 200:
                return status, data
            return _ok({"permissions": perms, "categories": data, "att_kinds": att_kinds(),
                        "settings": dict(db.settings_map())})
        if path == "/api/att_kinds":
            if method == "GET":
                return get_att_kinds()
            if method in ("POST", "PUT"):
                return put_att_kinds(body)
        if path == "/api/settings":
            if method == "GET":
                return get_settings()
            if method in ("POST", "PUT"):
                return put_settings(body)
        if path == "/api/selfcheck" and method == "GET":
            return selfcheck()
        if path == "/api/selfcheck/repair" and method == "POST":
            return repair_covers()
        if path == "/api/permissions":
            if method == "GET":
                return get_permissions()
            if method == "POST":
                return post_permissions(body)
        if path == "/api/categories" and method == "GET":
            return get_categories()
        if path == "/api/rosters":
            if method == "GET":
                return get_rosters()
            if method == "POST":
                return post_rosters(body)
        if path == "/api/persons" and method == "POST":
            return post_persons(body)
        if path == "/api/tasks":
            if method == "GET":
                return get_tasks()
            if method == "POST":
                return post_tasks(body)
        if path == "/api/schedule/assign" and method == "PUT":
            return assign(int(body.get("task_id") or 0), body)
        if path == "/api/attendance/range" and method == "POST":
            return post_attendance_range(body)
        if path == "/api/attendance":
            if method == "GET":
                return get_attendance(qs)
            if method == "POST":
                return post_attendance(body)
            if method == "DELETE":
                return delete_attendance(qs)
        if path == "/api/cover/suggest" and method == "GET":
            return cover_suggest(qs)
        if path == "/api/cover" and method == "POST":
            return cover_apply(body)
        if path == "/api/cover" and method == "DELETE":
            return cover_remove(qs)
        if path == "/api/stats" and method == "GET":
            return get_stats(qs)
        if path == "/api/stats/detail" and method == "GET":
            return get_stats_detail(qs)
        return _err("接口不存在: %s %s" % (method, path), 404)
    except ValueError as e:
        return _err(e)
    except Exception as e:
        traceback.print_exc()
        return _err("服务器错误: %s" % e, 500)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_static(self, rel):
        rel = rel.replace(os.sep, "/")
        if rel.startswith("/") or ".." in rel.split("/"):
            return None, None
        path = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if path.startswith(STATIC_DIR + os.sep) and os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read(), os.path.splitext(path)[1].lower()
        if PYZ_FILE and os.path.isfile(PYZ_FILE):  # zipapp 单文件模式
            try:
                import zipfile
                with zipfile.ZipFile(PYZ_FILE) as z:
                    return z.read("static/" + rel), os.path.splitext(rel)[1].lower()
            except Exception:
                return None, None
        return None, None

    def _send_file(self, rel):
        raw, ext = self._read_static(rel)
        if raw is None:
            self._send_json(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path.startswith("/api/"):
            body = self._body() if method in ("POST", "PUT") else {}
            status, data = dispatch(method, path, qs, body)
            self._send_json(status, data)
            return
        if method != "GET":
            self._send_json(405, {"error": "method not allowed"})
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
        elif path == "/" or path == "":
            rel = "index.html"
        else:
            rel = path.lstrip("/")
        self._send_file(rel)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")


def _open_browser(url):
    if os.environ.get("WORKDAY_NO_BROWSER"):
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    init_db()
    url = "http://127.0.0.1:%d" % PORT
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # 端口已被占用：视为已在运行，直接打开浏览器
        print("服务已在运行，打开浏览器: %s" % url)
        _open_browser(url)
        return
    print("排班系统已启动: %s （Ctrl+C 停止）" % url)
    _open_browser(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
