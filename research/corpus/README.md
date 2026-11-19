# ERC-LawQA corpus pipeline

This pipeline builds the corpus of ERC regulations used in the research papers (Paper A, reference-aware hybrid retrieval). It turns the files from the legal team (Word / PDF from Google Drive) into retrieval units, one per มาตรา or ข้อ. Unit IDs follow the labelling guide:

```
T<registry row>@<B.E. year>#<unit>      e.g.  T220@2551#ข้อ8   T366@2552#ข้อ9
ACT2550@2550#ม<n>                     Energy Industry Act B.E. 2550, e.g. ACT2550@2550#ม121
```

> **Data is not committed to this repo.** The raw files, the extracted text, `units.jsonl` and the office registry (`legal-files-inventory.xlsx`) are internal data. `.gitignore` already blocks `data/` and `*.xlsx`. Run the pipeline in your own `work/` directory outside the repo.

## Steps

```bash
pip install -r requirements.txt          # .doc files also need LibreOffice (soffice)
export PYTHONPATH=research/corpus

python -m erc_corpus.unzip    raw  drive-download-*.zip            # 0 unpack, decode Thai file names, manifest.csv
python -m erc_corpus.extract  raw  work                            # 1 pick the best file per document + normalise text
python -m erc_corpus.segment  work                                 # 2 split into มาตรา/ข้อ + metadata
python -m erc_corpus.registry work legal-files-inventory.xlsx      # 3 link to registry rows → T### ids
python -m erc_corpus.qa       work                                 # 4 work/qc_report.md + spot-check sample
```

Outputs: `work/docs.jsonl` (one line per document) and `work/units.jsonl` (one line per unit). Unit fields are `id, doc_id, unit, kind, number, path, text, text_flat`.

## Rules worth knowing

| Step | Rule |
|---|---|
| extract | File preference: `.docx` > `.doc` > text PDF. A PDF with fewer than 100 Thai characters per page, a Thai-character ratio under 0.6, or a tone-mark ratio under 0.03 is flagged `needs_ocr`. |
| textnorm | Fixes Thai PDF PUA glyphs (U+F700–F71A) and `ํา`→`ำ`. Fixes "ก ากับ"→"กำกับ" (from Word→PDF exports). Removes the Krisdika watermark. |
| extract | Word files whose clauses use automatic numbering get their labels rebuilt (`ข้อ n`, `(n)`). |
| segment | Acts and royal decrees split on มาตรา; other instruments split on ข้อ only. A number is accepted only if it is the next in sequence, or `/n` of the current one, so a cross-reference that wraps to the start of a line is not a new unit. Text inside “ ” (amending text) is never split. |
| segment | Footnote markers glued to section numbers in Krisdika texts ("มาตรา ๒๑" = ม.2 + footnote 1) are removed. |
| segment | An instrument with no numbered clauses (fee schedule, charter) becomes a single unit, `#ทั้งฉบับ`. |
| segment | `text_flat` joins lines wrapped inside a phrase without a space and keeps paragraph (วรรค) breaks, for BM25 and embeddings. |
| registry | Matching order: registry number (= folder) + file name → registry number alone → title similarity ≥ 0.85. A second copy of the same version is flagged `duplicate_of` and its units are dropped. A second edition in the same year gets `~ฉn`. |

## Status of the first round (19 Nov 2569)

- 403 documents: 267 with text extracted, 116 waiting for OCR, 20 duplicate copies dropped.
- 3,751 units. The Act has 155 มาตรา (ม.1–ม.155) in order. 0 duplicate IDs, 0 numbering gaps.
- 281 of the 318 in-force registry rows are linked to a file.

## Next

1. OCR round for scanned files: tesseract `tha`, then validate Thai digits (๒/๖, ๔/๕).
2. Apply amendments to build point-in-time versions, so a unit ID can name the amended version, e.g. `T220@2557#ข้อ8(3)ว2`.
3. Split very long units (annexes, fee schedules) into chunks for indexing.
4. Index with BM25 + BGE-M3 and run the NitiBench-CCL baseline.
