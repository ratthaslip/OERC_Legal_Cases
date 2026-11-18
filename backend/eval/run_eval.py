"""ประเมินผู้ช่วย AI ด้วยชุดคำถามทดสอบ (golden_set.json)

ใช้ได้ 2 แบบ:
  python -m eval.run_eval --base http://localhost:8000          # ยิง API ที่รันอยู่ (เช่นบนเซิร์ฟเวอร์)
  python -m eval.run_eval --inproc                              # เรียกแอปในโปรเซส (ต้องตั้ง DATABASE_URL)
ผลลัพธ์: eval/results.json และสรุปตัวชี้วัดบนหน้าจอ

ตัวชี้วัด (ตรวจอัตโนมัติ ไม่ใช้ LLM ตัดสิน):
  status_ok      — สถานะ ANSWERED/NO_EVIDENCE ตรงที่คาด
  source_hit@5   — แหล่งที่ถูกต้องอย่างน้อย 1 รายการอยู่ใน 5 แหล่งแรกที่ระบบดึงมา
  cited_hit      — ประโยคคำตอบอ้าง [S#] ที่ชี้ไปยังแหล่งที่ถูกต้องอย่างน้อย 1 ครั้ง
  citation_rate  — สัดส่วนประโยคที่มีอ้างอิง (ต้องเป็น 100%)
  forbidden_ok   — ไม่อ้างแหล่งต้องห้าม (ตัวบทที่ยังไม่ใช้บังคับ ณ วันที่ถาม / สำนวนที่ผู้ใช้ไม่มีสิทธิ์)
ความถูกต้องของเนื้อคำตอบ (เทียบ reference_answer) ให้นิติกรรับรองในไฟล์ golden-qa-60.xlsx
"""
import argparse
import sys
import json
import time
from pathlib import Path

HERE = Path(__file__).parent


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        if not block.startswith("event:"):
            continue
        name, data = block.split("\n", 1)
        out.append((name[7:].strip(), json.loads(data[6:])))
    return out


def make_caller(args):
    if args.inproc:
        from fastapi.testclient import TestClient

        from app.main import app
        c = TestClient(app)

        def call(item):
            body = {"question": item["question"], **({"as_of": item["as_of"]} if item["as_of"] else {})}
            return c.post("/api/v1/assistant/answers", json=body, headers={"X-Demo-User": item["user"]}).text
        return call
    import httpx

    def call(item):
        body = {"question": item["question"], **({"as_of": item["as_of"]} if item["as_of"] else {})}
        r = httpx.post(f"{args.base.rstrip('/')}/api/v1/assistant/answers", json=body,
                       headers={"X-Demo-User": item["user"]}, timeout=args.timeout)
        return r.text
    return call


def score(item: dict, events: list[tuple[str, dict]]) -> dict:
    done = next((d for n, d in events if n == "done"), {})
    err = next((d for n, d in events if n == "error"), None)
    sources = next((d["sources"] for n, d in events if n == "sources"), [])
    sentences = [d for n, d in events if n == "sentence"]
    status = done.get("status") or ("ERROR" if err else "?")
    key_eli = {s["key"]: (s.get("eli_id") or "") for s in sources}
    exp = set(item["expected_sources"])
    top5 = [s.get("eli_id") for s in sources[:5]]
    cited = {k for s in sentences for k in s["citations"]}
    cited_eli = {key_eli.get(k) for k in cited}

    r = {"id": item["id"], "status": status, "n_sources": len(sources), "n_sentences": len(sentences),
         "top_sources": [f"{s.get('kind')}:{s.get('eli_id') or s.get('title')}" for s in sources[:5]],
         "answer": " ".join(s["text"] for s in sentences)[:1500], "error": err}
    want = item["expected_status"]
    r["status_ok"] = status == want if want != "ANY" else status != "ERROR"
    if exp:
        r["source_hit@5"] = bool(exp & set(top5))
        r["cited_hit"] = exp <= cited_eli if item.get("require_all") else bool(exp & cited_eli)
    if item.get("expected_title_contains"):
        t = item["expected_title_contains"]
        r["source_hit@5"] = any(t in (s.get("title") or "") for s in sources[:5])
        r["cited_hit"] = any(t in (s.get("title") or "") for s in sources if s["key"] in cited)
    if item.get("must_include"):
        from app.search.thai import normalize
        full = normalize(" ".join(s["text"] for s in sentences))
        r["answer_ok"] = all(normalize(m) in full for m in item["must_include"])
    r["citation_rate"] = (sum(1 for s in sentences if s["citations"]) / len(sentences)) if sentences else None
    forb = set(item["must_not_cite"])
    kinds = set(item["must_not_kinds"])
    r["forbidden_ok"] = not (forb & {s.get("eli_id") for s in sources}) and not any(s.get("kind") in kinds for s in sources)
    return r


def passed(r: dict) -> bool:
    return bool(r["status_ok"] and r.get("cited_hit", True) and r["forbidden_ok"] and r.get("answer_ok", True))


def summarize(rows: list[dict]) -> dict:
    def rate(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return (round(100 * sum(vals) / len(vals), 1), len(vals)) if vals else (None, 0)
    cr = [r["citation_rate"] for r in rows if r["citation_rate"] is not None]
    return {"n": len(rows), "status_ok_%": rate("status_ok"), "source_hit@5_%": rate("source_hit@5"),
            "cited_hit_%": rate("cited_hit"), "forbidden_ok_%": rate("forbidden_ok"),
            "answer_ok_%": rate("answer_ok"),
            "passed": f"{sum(passed(r) for r in rows)}/{len(rows)}",
            "citation_rate_%": round(100 * sum(cr) / len(cr), 1) if cr else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--inproc", action="store_true")
    ap.add_argument("--only", help="รหัสข้อคั่นด้วย , เช่น G01,G05")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--out", default=str(HERE / "results.json"))
    ap.add_argument("--mode", choices=["auto", "public", "full"], default="auto",
                    help="public = ไม่มีทะเบียนจริง (CI) ข้ามข้อที่ต้องใช้ทะเบียน; auto = ตรวจจากฐานข้อมูล (เฉพาะ --inproc)")
    ap.add_argument("--baseline", help="ไฟล์ baseline.json — ล้มเหลว (exit 1) ถ้าข้อที่เคยผ่านกลับไม่ผ่าน")
    ap.add_argument("--write-baseline", action="store_true", help="บันทึกข้อที่ผ่านรอบนี้ลง --baseline ตามโหมด")
    args = ap.parse_args()

    items = json.loads((HERE / "golden_set.json").read_text(encoding="utf-8"))
    mode = resolve_mode(args)
    if mode == "public":
        skipped = [i["id"] for i in items if i.get("data") == "register"]
        items = [i for i in items if i.get("data") != "register"]
        print(f"โหมด public: ข้าม {len(skipped)} ข้อที่ต้องใช้ทะเบียนจริง")
    if args.only:
        keep = set(args.only.split(","))
        items = [i for i in items if i["id"] in keep]
    call = make_caller(args)
    rows = []
    for it in items:
        t0 = time.time()
        res = score(it, parse_sse(call(it)))
        res["latency_s"] = round(time.time() - t0, 2)
        res["category"] = it["category"]
        rows.append(res)
        flag = "✓" if passed(res) else "✗"
        print(f"{flag} {it['id']} {res['status']:<12} hit@5={res.get('source_hit@5', '-')!s:<5} "
              f"cited={res.get('cited_hit', '-')!s:<5} {it['question'][:60]}")
    summ = summarize(rows)
    summ["mode"] = mode
    Path(args.out).write_text(json.dumps({"summary": summ, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    if args.baseline:
        sys.exit(check_baseline(Path(args.baseline), mode, rows, write=args.write_baseline))


def resolve_mode(args) -> str:
    if args.mode != "auto":
        return args.mode
    if not args.inproc:
        return "full"
    from app.db import get_engine
    from sqlalchemy import text
    with get_engine().connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM law_register")).scalar()
    return "full" if n else "public"


def check_baseline(path: Path, mode: str, rows: list[dict], write: bool = False) -> int:
    """ข้อที่ผ่านใน baseline ต้องยังผ่าน (ข้อใหม่ที่ผ่านเพิ่มไม่ถือว่าผิด) — ใช้กันคะแนนถอยหลังใน CI"""
    base = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now = sorted(r["id"] for r in rows if passed(r))
    if write:
        base[mode] = now
        path.write_text(json.dumps(base, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"บันทึก baseline โหมด {mode}: ผ่าน {len(now)} ข้อ → {path}")
        return 0
    expected = set(base.get(mode, []))
    ran = {r["id"] for r in rows}
    regressed = sorted((expected & ran) - set(now))
    improved = sorted(set(now) - expected)
    print(f"เทียบ baseline โหมด {mode}: เคยผ่าน {len(expected & ran)} ข้อ, ตอนนี้ผ่าน {len(now)} ข้อ")
    if improved:
        print("ผ่านเพิ่ม (อัปเดต baseline ด้วย --write-baseline):", ", ".join(improved))
    if regressed:
        print("❌ คะแนนถอยหลัง — ข้อที่เคยผ่านแต่ตอนนี้ไม่ผ่าน:", ", ".join(regressed))
        return 1
    return 0


if __name__ == "__main__":
    main()
