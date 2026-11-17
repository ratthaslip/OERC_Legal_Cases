"""เทียบผลประเมิน 2 รอบรายข้อ เช่น ก่อน/หลังเปิด reranker

  python -m eval.compare eval/results_none.json eval/results_tei.json
"""
import json
import sys


def ok(r):
    return r["status_ok"] and r.get("cited_hit", True) and r["forbidden_ok"]


def main(a_path, b_path):
    a, b = (json.load(open(p, encoding="utf-8")) for p in (a_path, b_path))
    ra, rb = ({r["id"]: r for r in x["rows"]} for x in (a, b))
    better = [k for k in ra if not ok(ra[k]) and ok(rb[k])]
    worse = [k for k in ra if ok(ra[k]) and not ok(rb[k])]
    for k in ("status_ok_%", "source_hit@5_%", "cited_hit_%", "citation_rate_%"):
        print(f"{k:<18} {a['summary'][k]!s:<14} → {b['summary'][k]}")
    print(f"ดีขึ้น {len(better)} ข้อ: {', '.join(better) or '-'}")
    print(f"แย่ลง {len(worse)} ข้อ: {', '.join(worse) or '-'}")
    print(f"ยังไม่ผ่าน: {', '.join(k for k in rb if not ok(rb[k])) or '-'}")
    lat = [r["latency_s"] for r in b["rows"]]
    print(f"เวลาตอบเฉลี่ย {sum(r['latency_s'] for r in a['rows'])/len(a['rows']):.2f}s → {sum(lat)/len(lat):.2f}s")


if __name__ == "__main__":
    main(*sys.argv[1:3])
