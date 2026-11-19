"""Step 4: corpus QC report (work/qc_report.md) plus a random spot-check sample.

    python -m erc_corpus.qa work [--sample 30] [--seed 7]
"""
import argparse, collections, json, pathlib, random, re


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    work = pathlib.Path(args.work)
    docs = [json.loads(l) for l in open(work / "docs.jsonl", encoding="utf-8")]
    units = [json.loads(l) for l in open(work / "units.jsonl", encoding="utf-8")]

    live = [d for d in docs if not d.get("duplicate_of")]
    text = [d for d in live if not d.get("needs_ocr")]
    ocr = [d for d in live if d.get("needs_ocr")]
    ids = collections.Counter(u["id"] for u in units)
    lens = sorted(len(u["text_flat"]) for u in units)
    pct = lambda p: lens[int(len(lens) * p)]
    broken = sum(len(re.findall(r"[ก-ฮ]\sา", u["text"])) for u in units)
    pua = sum(1 for u in units if re.search("[-]", u["text"]))

    R = []
    w = R.append
    w("# ERC-LawQA corpus — QC report\n")
    w("## Documents\n")
    w("| | count |\n|---|---|")
    w(f"| source documents (after grouping formats of the same file) | {len(docs)} |")
    w(f"| duplicate copies dropped (same version in another file) | {len(docs) - len(live)} |")
    w(f"| text extracted | {len(text)} |")
    w(f"| waiting for OCR (scanned or broken font encoding) | {len(ocr)} |")
    m = collections.Counter(d.get("method") for d in text)
    w(f"| … from Word (.docx / .doc) | {m['docx'] + m['doc']} |")
    w(f"| … from text PDF | {m['pdf_text']} |")
    w("")
    w("## Registry linkage\n")
    rm = collections.Counter(d["registry_match"] for d in live)
    w("| match rule | documents |\n|---|---|")
    for k in ("folder+file", "folder", "title", "act", "unmatched"):
        w(f"| {k} | {rm.get(k, 0)} |")
    un = [d for d in live if d["registry_match"] == "unmatched"]
    if un:
        w("\nUnmatched (not in the registry — to confirm with the legal team):\n")
        for d in un:
            w(f"- `{d.get('source') or d['files'][0]}`")
    w("")
    w("## Metadata found in extracted documents\n")
    w("| field | documents | share |\n|---|---|---|")
    for f, lab in (("year_be", "B.E. year in title"), ("signed_date", "signing date"),
                   ("gazette_date", "Gazette date (header)"), ("effective_date", "effective date (derived)")):
        n = sum(1 for d in text if d.get(f))
        w(f"| {lab} | {n} | {n / len(text):.0%} |")
    w("")
    w("## Units\n")
    k = collections.Counter(u["kind"] for u in units)
    w("| | count |\n|---|---|")
    w(f"| units total | {len(units)} |")
    w(f"| ข้อ | {k['ข้อ']} |")
    w(f"| มาตรา | {k['มาตรา']} |")
    w(f"| preamble (อารัมภบท) | {k['preamble']} |")
    w(f"| whole document (no numbered clauses) | {k['whole']} |")
    w(f"| duplicate unit ids | {sum(1 for v in ids.values() if v > 1)} |")
    w(f"| documents with numbering gaps | {sum(1 for d in text if d.get('seq_gaps'))} |")
    w(f"| unit length, characters (p10 / median / p90 / max) | {pct(.1)} / {pct(.5)} / {pct(.9)} / {lens[-1]} |")
    w(f"| leftover PUA glyphs | {pua} units |")
    w(f"| leftover split ำ (\"ก ากับ\") | {broken} occurrences |")
    act = [u for u in units if u["doc_id"] == "ACT2550" and u["kind"] == "มาตรา"]
    w(f"| พ.ร.บ. การประกอบกิจการพลังงาน 2550: มาตรา found | {len(act)} (ม.{act[0]['number']}–ม.{act[-1]['number']}) |" if act else "| Act | missing |")
    w("")
    w(f"## Spot-check sample ({args.sample} units, seed {args.seed})\n")
    w("Compare each row with the source file; mark ✔ or note the problem.\n")
    w("| unit id | path | start of text | ok? |\n|---|---|---|---|")
    rng = random.Random(args.seed)
    for u in rng.sample([u for u in units if u["kind"] in ("ข้อ", "มาตรา")], args.sample):
        w(f"| `{u['id']}` | {' › '.join(u['path'])} | {u['text_flat'][:80]}… | |")
    (work / "qc_report.md").write_text("\n".join(R) + "\n", encoding="utf-8")
    print("\n".join(R[:60]))


if __name__ == "__main__":
    main()
