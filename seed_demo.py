# -*- coding: utf-8 -*-
"""一键生成本地测试数据。

内容：
  1. 名单「值班一组」+ 10 名人员（覆盖 一类~六类、权限 A~E 的梯度）
  2. 任务「日常值班排班」：3 个班次时段 × 2 个岗位
  3. 调用排班算法生成指定月份的整月排班
  4. 最近几天的考勤记录（A事/B事/缺勤）+ 一次顶班

用法：
  python3 seed_demo.py                     # 仅在空库时写入（已有名单/任务则拒绝）
  python3 seed_demo.py --force             # 清空业务数据后重新生成
  python3 seed_demo.py --month 11          # 指定生成的月份（默认当前月）
  python3 seed_demo.py --force --db dist/data.db     # 写入指定数据库
"""
import argparse
import datetime
import os
import sys

import db
import scheduler as sc
import server

# (姓名, 工号, 手机号, 类别名, 权限字母列表, 偏好权限字母)
PERSONS = [
    ("张伟", "EMP001", "13800000001", "一类", ["A", "B", "C", "D", "E"], "A"),
    ("李娜", "EMP002", "13800000002", "一类", ["A", "B", "C", "D", "E"], "B"),
    ("王强", "EMP003", "13800000003", "二类", ["B", "C", "D", "E"], "B"),
    ("赵敏", "EMP004", "13800000004", "二类", ["B", "C", "D", "E"], "C"),
    ("刘洋", "EMP005", "13800000005", "三类", ["C", "D", "E"], "C"),
    ("陈静", "EMP006", "13800000006", "三类", ["C", "D", "E"], "D"),
    ("杨帆", "EMP007", "13800000007", "四类", ["D", "E"], "D"),
    ("黄磊", "EMP008", "13800000008", "四类", ["D", "E"], "E"),
    ("周涛", "EMP009", "13800000009", "五类", ["E"], "E"),
    ("吴琼", "EMP010", "13800000010", "六类", ["E"], "E"),
]

# 3 个班次 × 2 个岗位；岗位权限有意做成梯度：
#   E(全员) / D(8人) / C(6人) / 无限制，便于验证权限过滤
SLOTS = [
    {
        "start": "00:00",
        "end": "08:00",
        "positions": [
            {"name": "夜班值守", "perm": "E"},
            {"name": "门岗巡逻", "perm": "D"},
        ],
    },
    {
        "start": "08:00",
        "end": "16:00",
        "positions": [
            {"name": "前台接待", "perm": "C"},
            {"name": "设备巡检", "perm": "D"},
        ],
    },
    {
        "start": "16:00",
        "end": "24:00",
        "positions": [
            {"name": "前台接待", "perm": "C"},
            {"name": "机动支援", "perm": None},
        ],
    },
]

ROSTER_NAME = "值班一组"
TASK_NAME = "日常值班排班"

PERSONS2 = [
    ("孙磊", "EMP101", "13800000011", "一类", ["A", "B", "C", "D", "E"], "A"),
    ("钱进", "EMP102", "13800000012", "二类", ["B", "C", "D", "E"], "B"),
    ("周敏", "EMP103", "13800000013", "三类", ["C", "D", "E"], "C"),
    ("郑浩", "EMP104", "13800000014", "四类", ["D", "E"], "D"),
    ("冯雪", "EMP105", "13800000015", "五类", ["E"], "E"),
    ("陈晨", "EMP106", "13800000016", "六类", ["E"], "E"),
]

SLOTS2 = [
    {
        "start": "08:00",
        "end": "16:00",
        "positions": [
            {"name": "增援前台", "perm": "D"},
            {"name": "增援门岗", "perm": "E"},
        ],
    },
    {
        "start": "16:00",
        "end": "24:00",
        "positions": [
            {"name": "夜间值守", "perm": "E"},
            {"name": "应急待命", "perm": None},
        ],
    },
]

ROSTER_NAME2 = "值班二组"
TASK_NAME2 = "机动支援排班"


def _wipe(conn):
    for sql in (
        "DELETE FROM schedules",
        "DELETE FROM attendance",
        "DELETE FROM positions",
        "DELETE FROM slots",
        "DELETE FROM tasks",
        "DELETE FROM persons",
        "DELETE FROM rosters",
    ):
        conn.execute(sql)
    conn.commit()


def _perm_index(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM permissions")}


def _category_index(conn):
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}


def seed_roster(conn, perms, cats, name=ROSTER_NAME, persons=PERSONS):
    cur = conn.execute("INSERT INTO rosters(name) VALUES(?)", (name,))
    roster_id = cur.lastrowid
    for pname, code, phone, cat, letters, pref in persons:
        conn.execute(
            "INSERT INTO persons(roster_id, name, code, phone, category_id, perm_ids, "
            "preferred_perm_id, remark) VALUES(?,?,?,?,?,?,?,?)",
            (
                roster_id,
                pname,
                code,
                phone,
                cats.get(cat),
                db._dumps([perms[l] for l in letters if l in perms]),
                perms.get(pref),
                "测试数据",
            ),
        )
    conn.commit()
    return roster_id


def seed_task(conn, roster_id, perms, name=TASK_NAME, slots=SLOTS):
    body = {
        "name": name,
        "roster_id": roster_id,
        "slots": [
            {
                "start": s["start"],
                "end": s["end"],
                "positions": [
                    {"name": p["name"], "perm_id": perms.get(p["perm"]) if p["perm"] else None}
                    for p in s["positions"]
                ],
            }
            for s in slots
        ],
    }
    status, data = server.post_tasks(body)
    if status != 200:
        raise RuntimeError("创建任务失败: %s" % data)
    return data["id"]


def seed_group2(conn, perms, cats):
    row = conn.execute(
        "SELECT t.id FROM tasks t JOIN rosters r ON r.id=t.roster_id "
        "WHERE r.name=? AND t.name=?",
        (ROSTER_NAME2, TASK_NAME2),
    ).fetchone()
    if row:
        print("名单「%s」/任务「%s」已存在，跳过创建" % (ROSTER_NAME2, TASK_NAME2))
        return row["id"]
    roster_id = seed_roster(conn, perms, cats, ROSTER_NAME2, PERSONS2)
    print("名单「%s」+ %d 名人员 已创建" % (ROSTER_NAME2, len(PERSONS2)))
    task_id = seed_task(conn, roster_id, perms, TASK_NAME2, SLOTS2)
    print("任务「%s」已创建（%d 班次 × 2 岗位）" % (TASK_NAME2, len(SLOTS2)))
    return task_id


def seed_attendance_and_cover(task_id, year, month, today):
    """对最近 3 个有排班的工作日各登记一条考勤，并为缺勤者安排顶班。"""
    conn = db.get_conn()
    kinds = [("a", "家中有事"), ("b", "身体不适"), ("absent", "临时缺勤")]
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM schedules WHERE task_id=? AND date>=? AND date<? "
            "ORDER BY date DESC",
            (task_id, "%04d-%02d-01" % (year, month), today.isoformat()),
        ).fetchall()
        dates = [r["date"] for r in rows[:3]]
        applied = []
        for date, (kind, reason) in zip(reversed(dates), kinds):
            row = conn.execute(
                "SELECT person_id, position_id FROM schedules "
                "WHERE task_id=? AND date=? AND cover_of IS NULL ORDER BY id LIMIT 1",
                (task_id, date),
            ).fetchone()
            if not row:
                continue
            status, data = server.post_attendance(
                {"person_id": row["person_id"], "date": date, "kind": kind, "reason": reason}
            )
            if status != 200:
                print("  考勤登记失败 %s: %s" % (date, data))
                continue
            applied.append((date, kind, row["person_id"]))
            if kind == "absent":
                cover_pid = _first_cover(conn, task_id, date, row["position_id"])
                if not cover_pid:
                    print("  %s 缺勤无可顶班人选" % date)
                    continue
                status, data = server.cover_apply(
                    {
                        "task_id": task_id,
                        "date": date,
                        "position_id": row["position_id"],
                        "person_id": cover_pid,
                    }
                )
                if status == 200:
                    print("  %s 缺勤已顶班" % date)
                else:
                    print("  %s 顶班未安排: %s" % (date, data))
        return applied
    finally:
        conn.close()


def _first_cover(conn, task_id, date, position_id):
    cands = sc.suggest_covers(conn, task_id, date, position_id, limit=1)
    return cands[0]["person_id"] if cands else None


def summary(task_id, year, month):
    status, tasks = server.get_tasks()
    print("\n== 汇总 ==")
    print("任务: %s" % [t["name"] for t in tasks])
    status, sched = server.get_schedule(
        task_id, {"year": ["%d" % year], "month": ["%d" % month]}
    )
    cells = sched["cells"] if status == 200 else {}
    total = sum(len(v) for day in cells.values() for v in day.values())
    print("排班: %d 月共 %d 天有排班，%d 条记录" % (month, len(cells), total))
    conn = db.get_conn()
    n_att = conn.execute("SELECT COUNT(*) c FROM attendance").fetchone()["c"]
    n_cover = conn.execute(
        "SELECT COUNT(*) c FROM schedules WHERE cover_of IS NOT NULL"
    ).fetchone()["c"]
    conn.close()
    print("考勤记录: %d 条，顶班记录: %d 条" % (n_att, n_cover))
    status, stats = server.get_stats({"year": ["%d" % year]})
    if status == 200:
        print("统计（按排班表分组，共 %d 个排班表）:" % len({s["task_id"] for s in stats}))
        for s in stats[:14]:
            print(
                "  %-14s %-6s 正常%2d  A事%2d  B事%2d  缺勤%2d  总分%.1f"
                % (
                    s["task_name"] or "未参与排班",
                    s["name"],
                    s["normal"],
                    s["a"],
                    s["b"],
                    s["absent"],
                    s["total"],
                )
            )


def _generate(task_id, year, month, label):
    result = sc.generate_month(task_id, year, month)
    print("%d年%d月「%s」排班已生成：%d 天，警告 %d 条"
          % (year, month, label, result["days"], len(result["warnings"])))
    for w in result["warnings"][:5]:
        print("  警告: %s" % w)
    return result


def main():
    ap = argparse.ArgumentParser(description="生成本地测试数据")
    ap.add_argument("--force", action="store_true", help="清空业务数据后重新生成")
    ap.add_argument("--month", type=int, default=None, help="生成排班的月份（默认当月）")
    ap.add_argument("--db", default=None, help="目标数据库文件（默认程序同目录 data.db）")
    ap.add_argument("--append", action="store_true",
                    help="仅追加第二组名单+排班任务并生成排班（不清空已有数据）")
    args = ap.parse_args()

    if args.db:
        db.DB_PATH = os.path.abspath(args.db)
    print("目标数据库: %s" % db.DB_PATH)

    db.init_db()
    today = datetime.date.today()
    year, month = today.year, args.month or today.month

    if args.append:
        conn = db.get_conn()
        try:
            perms = _perm_index(conn)
            cats = _category_index(conn)
            task2_id = seed_group2(conn, perms, cats)
        finally:
            conn.close()
        _generate(task2_id, year, month, TASK_NAME2)
        summary(task2_id, year, month)
        return

    conn = db.get_conn()
    try:
        has_data = (
            conn.execute("SELECT COUNT(*) c FROM rosters").fetchone()["c"]
            or conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        )
        if has_data:
            if not args.force:
                print("已有名单/任务数据，如需重新生成请加 --force")
                sys.exit(1)
            _wipe(conn)
            print("已清空业务数据")

        perms = _perm_index(conn)
        cats = _category_index(conn)
        roster_id = seed_roster(conn, perms, cats)
        print("名单「%s」+ %d 名人员 已创建" % (ROSTER_NAME, len(PERSONS)))

        task_id = seed_task(conn, roster_id, perms)
        print("任务「%s」已创建（%d 班次 × 2 岗位）" % (TASK_NAME, len(SLOTS)))

        task2_id = seed_group2(conn, perms, cats)
    finally:
        conn.close()

    _generate(task_id, year, month, TASK_NAME)
    _generate(task2_id, year, month, TASK_NAME2)

    seed_attendance_and_cover(task_id, year, month, today)
    summary(task_id, year, month)


if __name__ == "__main__":
    main()
