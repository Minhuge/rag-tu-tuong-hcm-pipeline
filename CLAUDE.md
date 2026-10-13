# Hướng dẫn cho Claude Code

## Giao diện web (`web/`)

- **Trước khi thiết kế, viết hay sửa bất kỳ giao diện nào** trong `web/src/` (trang, component, CSS), gọi skill
  `ui-ux-pro-max` (`.claude/skills/ui-ux-pro-max/`) và làm theo nó, kể cả với bước nhỏ hay chỉ sửa CSS. Trước khi
  báo xong, rà theo "Pre-Delivery Checklist" của skill.
- Giữ bộ design token sẵn có trong `web/src/index.css` (nền giấy ấm, đỏ trầm `--accent`, vàng `--gold`,
  Be Vietnam Pro + Noto Serif, `--radius`, chế độ tối) — không đổi sang bảng màu / font skill gợi ý nếu chưa hỏi.
- Kiểu dữ liệu API sinh từ backend: sửa schema API thì chạy `cd web && npm run api:types`
  (`tests/test_api_schemas.py` báo nếu quên). Kiểm tra: `npx oxlint src`, `npm run build`, `npm run typecheck`.
