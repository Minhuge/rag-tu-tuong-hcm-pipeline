import { ChatCircleText, Exam } from '@phosphor-icons/react'

/**
 * Chế độ cuộc trò chuyện (conversations.mode, roadmap 4.9). ready = dùng được chưa (false → ô nhập khoá, nhãn "Sắp có").
 * sr: chữ cho trình đọc màn hình trước tiêu đề trong lịch sử — chế độ mặc định thì không đọc cho đỡ rườm.
 */
export const MODES = {
  docs: {
    Icon: ChatCircleText, label: 'Hỏi giáo trình', sr: '', ready: true,
    text: 'Hỏi đáp về nội dung giáo trình, có trích dẫn chương và trang.',
  },
  exam: {
    Icon: Exam, label: 'Trợ lý bài kiểm tra', sr: 'Trợ lý bài kiểm tra: ', ready: true,
    text: 'Hỏi về bài bạn đã làm (điểm, câu sai, phần cần ôn) hoặc nhờ tạo đề nháp theo chương.',
  },
}

export const modeOf = (m) => MODES[m] ?? MODES.docs
