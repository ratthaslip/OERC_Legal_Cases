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
        r["cited_hit"] = bool(exp & cited_eli)
    if item.get("expected_title_contains"):
        t = item["expected_title_contains"]
        r["source_hit@5"] = any(t in (s.get("title") or "") for s in sources[:5])
        r["cited_hit"] = any(t in (s.get("title") or "") for s in sources if s["key"] in cited)
    r["citation_rate"] = (sum(1 for s in sentences if s["citations"]) / len(sentences)) if sentences else None
    forb = set(item["must_not_cite"])
    kinds = set(item["must_not_kinds"])
    r["forbidden_ok"] = not (forb & {s.get("eli_id") for s in sources}) and not any(s.get("kind") in kinds for s in sources)
    return r


def summarize(rows: list[dict]) -> dict:
    def rate(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return (round(100 * sum(vals) / len(vals), 1), len(vals)) if vals else (None, 0)
    cr = [r["citation_rate"] for r in rows if r["citation_rate"] is not None]
    return {"n": len(rows), "status_ok_%": rate("status_ok"), "source_hit@5_%": rate("source_hit@5"),
            "cited_hit_%": rate("cited_hit"), "forbidden_ok_%": rate("forbidden_ok"),
            "citation_rate_%": round(100 * sum(cr) / len(cr), 1) if cr else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--inproc", action="store_true")
    ap.add_argument("--only", help="รหัสข้อคั่นด้วย , เช่น G01,G05")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--out", default=str(HERE / "results.json"))
    args = ap.parse_args()

    items = json.loads((HERE / "golden_set.json").read_text(encoding="utf-8"))
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
        flag = "✓" if res["status_ok"] and res.get("cited_hit", True) and res["forbidden_ok"] else "✗"
        print(f"{flag} {it['id']} {res['status']:<12} hit@5={res.get('source_hit@5', '-')!s:<5} "
              f"cited={res.get('cited_hit', '-')!s:<5} {it['question'][:60]}")
    summ = summarize(rows)
    Path(args.out).write_text(json.dumps({"summary": summ, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))


if __name__ == "__main__":
    main()
