# -*- coding: utf-8 -*-
"""排班系统自测：公平性 + 业务不变量 + 请假区间接口。

在临时目录建库运行，不会影响 data.db。用法：python3 selftest.py
"""
import datetime
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import scheduler as sc
import server

TMP = None


def fresh(name):
    path = os.path.join(TMP, name)
    if os.path.exists(path):
        os.remove(path)
    db.DB_PATH = path
    db.init_db()
    conn = db.get_conn()
    perms = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM permissions")}
    cats = [r["id"] for r in conn.execute("SELECT id FROM categories")]
    return conn, perms, cats


def make_roster(conn, perms, cats, n):
    cur = conn.execute("INSERT INTO rosters(name) VALUES('R')")
    rid = cur.lastrowid
    letters = [["A", "B", "C", "D", "E"], ["B", "C", "D", "E"], ["C", "D", "E"], ["D", "E"], ["E"]]
    for i in range(n):
        ls = letters[i % len(letters)]
        conn.execute(
            "INSERT INTO persons(roster_id, name, code, category_id, perm_ids, preferred_perm_id) "
            "VALUES(?,?,?,?,?,?)",
            (rid, "P%02d" % (i + 1), "E%03d" % (i + 1), cats[i % len(cats)],
             db._dumps([perms[l] for l in ls]), perms[ls[-1]]),
        )
    conn.commit()
    return rid


def make_task(perms, rid, npos):
    perms_cycle = ["E", "D", "C", None, "D", "E"]
    slots = [
        {
            "start": "%02d:00" % (i * 4),
            "end": "%02d:00" % (i * 4 + 4),
            "positions": [{"name": "pos%d" % i,
                           "perm_id": perms[perms_cycle[i % 6]] if perms_cycle[i % 6] else None}],
        }
        for i in range(npos)
    ]
    status, data = server.post_tasks({"name": "T", "roster_id": rid, "slots": slots})
    assert status == 200, data
    return data["id"]


def invariants(conn, task_id):
    errs = []
    rows = conn.execute(
        "SELECT sc.date, sc.person_id, sc.slot_id, sc.position_id, po.perm_id, pe.perm_ids, pe.name "
        "FROM schedules sc JOIN positions po ON po.id=sc.position_id "
        "JOIN persons pe ON pe.id=sc.person_id WHERE sc.task_id=? "
        "ORDER BY sc.person_id, sc.date",
        (task_id,),
    ).fetchall()
    per_day = {}
    for r in rows:
        per_day.setdefault((r["person_id"], r["date"]), []).append(r)
        if r["perm_id"] is not None and r["perm_id"] not in json.loads(r["perm_ids"] or "[]"):
            errs.append("权限违规: %s %s" % (r["name"], r["date"]))
    for (pid, date), rs in per_day.items():
        if len(rs) > 1:
            errs.append("一天多班: pid=%s %s x%d" % (pid, date, len(rs)))

    by_person = {}
    for r in rows:
        by_person.setdefault(r["person_id"], {})[r["date"]] = r["slot_id"]
    for pid, m in by_person.items():
        for date, sid in m.items():
            nxt = (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()
            if m.get(nxt) == sid:
                errs.append("连续同一班次: pid=%s %s" % (pid, date))

    for a in conn.execute(
        "SELECT person_id, date FROM attendance WHERE kind IN ('a','b','absent')"
    ).fetchall():
        if by_person.get(a["person_id"], {}).get(a["date"]):
            errs.append("不在岗仍排班: pid=%s %s" % (a["person_id"], a["date"]))

    booked = {(r["person_id"], r["date"]) for r in rows}
    for r in conn.execute("SELECT date, person_id FROM schedules WHERE task_id!=?", (task_id,)):
        if (r["person_id"], r["date"]) in booked:
            errs.append("跨任务重复排班: pid=%s %s" % (r["person_id"], r["date"]))
    return errs, by_person


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + str(detail)) if detail else ""))


def scenario_fairness(name, n_persons, npos, months=1):
    print("== %s（%d人 %d岗 %d月）" % (name, n_persons, npos, months))
    conn, perms, cats = fresh(name + ".db")
    rid = make_roster(conn, perms, cats, n_persons)
    tid = make_task(perms, rid, npos)
    warn = 0
    for m in range(1, months + 1):
        warn += len(sc.generate_month(tid, 2026, m)["warnings"])
    errs, by_person = invariants(conn, tid)
    counts = sorted(len(v) for v in by_person.values())
    conn.close()
    print("     班次分布: %s 合计=%d 缺岗告警=%d" % (counts, sum(counts), warn))
    check(name + "/零违规", not errs, str(errs))
    if n_persons >= npos:
        check(name + "/人人有班", counts[0] > 0, "最少 %d 班" % counts[0])
        check(name + "/公平(极差<=月数)", counts[-1] - counts[0] <= months,
              "极差=%d months=%d" % (counts[-1] - counts[0], months))


def scenario_leave(n_persons=8, npos=2):
    print("== 请假区间接口 + 排班联动")
    conn, perms, cats = fresh("leave.db")
    rid = make_roster(conn, perms, cats, n_persons)
    tid = make_task(perms, rid, npos)
    pid = conn.execute("SELECT id FROM persons ORDER BY id LIMIT 1").fetchone()["id"]

    bad = [
        server.post_attendance_range({"person_id": pid, "start": "2026-11-05", "end": "2026-11-01", "kind": "a"})[0],
        server.post_attendance_range({"person_id": pid, "start": "2026/11/01", "end": "2026-11-02", "kind": "a"})[0],
        server.post_attendance_range({"person_id": pid, "start": "2026-01-01", "end": "2028-01-01", "kind": "a"})[0],
        server.post_attendance_range({"person_id": pid, "start": "2026-11-01", "end": "2026-11-02", "kind": "x"})[0],
        server.post_attendance_range({"person_id": 999999, "start": "2026-11-01", "end": "2026-11-02", "kind": "a"})[0],
    ]
    check("非法参数均被拒绝", bad == [400] * 5, str(bad))

    status, data = server.post_attendance_range(
        {"person_id": pid, "start": "2026-11-05", "end": "2026-11-09", "kind": "a", "reason": "家中有事"})
    check("区间录入 5 天", status == 200 and data["days"] == 5, str(data))

    sc.generate_month(tid, 2026, 11)
    worked = {r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM schedules WHERE task_id=? AND person_id=?", (tid, pid))}
    leave = {"2026-11-%02d" % d for d in range(5, 10)}
    check("请假期间不排班", not (leave & worked), str(sorted(leave & worked)))
    check("请假前后正常排班", bool(worked - leave), "%d 天" % len(worked - leave))

    server.post_attendance_range({"person_id": pid, "start": "2026-11-05", "end": "2026-11-09", "kind": "normal"})
    left = conn.execute("SELECT COUNT(*) c FROM attendance WHERE person_id=?", (pid,)).fetchone()["c"]
    check("normal 清除区间", left == 0, "剩余 %d 行" % left)

    server.post_attendance_range({"person_id": pid, "start": "2026-11-20", "end": "2026-11-20", "kind": "a"})
    server.post_attendance_range({"person_id": pid, "start": "2026-11-20", "end": "2026-11-20", "kind": "b", "reason": "Y"})
    row = conn.execute("SELECT kind, reason FROM attendance WHERE person_id=?", (pid,)).fetchone()
    check("同日覆盖更新(a->b)", row and row["kind"] == "b" and row["reason"] == "Y",
          dict(row) if row else "无记录")
    conn.close()


def scenario_cross_task():
    print("== 跨任务：同一人同一天不得重复排班")
    conn, perms, cats = fresh("cross.db")
    rid = make_roster(conn, perms, cats, 6)
    t1 = make_task(perms, rid, 2)
    t2 = make_task(perms, rid, 2)
    sc.generate_month(t1, 2026, 10)
    sc.generate_month(t2, 2026, 10)
    dup = conn.execute(
        "SELECT person_id, date, COUNT(*) c FROM schedules WHERE date='2026-10-10' "
        "GROUP BY person_id, date HAVING c>1"
    ).fetchall()
    check("同日无重复", not dup, str([dict(d) for d in dup]))
    conn.close()


def scenario_config():
    print("== 考勤类型可视化配置（DB 持久化 + 接口校验）")
    conn, perms, cats = fresh("config.db")
    rid = make_roster(conn, perms, cats, 4)
    tid = make_task(perms, rid, 2)
    sc.generate_month(tid, 2026, 10)
    pid = conn.execute("SELECT id FROM persons ORDER BY id LIMIT 1").fetchone()["id"]
    date = conn.execute(
        "SELECT date FROM schedules WHERE task_id=? AND person_id=? ORDER BY date LIMIT 1", (tid, pid)
    ).fetchone()["date"]

    meta = server.att_kinds()
    check("meta 暴露 att_kinds", [x["kind"] for x in meta] == list(db.att_kinds_map()), str(meta))
    check("meta 带 label/weight/unavailable",
          all({"kind", "label", "weight", "unavailable"} <= set(x) for x in meta), "")
    check("weight_of/label_of 取自配置",
          sc.weight_of("a") == db.att_kinds_map()["a"]["weight"] and sc.label_of("a") == db.att_kinds_map()["a"]["label"],
          "%s %s" % (sc.weight_of("a"), sc.label_of("a")))
    check("is_absent 取自配置",
          sc.is_absent("a") and sc.is_absent("absent") and not sc.is_absent("normal"), "")

    server.post_attendance_range({"person_id": pid, "start": date, "end": date, "kind": "a", "reason": "X"})
    stats = {r["person_id"]: r for r in server.get_stats({"year": ["2026"]})[1]}
    row = stats[pid]
    check("统计按 kind 分桶", row["a"] >= 1 and row["normal"] >= 0, "a=%d normal=%d" % (row["a"], row["normal"]))
    check("scores 列表长度=班次数", len(row["scores"]) == row["count"], "len=%d count=%d" % (len(row["scores"]), row["count"]))
    check("scores 之和=总分数", abs(sum(row["scores"]) - row["total"]) < 1e-9, "sum=%s total=%s" % (sum(row["scores"]), row["total"]))
    check("总班次=各 kind 计数之和", row["count"] == sum(row[k] for k in db.att_kinds_map()),
          "count=%d 期望=%d" % (row["count"], sum(row[k] for k in db.att_kinds_map())))

    det = server.get_stats_detail({"person_id": [str(pid)], "year": ["2026"]})[1]
    arow = [d for d in det if d["kind"] == "a"][0]
    check("明细 label/weight 取自配置",
          arow["kind_label"] == db.att_kinds_map()["a"]["label"] and arow["weight"] == db.att_kinds_map()["a"]["weight"],
          str(arow))

    grid = server.get_schedule(tid, {"year": ["2026"], "month": ["10"]})[1]
    cell = [it for items in grid["cells"].get(date, {}).values() for it in items if it["person_id"] == pid][0]
    check("排班表返回 kind/kind_label", cell["kind"] == "a" and cell["kind_label"] == db.att_kinds_map()["a"]["label"], str(cell))

    orig = server.att_kinds()
    changed = [dict(x) for x in orig]
    for x in changed:
        if x["kind"] == "a":
            x["weight"] = 0.9
    try:
        st, _ = server.put_att_kinds({"kinds": changed})
        row2 = {r["person_id"]: r for r in server.get_stats({"year": ["2026"]})[1]}[pid]
        check("改分数即改计分（接口）",
              st == 200 and any(abs(s - 0.9) < 1e-9 for s in row2["scores"]), "scores=%s" % row2["scores"][:8])
    finally:
        server.put_att_kinds({"kinds": orig})

    date2 = conn.execute(
        "SELECT date FROM schedules WHERE task_id=? AND person_id=? AND date<>? ORDER BY date LIMIT 1",
        (tid, pid, date),
    ).fetchone()["date"]
    added = [dict(x) for x in orig] + [{"kind": "c", "label": "C事", "weight": 0.3, "unavailable": True}]
    try:
        server.put_att_kinds({"kinds": added})
        check("新增类型后 meta 自动带上", any(x["kind"] == "c" for x in server.att_kinds()),
              str([x["kind"] for x in server.att_kinds()]))
        check("absent_labels 自动带上新类型", "C事" in server.absent_labels(), server.absent_labels())
        db.reload_att_kinds()
        check("配置持久化（重新加载后仍在）", "c" in db.att_kinds_map(), str(list(db.att_kinds_map())))
        st_c, _ = server.post_attendance_range({"person_id": pid, "start": date2, "end": date2, "kind": "c", "reason": "C"})
        row3 = {r["person_id"]: r for r in server.get_stats({"year": ["2026"]})[1]}[pid]
        expect_total = row["total"] - 1.0 + 0.3
        check("新类型通过校验且被计入", st_c == 200 and row3["c"] == 1 and row3["count"] == row["count"],
              "c=%d count=%d" % (row3["c"], row3["count"]))
        check("新类型权重参与总分数", abs(row3["total"] - expect_total) < 1e-9,
              "total=%s 期望=%s" % (row3["total"], expect_total))
        det2 = server.get_stats_detail({"person_id": [str(pid)], "year": ["2026"]})[1]
        crow = [d for d in det2 if d["kind"] == "c"][0]
        check("新类型明细标签取自配置", crow["kind_label"] == "C事" and crow["weight"] == 0.3, str(crow))
    finally:
        server.post_attendance_range({"person_id": pid, "start": date2, "end": date2, "kind": "normal"})
        server.put_att_kinds({"kinds": orig})
        row_after = {r["person_id"]: r for r in server.get_stats({"year": ["2026"]})[1]}[pid]
        check("移除类型后统计恢复", "c" not in row_after and abs(row_after["total"] - row["total"]) < 1e-9,
              "keys 含 c=%s total=%s" % ("c" in row_after, row_after["total"]))

    check("校验：空列表被拒", server.put_att_kinds({"kinds": []})[0] == 400, "")
    check("校验：缺少 normal 被拒",
          server.put_att_kinds({"kinds": [{"kind": "a", "label": "A事", "weight": 1, "unavailable": True}]})[0] == 400, "")
    check("校验：代码重复被拒",
          server.put_att_kinds({"kinds": [{"kind": "normal", "label": "正常", "weight": 1, "unavailable": False},
                                          {"kind": "normal", "label": "正常2", "weight": 1, "unavailable": False}]})[0] == 400, "")
    check("校验：分数非数字被拒",
          server.put_att_kinds({"kinds": [{"kind": "normal", "label": "正常", "weight": "x", "unavailable": False}]})[0] == 400, "")
    st_force, data_force = server.put_att_kinds({"kinds": [
        {"kind": "normal", "label": "出勤", "weight": 1, "unavailable": True},
        {"kind": "a", "label": "A事", "weight": 0.8, "unavailable": True},
    ]})
    nm = [x for x in (data_force if isinstance(data_force, list) else []) if x.get("kind") == "normal"]
    check("校验：normal 强制不在岗=False", st_force == 200 and bool(nm) and nm[0]["unavailable"] is False, str(data_force))
    server.put_att_kinds({"kinds": orig})
    check("校验被拒后配置未变", [x["kind"] for x in server.att_kinds()] == [x["kind"] for x in orig], "")

    for fn in ("scheduler.py", "server.py"):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), fn), encoding="utf-8").read()
        check("无硬编码权重 %s" % fn, "0.8" not in src and "0.5" not in src, "")
    conn.close()


def scenario_move():
    print("== 调整排班：同任务当天可移动（一天一班不破）")
    conn, perms, cats = fresh("move.db")
    rid = make_roster(conn, perms, cats, 5)
    tid = make_task(perms, rid, 3)
    poss = conn.execute(
        "SELECT po.id, po.slot_id FROM positions po JOIN slots s ON s.id=po.slot_id "
        "WHERE s.task_id=? ORDER BY po.id", (tid,)
    ).fetchall()
    pids = [r["id"] for r in conn.execute("SELECT id FROM persons ORDER BY id")]
    d = "2026-10-10"
    a, b = poss[0], poss[1]
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (tid, d, a["slot_id"], a["id"], pids[0]))
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (tid, d, b["slot_id"], b["id"], pids[2]))
    conn.commit()

    st, data = server.assign(tid, {"date": d, "position_id": b["id"], "person_id": pids[0]})
    check("已排人员可再次选择(移动)", st == 200, str(data))
    n = conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND person_id=?",
                     (tid, d, pids[0])).fetchone()["c"]
    check("移动后仍只有一班", n == 1, "%d 班" % n)
    at_b = conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND position_id=? AND person_id=? AND cover_of IS NULL",
                        (tid, d, b["id"], pids[0])).fetchone()["c"]
    check("人已在目标岗位", at_b == 1, "")
    at_a = conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NULL",
                        (tid, d, a["id"])).fetchone()["c"]
    check("原岗位已释放", at_a == 0, "")
    at_b_old = conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND position_id=? AND person_id=?",
                            (tid, d, b["id"], pids[2])).fetchone()["c"]
    check("目标岗位原人被换下", at_b_old == 0, "")

    t2 = make_task(perms, rid, 2)
    poss2 = conn.execute(
        "SELECT po.id, po.slot_id FROM positions po JOIN slots s ON s.id=po.slot_id "
        "WHERE s.task_id=? ORDER BY po.id", (t2,)
    ).fetchall()
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (t2, d, poss2[0]["slot_id"], poss2[0]["id"], pids[1]))
    conn.commit()
    st2, d2 = server.assign(tid, {"date": d, "position_id": a["id"], "person_id": pids[1]})
    check("跨任务仍拦截(当天已有排班)", st2 == 400 and "已有排班" in d2.get("error", ""), str(d2))
    n2 = conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND person_id=?",
                      (t2, d, pids[1])).fetchone()["c"]
    check("跨任务拦截后原排班未被破坏", n2 == 1, "%d 班" % n2)

    server.post_attendance_range({"person_id": pids[3], "start": d, "end": d, "kind": "a"})
    st3, d3 = server.assign(tid, {"date": d, "position_id": a["id"], "person_id": pids[3]})
    check("不在岗不可排", st3 != 200, str(d3))

    d2 = "2026-10-11"
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (tid, d2, a["slot_id"], a["id"], pids[2]))
    conn.commit()
    st4, d4 = server.assign(tid, {"date": d2, "position_id": a["id"], "person_id": pids[4]})
    base_rows = conn.execute(
        "SELECT person_id FROM schedules WHERE task_id=? AND date=? AND position_id=? AND cover_of IS NULL",
        (tid, d2, a["id"])).fetchall()
    check("更换岗位底班不残留重复", st4 == 200 and [b["person_id"] for b in base_rows] == [pids[4]],
          "rows=%s %s" % ([b["person_id"] for b in base_rows], d4))
    st5, _ = server.assign(tid, {"date": d2, "position_id": a["id"], "person_id": None})
    left = conn.execute(
        "SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND position_id=?",
        (tid, d2, a["id"])).fetchone()["c"]
    check("清空岗位真正清空", st5 == 200 and left == 0, "剩余 %d 行" % left)
    conn.close()


def scenario_group_and_cover():
    print("== 名单分组 + 顶班候选（全部有权限者 / 建议标记）")
    conn, perms, cats = fresh("group.db")
    rid_a = conn.execute("INSERT INTO rosters(name) VALUES('甲组')").lastrowid
    rid_b = conn.execute("INSERT INTO rosters(name) VALUES('乙组')").lastrowid

    def add(rid, name, letters):
        cur = conn.execute(
            "INSERT INTO persons(roster_id, name, code, perm_ids) VALUES(?,?,?,?)",
            (rid, name, "", db._dumps([perms[l] for l in letters])),
        )
        return cur.lastrowid

    p0 = add(rid_a, "甲0", ["A", "E"])
    p1 = add(rid_a, "甲1", ["E"])
    p2 = add(rid_a, "甲2", ["E"])
    p3 = add(rid_a, "甲3", ["B"])
    p4 = add(rid_a, "甲4", ["E"])
    p5 = add(rid_b, "乙0", ["E"])
    conn.commit()

    t1 = make_task(perms, rid_a, 1)
    pos1 = conn.execute(
        "SELECT po.id, po.slot_id FROM positions po JOIN slots s ON s.id=po.slot_id WHERE s.task_id=?", (t1,)
    ).fetchone()
    t2 = make_task(perms, rid_a, 1)
    pos2 = conn.execute(
        "SELECT po.id, po.slot_id FROM positions po JOIN slots s ON s.id=po.slot_id WHERE s.task_id=?", (t2,)
    ).fetchone()
    d = "2026-10-10"
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (t1, d, pos1["slot_id"], pos1["id"], p0))
    conn.execute("INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,NULL)",
                 (t2, d, pos2["slot_id"], pos2["id"], p1))
    conn.commit()

    rows = server.get_stats({"year": ["2026"]})[1]
    stats = {r["person_id"]: r for r in rows}
    check("统计行含名单名", stats[p0]["roster_name"] == "甲组" and stats[p5]["roster_name"] == "乙组",
          "%s / %s" % (stats[p0]["roster_name"], stats[p5]["roster_name"]))

    by_task = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    check("统计按排班表分组", set(by_task) == {t1, t2, 0}, str(list(by_task)))
    check("未参与排班的人员单独成组",
          any(r["task_id"] == 0 and r["person_id"] == p5 for r in rows), "")
    check("组内为该排班表名单的全部人员",
          set(r["person_id"] for r in by_task[t1]) == {p0, p1, p2, p3, p4},
          str(sorted(r["person_id"] for r in by_task[t1])))
    p0_t1 = next(r for r in by_task[t1] if r["person_id"] == p0)
    p0_t2 = next(r for r in by_task[t2] if r["person_id"] == p0)
    check("分数按排班表分开统计(不跨表合并)",
          p0_t1["task_name"] == "T" and p0_t1["count"] == 1 and p0_t2["count"] == 0,
          "t1.count=%s t2.count=%s" % (p0_t1["count"], p0_t2["count"]))

    att = server.get_attendance({"date": [d]})[1]
    check("考勤 groups 带名单信息",
          bool(att["groups"]) and all(g["roster_name"] == "甲组" and g["roster_id"] == rid_a for g in att["groups"]),
          str([g["roster_name"] for g in att["groups"]]))

    server.post_attendance_range({"person_id": p0, "start": d, "end": d, "kind": "a"})
    server.post_attendance_range({"person_id": p4, "start": d, "end": d, "kind": "b", "reason": "X"})
    att2 = server.get_attendance({"date": [d]})[1]
    ext = {r["person_id"]: r for r in att2["extra_records"]}
    check("无排班考勤记录带名单信息", p4 in ext and ext[p4]["roster_name"] == "甲组", str(ext.get(p4)))

    cand = sc.suggest_covers(conn, t1, d, pos1["id"])
    ids = [c["person_id"] for c in cand]
    check("候选=有权限者(排除底班/无权限/不在岗)", set(ids) == {p1, p2},
          str([(c["name"], c["busy"], c["risky"], c["suggested"]) for c in cand]))
    check("恰有一名建议人员", sum(1 for c in cand if c["suggested"]) == 1, "")
    sug = [c for c in cand if c["suggested"]][0]
    check("建议=可顶班且总班次最少者", sug["person_id"] == p2 and sug["eligible"] and not sug["busy"], str(sug))
    check("当天已排者标注 busy 且不可选",
          any(c["person_id"] == p1 and c["busy"] and not c["eligible"] for c in cand), "")
    check("建议排在最前", ids[0] == p2, str(ids))
    conn.close()


def scenario_cover_override():
    print("== 顶班：已排/冲突人员可选（同任务自动移入），跨任务拦截")
    conn, perms, cats = fresh("cover.db")
    rid = make_roster(conn, perms, cats, 5)
    t1 = make_task(perms, rid, 2)
    t2 = make_task(perms, rid, 2)
    t3 = make_task(perms, rid, 1)

    def pos_by_perm(tid, pname):
        return conn.execute(
            "SELECT po.id, po.slot_id FROM positions po JOIN slots s ON s.id=po.slot_id "
            "WHERE s.task_id=? AND po.perm_id=?", (tid, perms[pname])
        ).fetchone()

    a1 = pos_by_perm(t1, "E")
    a2 = pos_by_perm(t1, "D")
    b1 = pos_by_perm(t2, "E")
    c1 = pos_by_perm(t3, "E")
    pids = [r["id"] for r in conn.execute("SELECT id FROM persons ORDER BY id")]
    p1, p2, p3, p4, p5 = pids[0], pids[1], pids[2], pids[3], pids[4]
    d, yd = "2026-10-10", "2026-10-09"

    def ins(tid, date, pos, pid, cover=None):
        conn.execute(
            "INSERT INTO schedules(task_id,date,slot_id,position_id,person_id,cover_of) VALUES(?,?,?,?,?,?)",
            (tid, date, pos["slot_id"], pos["id"], pid, cover),
        )

    ins(t1, d, a1, p1)            # 底班
    ins(t1, d, a2, p2)            # p2 同任务当天已排
    ins(t2, d, b1, p3)            # p3 其他任务当天已排
    ins(t3, d, c1, p1)            # t3 底班
    ins(t3, yd, c1, p4)           # p4 昨天同班次
    conn.commit()

    sc.apply_cover(conn, t1, d, a1["id"], p2)
    moved = conn.execute(
        "SELECT position_id, cover_of FROM schedules WHERE task_id=? AND date=? AND person_id=?", (t1, d, p2)
    ).fetchall()
    check("同任务已排人员可顶班（自动移入）",
          len(moved) == 1 and moved[0]["position_id"] == a1["id"] and moved[0]["cover_of"] == p1,
          str([dict(m) for m in moved]))
    check("原岗位已释放",
          conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND position_id=? AND person_id=?",
                       (t1, d, a2["id"], p2)).fetchone()["c"] == 0, "")

    ins(t1, d, a2, p5)            # 为跨任务用例重置 a2 底班（步骤1已移走 p2）
    err = None
    try:
        sc.apply_cover(conn, t1, d, a2["id"], p3)
    except ValueError as e:
        err = str(e)
    check("跨任务已排人员被拦截", err is not None and "其他任务" in err, str(err))
    check("跨任务拦截未破坏其原排班",
          conn.execute("SELECT COUNT(*) c FROM schedules WHERE task_id=? AND date=? AND person_id=?",
                       (t2, d, p3)).fetchone()["c"] == 1, "")

    err = None
    ok = True
    try:
        sc.apply_cover(conn, t3, d, c1["id"], p4)
    except ValueError as e:
        ok, err = False, str(e)
    check("昨天同班次人员也可顶班（不再拦截）", ok, str(err))
    conn.close()


def main():
    global TMP
    TMP = tempfile.mkdtemp(prefix="workday_selftest_")
    print("临时目录: %s\n" % TMP)
    try:
        scenario_fairness("10人6岗", 10, 6)
        scenario_fairness("12人6岗", 12, 6)
        scenario_fairness("4人6岗(缺人)", 4, 6)
        scenario_fairness("10人6岗x3月", 10, 6, months=3)
        scenario_leave()
        scenario_cross_task()
        scenario_config()
        scenario_move()
        scenario_group_and_cover()
        scenario_cover_override()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    failed = [r for r in RESULTS if not r[1]]
    print("\n共 %d 项，通过 %d 项，失败 %d 项" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
    for name, _, detail in failed:
        print("  FAIL: %s %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
