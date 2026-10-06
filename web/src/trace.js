// Biến ChatResult (pipeline.py) thành các bước theo sơ đồ 1a → 1b → 2 → 3 → 4c → 4a.
// status: ok | warn | block | skip

const sec = (s) => (s == null ? '' : `${s < 1 ? Math.round(s * 1000) + ' ms' : s.toFixed(1) + ' s'}`)

export function buildTrace(r) {
  const t = r.timings || {}
  const steps = []

  steps.push({
    id: '1a',
    name: 'Kiểm tra cơ bản',
    status: r.blocked_by === 'input-basic' ? 'block' : 'ok',
    detail: r.blocked_by === 'input-basic' ? r.answer : 'Không rỗng, không quá dài, không có mẫu injection',
  })

  if (!r.guard) {
    steps.push({ id: '1b', name: 'Qwen3Guard', status: 'skip', detail: 'Không chạy vì đã bị chặn ở 1a' })
  } else {
    const cats = r.guard.categories?.length ? ` · ${r.guard.categories.join(', ')}` : ''
    steps.push({
      id: '1b',
      name: 'Qwen3Guard',
      status: { pass: 'ok', strict: 'warn', block: 'block' }[r.input_policy] || 'warn',
      detail: `${r.guard.label}${cats} → ${r.input_policy}${r.input_policy === 'strict' ? ' (chế độ nghiêm)' : ''}`,
      time: sec(t.guard_s),
    })
  }

  if (!r.retrieval) {
    steps.push({ id: '2', name: 'Retrieval guard', status: 'skip', detail: 'Không chạy' })
  } else {
    const top = r.retrieval_top ?? r.sources?.[0]?.rerank_score
    const cos = r.retrieval_top_cos   // câu trả lời cũ trong lịch sử chưa có trường này
    // Câu nối tiếp ("nói rõ hơn ý 2") được viết lại thành câu đầy đủ trước khi tìm
    const rewritten = r.search_question ? ` · tìm theo: “${r.search_question}”` : ''
    steps.push({
      id: '2',
      name: 'Retrieval guard',
      status: { pass: 'ok', partial: 'warn', refuse: 'block' }[r.retrieval],
      detail: `${r.retrieval}${top != null ? ` · P(yes) cao nhất ${top.toFixed(3)}` : ''}`
        + `${cos != null ? ` · cosine ${cos.toFixed(3)}` : ''}${rewritten}`,
      time: sec((t.rewrite_s || 0) + (t.embed_qdrant_s || 0) + (t.rerank_s || 0)),
    })
  }

  const generated = t.generate_s != null
  steps.push({
    id: '3',
    name: 'Gemini trả lời',
    status: generated ? 'ok' : 'skip',
    detail: generated ? 'Chỉ dùng tài liệu trong <context>' : 'Không gọi Gemini',
    time: sec(t.generate_s),
  })

  if (r.citation_check == null) {
    steps.push({ id: '4c', name: 'Kiểm tra trích dẫn', status: 'skip', detail: 'Không chạy' })
  } else {
    const ok = r.citation_check === 'ok' || r.citation_check === 'câu từ chối'
    steps.push({ id: '4c', name: 'Kiểm tra trích dẫn', status: ok ? 'ok' : 'warn', detail: r.citation_check })
  }

  if (!r.judged) {
    steps.push({
      id: '4a',
      name: 'Gemini giám khảo',
      status: 'skip',
      detail: generated ? 'Không cần (rủi ro thấp)' : 'Không chạy',
    })
  } else {
    const grounded = r.judge?.grounded
    steps.push({
      id: '4a',
      name: 'Gemini giám khảo',
      status: grounded ? 'ok' : 'block',
      detail: grounded
        ? 'Mọi khẳng định đều có nguồn'
        : `Không bám nguồn: ${(r.judge?.unsupported_claims || []).join('; ') || 'có khẳng định thiếu nguồn'}`,
      time: sec(t.judge_s),
    })
  }

  return steps
}

const REFUSAL_TEXT = 'Giáo trình không đề cập nội dung này.' // trùng guards.REFUSAL_TEXT

// Câu hỏi giáo trình không có → không hiện nguồn (chunk tìm được đều lạc đề).
// Backend đã trả sources rỗng; kiểm tra lại ở đây cho các tin nhắn cũ trong lịch sử.
export function visibleSources(r) {
  const answer = r.answer || ''
  const refused = r.blocked_by === 'retrieval' || (answer.includes(REFUSAL_TEXT) && !answer.includes('[Chương'))
  return refused ? [] : r.sources || []
}

export const BLOCK_LABEL = {
  'input-basic': 'Bị chặn ở lớp 1a',
  'input-guard': 'Bị chặn bởi Qwen3Guard',
  retrieval: 'Giáo trình không có tài liệu phù hợp',
  output: 'Câu trả lời không bám nguồn — đã thay bằng câu an toàn',
}

export const formatSeconds = sec
