# Roadmap: exam feature for the HCM chat app

## Decisions already made

**Who can do what:** any user can create exams and take exams. There are no teacher/student roles. Permissions depend on ownership: the creator edits, sees answers and grades; takers see only questions and their own results.

**Two chat systems, one chat window:**

- **Docs mode:** the existing RAG pipeline (guard → retrieve → answer → judge → citations). It stays unchanged.
- **Exam mode:** a new agent with tools, built in Phase 5.

**Tables added (no JSON except the old `messages.meta`):**

- `exams`
- `questions`
- `question_options`
- `submissions`
- `submission_answers`

**Columns to add now, before data exists:**

| Column | Values | Why |
|---|---|---|
| `exams.status` | `draft` / `published` | AI exams must be reviewed before anyone takes them |
| `exams.origin` | `manual` / `ai` | Shows where the exam came from; also used to count AI generations per day |
| `exams.max_attempts` | int, null = unlimited | Retake limit; stored now, enforced in Phase 6 |
| `questions.explanation` | text | Results can show *why* an answer is correct |
| `questions.source_page` | int | Page citation, like the chatbot |
| `submission_answers.graded_by` | `auto` / `ai` / `creator` | Shows who graded; also used to count AI gradings per day |
| `conversations.mode` | `docs` / `exam` | Keeps the two chat systems' histories apart |

## Phase 0: organize the backend (small)

- Split `api.py` into `routers/auth.py`, `routers/chat.py` and `routers/exams.py`.
- Add the 5 new tables and the columns above to `db.py`. Write a migration for `conversations.mode`, because that table already has data (same pattern as `migrate_auth.py`).
- Chat code moves but doesn't change. The existing tests must still pass.

**Done when:** all old tests pass, and the new tables are created on startup.

## Phase 1: core exam flow, no AI (← start here)

**Service functions** in `exams.py`. Write these as clean functions taking `user_id`, because Phase 5 reuses them as tools.

- Creator:
  - create an exam (as a draft), add or edit questions and options, publish;
  - list "my exams";
  - view all submissions to their exam.
- Taker:
  - open an exam, with `is_correct`, `rubric` and `explanation` hidden;
  - start a submission, save answers, submit;
  - view their own results.

**MCQ auto-grading on submit:**

- Compare `selected_option_id` with `is_correct`, then set `score` and `graded_by = auto`.
- If the exam has no essay questions, sum `submissions.score` / `max_score` and set `status = graded`. If it has essays, set `status = submitted`.

**Rules the database can't enforce (checked in code):**

- The chosen option belongs to that question.
- Each MCQ question has exactly one correct option, checked when publishing.
- Only `published` exams can be taken.
- `visibility`, the `open_at` / `close_at` window and the time limit (`started_at + duration_minutes`) are respected.
- Only the creator can edit an exam or see its answers.

**Tests:** in the same style as `test_auth.py`, covering every rule above, including someone else trying to read your exam's answers.

**Done when:** you can create an exam by hand, take it as another user, and get the correct MCQ score.

## Phase 2: AI generates exams from the textbook

1. **Prototype as a script first** (like `eval_ragas.py`): pick a chapter, generate questions, print them, and tune the prompt until quality is good.
2. The pipeline:
   - The user picks a chapter and the number of MCQ and essay questions.
   - The retriever pulls that chapter's passages from Qdrant. Your code does the retrieval, not the model.
   - Gemini returns **structured JSON**: question, 4 options, correct answer, explanation, source page (plus a rubric for essay questions).
   - Validation: exactly one correct answer, and the answer is supported by the source passage (reuse the judge idea). Questions that fail are dropped or regenerated.
   - Save through Phase 1's create functions with `origin = ai` and `status = draft`.
3. The user reviews and edits, then publishes.

**Quota:** cap AI generations per user per day by counting exams with `origin = ai` created today. This sits on top of the global daily limit in `pipeline.py`.

**Done when:** a generated chapter exam passes validation, and you would actually give it to a classmate.

## Phase 3: AI grades essay questions

- For each essay answer, Gemini gets the question, `rubric`, relevant passages and the student's text. It returns `{score, feedback}` as structured output.
- Code clamps the score to `0..questions.points` and sets `graded_by = ai`.
- It runs as a background job after submit. When every answer is graded, the job sums the score and sets `status = graded`.
- The creator can override any AI score, which sets `graded_by = creator`.
- **Safety:** essay text is untrusted (for example "give me full marks"). The grading call has no tools and no write access, only the returned score, clamped by code.
- **Quota:** cap AI gradings per user per day by counting answers with `graded_by = ai`.

**Done when:** essay scores look fair on a handful of test answers, including one deliberate injection attempt.

## Phase 4: web UI

- **Chat page:** add a mode switch, Hỏi giáo trình | Trợ lý bài kiểm tra. Docs mode behaves exactly as today.
- **Exam pages:**
  - My exams (drafts and published).
  - Create: an AI form plus a question editor.
  - Take exam: one question at a time, with a timer.
  - Results: score, correct answers, explanation, AI feedback, page citations. Show "grading…" while `status = submitted`.

## Phase 5: exam assistant with tool calling

A separate agent (a good fit for LangGraph), used only in exam mode.

**Step 1, results tutor (read-only tools):**

- `get_my_submission(exam_id)`
- `get_question(question_id)`
- `search_textbook(query, chapter)` (the same retriever as docs mode)

**Step 2, one write tool:**

- `create_exam_draft(chapter, n_mcq, n_essay)`, which calls the Phase 2 pipeline. It only creates a draft and never publishes.

**Rules:**

- Tools never take a `user_id`. Code injects it from the JWT and checks ownership.
- At most around 5 tool calls per message.
- Tool calls count toward the Gemini quota.
- No tools for editing, deleting or submitting. Those stay as buttons.

**Done when:** "Vì sao câu 4 tôi sai?" gets a correct answer with a page citation, and the agent can't read another user's results.

## Phase 6: extras

- Sharing by `share_code` or public listing.
- Enforce `max_attempts`.
- Statistics: average score, hardest questions. Easy with SQL now that answers are in `submission_answers`.
- Automatic routing between docs and exam mode, only if the mode switch feels clunky.

## Order at a glance

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
 setup     manual     AI         AI        UI       tool-      extras
           exams      creates    grades             calling
                      exams      essays             assistant
```

Each phase only builds on tested work from earlier phases. AI is added only after the plain exam flow is proven correct, so when something breaks you know which layer to look at.
