# Vocabulary Learning System — Historical Design Narrative (Non-Normative)

> **Source precedence:** machine-checkable invariants in `vocab/contracts.py` and `vocab/models.py` come first; accepted decisions in `DECISIONS.md` come second. This document preserves design history, lane guidance, build order, and earlier proposals. When it conflicts with those normative sources, it does **not** override them.

---

Trước khi vào từng bước, có một thứ phải nói vì nó quyết định toàn bộ cách bạn dùng Codex ở dự án này.

# §0 — Một quy tắc

**Codex không được viết** **`validators.py`** **và không được viết năm hợp đồng ở §1.**

Lý do không phải là nguyên tắc chung về AI-generated code. Nó là lý do kỹ thuật riêng của kiến trúc này: toàn bộ giá trị của #5 nằm ở chuỗi `AI Generate → STRICT SCHEMA → Deterministic Validators`. Validator tồn tại để **bắt lỗi của model**. Nếu model viết luôn validator, bạn có hai thành phần tương quan lỗi với nhau và tưởng là đã kiểm tra chéo. Đây đúng là lỗi mà một người review RAGTrust sẽ chỉ ra ngay: assessor và judge không được là cùng một nguồn.

Cùng logic đó áp cho câu trả lời này: tôi không viết `CONTRACTS.md` hộ bạn. §1 dưới đây là **bản nháp để bạn phản biện và sửa**, không phải bản để copy. Bạn sửa xong thì gửi lại, tôi review — đó mới là phân công đúng.

Ánh xạ sang lane router của bạn:

| Lane Ở dự án này        |                                                                               |
| ----------------------- | ----------------------------------------------------------------------------- |
| **A** — delegate 5 phút | boilerplate: HTTP client, argparse, JSONL I/O, SDK wrapper                    |
| **B** — tự làm 30 phút  | hợp đồng, validator, state machine, card template, prompt của judge           |
| **C** — học 75–95 phút  | mô hình note type/card template của Anki, revlog & FSRS, Azure assessment API |

---

# §1 — Năm hợp đồng phải đóng băng trước khi mở Codex

Đóng băng năm cái này trước, và Codex chỉ được implement ngược lại chúng. Đây là Design Freeze Gate của dự án.

### 1.1 `unit_key`

```
<lemma-slug>::<sense-slug>     lowercase, ascii, hyphen

pose-a-threat-to::create-danger
subtle::small-difference
subtle::delicate-flavour
notwithstanding::despite-formal

```

Là field đầu tiên của note type → Anki tự enforce uniqueness. `sense-slug` do **bạn duyệt một lần lúc tạo**, AI không được sinh lại.

Câu hỏi bạn phải tự trả lời: khi nào hai nghĩa đáng tách? Đề xuất của tôi — tách khi **collocation frame khác nhau**, không tách theo sắc thái nghĩa.

### 1.2 Note type

```
unit_key*        lemma          sense_slug     unit_type
Target_R  Target_L  Target_W  Target_S        ← rỗng = không sinh thẻ
register         definition_en  source_ref
Ctx_1 … Ctx_5    audio_1  audio_2  audio_3    VisualCue
state            freq_band      created  graduated

```

`{{#Target_S}}...{{/Target_S}}` trong front template → field rỗng thì Anki **không tạo thẻ đó**. Đây là chỗ #1 biến thành bộ điều tiết chi phí.

### 1.3 Event schema

```
{"v": 1, "ts": "2026-08-18T09:12:03+07:00",
 "event": "REVIEW", "unit_key": "...", "payload": {}}

```

`event` ∈ `REVIEW | JUDGE | FORGE | STATE | SPEAK | ENCOUNTER`. `v` bắt buộc — log append-only sống nhiều năm thì schema sẽ đổi, và bạn phải đọc được bản cũ. Event `JUDGE` và `SPEAK` bắt buộc có `model_id` + `model_version`.

### 1.4 State machine

```
NEW       → LEARNING   lượt review đầu tiên
LEARNING  → STABLE     mọi kênh target: interval ≥ 21d, 0 lapse trong 30d
STABLE    → MASTERED   + weekly session PASS trên novel context
MASTERED  → DORMANT    sau 30d ở MASTERED: suspend cards, xoá field audio/visual
DORMANT   → RELAPSE    ENCOUNTER + fail, hoặc corpus scan thấy dùng sai
RELAPSE   → LEARNING   reactivate CHỈ kênh đã fail
*         → LEARNING   Anki gắn tag:leech (lapse ≥ 4)

```

Ngưỡng `21d / 30d / 4 lapse` là **engineering parameter**, không phải chân lý. Ghi rõ trong file rằng chúng sẽ được calibrate lại sau 90 ngày.

### 1.5 FORGE output schema

Codex implement validator theo schema; **bạn viết schema**. Tối thiểu phải ràng buộc:

- `definition_en` — bắt buộc `source_ref`; từ chối nếu rỗng
- `Ctx_1..5` — bắt buộc khác nhau về chủ đề, **không được chứa** **`lemma`** **ở dạng nguyên văn của source sentence**
- `Target_*` — mặc định chỉ `R`; bật W/S phải kèm `justification`
- `register` ∈ enum đóng
- fail-closed: thiếu field → **reject cả Unit**, không tự điền

---

# §2 — Cây module

Ranh giới đặt sao cho mỗi task giao cho Codex là độc lập và test được.

```
vocab/
  models.py        dataclass thuần, không I/O          ← B
  contracts.py     hằng số từ §1                        ← B
  validators.py    KHÔNG DELEGATE                       ← B
  events.py        append-only JSONL                    ← A
  anki.py          AnkiConnect client                   ← A
  forge.py         JOB 1                                ← A (trừ chỗ gọi validators)
  context.py       JOB 2: Ctx bank + TTS 3 voice        ← A
  reconcile.py     JOB 3: state + suspend + strip media ← B (logic) / A (I/O)
  corpus.py        scan_corpus                          ← A
  judges/
    base.py        Protocol SpeechJudge / TextJudge     ← B
    azure.py       STT → scripted assessment            ← A
    llm.py         prompt = B, plumbing = A
  session.py       weekly runner + YouGlish URL         ← A
  reports/
    curator_eval.py  channel_report.py  speech_replay.py ← A

```

---

# §3 — Mười hai task, theo thứ tự

Mỗi task: **giao gì cho Codex** / **acceptance** / **dòng** **`.predict`** **bạn commit trước khi chạy**.

### Tuần 1

**T1 ·** **`contracts.py`** **+** **`models.py`** — Lane B, không delegate. Gõ tay §1 thành code. \~40 phút. Đây là 40 phút đắt nhất và cũng đáng nhất của cả dự án.

**T2 ·** **`events.py`** — Lane A.

> Implement append-only JSONL logger. API: `log(event, unit_key, payload)`, `read(event_type=None, since=None)`. Never rewrite existing lines. Create file if missing. Validate `event` against the enum in contracts.py. Do not modify contracts.py. Acceptance: append 3 event → đọc lại đủ 3, thứ tự giữ nguyên, file không bị truncate khi crash giữa chừng. `.predict`: *"tôi đoán nó sẽ dùng* *`json.dumps`* *không set* *`ensure_ascii=False`* *→ tiếng Việt trong payload thành escape"*

**T3 · Note type + card template** — Lane C rồi B. Đọc Anki manual về note type và conditional card generation trước (\~60 phút, Lane C). Rồi tự tạo trong Anki GUI. **Đừng để Codex tạo note type qua API** — bạn cần thấy tận mắt thẻ nào được sinh, thẻ nào không. Acceptance: tạo 1 note với `Target_R` có giá trị và `Target_S` rỗng → Anki sinh đúng 1 thẻ.

**T4 ·** **`anki.py`** — Lane A.

> AnkiConnect client. Methods: `add_notes`, `find_notes`, `notes_info`, `update_note_fields`, `suspend`, `unsuspend`, `get_revlog`. Raise on connection failure, never silently return empty. No retry logic. Acceptance: thêm 1 note test, tìm lại được, xoá được.

**T5 ·** **`validators.py`** — Lane B, **không delegate**. Viết từ schema của bạn. Mọi validator là hàm thuần `(dict) -> list[Violation]`. Không có nhánh nào "sửa hộ".

**T6 ·** **`forge.py`** — Lane A, nhưng ghim rõ.

> Pipeline: read candidates → call LLM with strict JSON schema → parse → run `validators.validate(unit)` → on any violation, write to `rejected.jsonl` and skip → dedup by `unit_key` against Anki → print preview table → on confirm, `anki.add_notes` + log FORGE event. Do not modify validators.py. Do not add auto-correction of rejected units.

**T7 · Leech config** — 5 phút, GUI. Deck options → leech threshold = 4, action = **Tag Only**. Không phải Suspend.

### Tuần 2

**T8 ·** **`context.py`** — Lane A.

> For each unit lacking `Ctx_1..5`: generate 5 novel contexts via LLM, validate against validators, write to note fields. Then Azure TTS: synthesize `audio_1..3` using 3 distinct voice IDs. All generation is batch and offline — no API call may occur at review time.

**T9 ·** **`reconcile.py`** — logic Lane B, I/O Lane A. Bạn viết bảng chuyển trạng thái. Codex viết phần đọc revlog và gọi suspend. Acceptance: một unit giả lập ở `MASTERED` + 31 ngày → cards suspended, `audio_*` và `VisualCue` rỗng, note còn nguyên, revlog còn nguyên. `.predict`: *"tôi đoán nó sẽ dùng* *`deleteNotes`* *thay vì* *`suspend`* *ở đâu đó"* — đây là lỗi nguy hiểm nhất trong toàn bộ dự án, nó phá dữ liệu FSRS không phục hồi được. Test kỹ.

**T10 ·** **`corpus.py`** — Lane A.

> Scan `corpus/YYYY-MM/*.{txt,md}`. For each `unit_key` in the registry, count occurrences. Word units: lemma-aware regex. Chunk units: allow ≤2 intervening tokens (`pose a serious threat to` matches `pose a threat to`). Emit one ENCOUNTER event per unit per month with count and source.

### Tuần 3–4

**T11 ·** **`judges/`** — Protocol và prompt là Lane B, plumbing Lane A. Azure: **STT trước → scripted assessment**, không ném thẳng audio vào unscripted. Prompt của LLM judge phải chứa rule: *phạt dùng sai Unit nâng cao nặng hơn không dùng*. Output schema **không có trường band score**. Rồi chạy invariant probe n=10 (5 cặp có chủ đích sai/đúng). Chỉ số nào không di chuyển đúng hướng → xoá khỏi pipeline.

**T12 ·** **`session.py`** **+** **`reports/`** — Lane A. Session runner: sample 10–12 unit từ pool productive, prompt free production, mở URL YouGlish cho T2, gõ `p`/`f`, ghi log. Ba report phải **in ngưỡng còn thiếu**, không im lặng:

```
curator_eval: INSUFFICIENT — n=42/150, positive 11/30
channel_report: INSUFFICIENT — n=18/50
speech_replay: READY — 1 impl, cần ≥2

```

---

# §4 — Cách prompt Codex, và bốn thứ nó sẽ tự ý làm

Mẫu prompt cho mọi task Lane A:

```
CONTEXT (read-only): contracts.py, models.py, validators.py
TASK: implement <module> in vocab/<file>.py
CONSTRAINTS:
  - do not modify any file in CONTEXT
  - fail closed: raise on ambiguous input, never guess or auto-fill
  - no network except <exact endpoint>
ACCEPTANCE: make tests/test_<module>.py pass.
  Do NOT modify the test file. If a test seems wrong, stop and say so.

```

Dòng cuối là quan trọng nhất. Chế độ hỏng phổ biến nhất của coding agent là **sửa test cho vừa code**.

Bốn thứ Codex gần như chắc chắn sẽ đề xuất, và bạn phải từ chối:

1. **Thêm SQLite/Postgres** cho registry → Từ chối. Anki *là* database. Hai nguồn sự thật là chỗ dự án này chết.
2. **Gọi LLM lúc review** để sinh context "tươi" → Từ chối. Phá tính offline, và đưa nhiễu phi tất định vào một phép đo trí nhớ.
3. **Nới validator khi nó reject nhiều** → Từ chối dứt khoát. Reject rate cao là *tín hiệu*, không phải bug. Nếu 40% Unit bị reject thì prompt FORGE sai, không phải validator sai.
4. **Bọc AnkiConnect trong một abstraction layer** "phòng khi đổi backend" → Từ chối. Bạn sẽ không đổi khỏi Anki. Layer đó chỉ thêm chỗ để lỗi trú.

---

# §5 — Điểm dừng

Sau T12, mọi thứ trong toàn bộ cuộc phân tích đều đã tồn tại dưới dạng code hoặc config. Không còn hạng mục nào chờ quyết định — chỉ còn dữ liệu chưa vượt ngưỡng, và đó là thời gian trôi chứ không phải việc bạn trì hoãn.

Quy tắc dừng, viết vào README:

> **Không component mới cho tới khi ba report chuyển sang** **`READY`** **hoặc đã chạy 30 ngày với dữ liệu thật.**

Và một kiểm tra thành thật, chạy cuối tuần 4: mở `events.jsonl`, đếm số `REVIEW` event. Nếu con số đó nhỏ hơn số dòng code bạn đã viết, thì bạn đang xây công cụ chứ không đang học từ vựng — và đó là chế độ hỏng nguy hiểm nhất của toàn bộ dự án này, nguy hiểm hơn mọi lỗi kỹ thuật ở trên, vì nó tự thưởng cho chính nó.

Gửi tôi `contracts.py` khi bạn viết xong T1. Đó là chỗ tôi review có giá trị nhất, vì mọi thứ sau đó đều được sinh ra từ nó.