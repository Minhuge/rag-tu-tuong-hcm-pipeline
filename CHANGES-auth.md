# Thay đổi: đăng nhập bằng JWT

Tài liệu này liệt kê các thay đổi làm ngày 07/10/2026, tiếp nối [CHANGES-guardrail-postgres-ragas.md](CHANGES-guardrail-postgres-ragas.md). Tất cả **chưa được commit**, nằm trên nhánh `feature/postgres-ragas-guard` tính từ commit `c5febfc`.

## Tóm tắt

| | Việc | Trạng thái |
|---|---|---|
| ✅ | Bảng `users`, mật khẩu băm bằng Argon2 | Xong, đã có trong `hcm_chat` |
| ✅ | `auth.py`: access token (JWT), refresh token, `get_current_user` | Xong, có test |
| ✅ | Các route `/auth/*` và bảo vệ các endpoint cũ trong `api.py` | Xong, có test |
| ✅ | Cuộc trò chuyện thuộc về `user_id` thay vì mã trình duyệt `X-Client-Id` | Xong, có test |
| ✅ | `create_user.py`, `migrate_auth.py` | Xong, đã chạy thử trên bản sao của `hcm_chat` |
| ✅ | `.env`: `JWT_SECRET`, `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS` | Đã thêm |
| ✅ | Chạy migration trên database thật `hcm_chat` | Bạn đã chạy: 5 cuộc trò chuyện cũ thuộc về `admin@gmail.com` |
| ✅ | Màn hình đăng nhập / đăng ký trên web (React), nút đăng xuất | Xong, build qua. **Chưa chạy thử trên trình duyệt** (bạn tự thử) |
| ❌ | Commit và push | **Chưa** |

---

## Đã làm

### 1. Cách đăng nhập hoạt động

```
Đăng nhập (email + mật khẩu)
   │
   ├─▶ access token  (JWT, sống 15 phút)  → cookie httpOnly "access_token", Path=/
   │                                         trình duyệt tự gửi kèm mọi request
   │
   └─▶ refresh token (chuỗi ngẫu nhiên, sống 30 ngày) → cookie httpOnly "refresh_token", chỉ gửi tới /auth
                                                         database chỉ lưu SHA-256 của nó

Access token hết hạn → POST /auth/refresh → token cũ bị thu hồi, nhận cặp cookie mới
```

- **Access token** ký HS256 bằng `JWT_SECRET`. Máy chủ không lưu, chỉ kiểm tra chữ ký và hạn, rồi đọc user để chắc tài khoản còn hoạt động. Tài khoản bị khoá thì token còn hạn cũng không dùng được.
- **Cả hai token nằm trong cookie** `HttpOnly`, `SameSite=Strict`. JavaScript không đọc được, nên lỗi XSS không lấy được token. Web không giữ token nào, F5 vẫn còn đăng nhập.
- **Chống CSRF:** cookie được trình duyệt tự gửi, nên có hai lớp chặn:
  - `SameSite=Strict`: request xuất phát từ trang web khác không mang cookie.
  - POST/DELETE đăng nhập bằng cookie phải có header `X-Requested-With`, thiếu thì 403. Trang khác không tự thêm header lạ được, vì CORS chỉ cho phép web của mình.
- **Header Bearer vẫn dùng được** cho `/docs`, curl và app khác. Header không bị trình duyệt tự gửi nên không cần header chống CSRF.
- `/auth/login` và `/auth/register` trả `access_token` trong JSON (cho `/docs`, curl), vì muốn gọi chúng phải biết mật khẩu. `/auth/refresh` **không** trả, nên mã độc chèn vào trang gọi `/auth/refresh` cũng không đọc được token.
- **Mỗi lần làm mới là đổi token mới (rotation).** Nếu một token đã bị thay mà vẫn bị dùng lại, có thể nó đã bị lộ, nên mọi phiên của người đó bị thu hồi và phải đăng nhập lại.
- **Ân hạn 30 giây** (`REUSE_GRACE_SECONDS`): token vừa bị thay trong vòng 30 giây vẫn được làm mới. Lý do: hai tab cùng làm mới đúng một lúc, hoặc mạng rớt mất phản hồi, thì không bị coi là token bị lộ và đăng xuất oan.
- Đăng xuất và thu hồi toàn bộ thì **xoá hẳn** dòng token, nên không có thời gian ân hạn.
- **Không dò được email nào đã đăng ký:** sai email và sai mật khẩu trả về cùng một thông báo. Email không tồn tại vẫn được kiểm tra với một hash giả để thời gian phản hồi như nhau.
- Mật khẩu từ 8 đến 128 ký tự. Email được lưu chữ thường, nên `A@x.com` và `a@x.com` là một.

### 2. Endpoint

| Endpoint | Cần đăng nhập | Ghi chú |
|---|---|---|
| `POST /auth/register` | không | Tạo tài khoản rồi đăng nhập luôn. Tắt bằng `ALLOW_REGISTRATION=false` → 403 |
| `POST /auth/login` | không | Sai → 401 "Email hoặc mật khẩu không đúng" |
| `POST /auth/refresh` | cookie + `X-Requested-With` | Đặt cặp cookie mới. Hỏng hoặc hết hạn → 401 và xoá cả hai cookie |
| `POST /auth/logout` | cookie + `X-Requested-With` | Thu hồi refresh token, xoá cả hai cookie. Access token đã hết hạn vẫn đăng xuất được |
| `GET /auth/me` | có | Thông tin người đang đăng nhập |
| `/conversations`, `/chat`, `/ask`, `/search` | **có** | Cookie `access_token` hoặc header Bearer. POST/DELETE bằng cookie cần `X-Requested-With`. Trước đây chỉ cần `X-Client-Id` |
| `/health`, `/docs` | không | |

Thử trong `/docs`: gọi `/auth/login`, chép `access_token`, bấm **Authorize** rồi dán vào.

### 3. Database

```
users                  conversations           refresh_tokens
─────                  ─────────────           ──────────────
id (PK) ◀──────┬────── user_id (FK)            id (PK)
email (unique) │       id, title, ...          token_hash (SHA-256)
password_hash  │                               created_at, expires_at, revoked_at
display_name   └────────────────────────────── user_id (FK)
is_active
is_admin (mới)
created_at, last_login_at
```

Xoá một user thì cuộc trò chuyện, tin nhắn và refresh token của người đó bị xoá theo (`ON DELETE CASCADE`). Cột `conversations.client_id` cũ được giữ lại nhưng không còn dùng.

### 4. File

| File | Thay đổi |
|---|---|
| `auth.py` (mới) | Băm và kiểm tra mật khẩu, tạo và giải mã JWT, refresh token, cookie, `get_current_user` |
| `api.py` | Thêm các route `/auth/*`. Các endpoint cũ dùng `Depends(auth.get_current_user)` thay cho `X-Client-Id`. Thiếu `JWT_SECRET` thì API không khởi động. CORS cho phép header `Authorization` và cookie |
| `db.py` | Thêm `RefreshToken`, `users.is_admin`, `conversations.user_id`. `init_db()` báo "chạy migrate_auth.py" nếu database chưa được nâng cấp. Code đăng nhập chuyển sang `auth.py` |
| `create_user.py` (mới) | Tạo tài khoản từ terminal, mật khẩu nhập ẩn hai lần |
| `migrate_auth.py` (mới) | Nâng cấp database cũ, gán cuộc trò chuyện cũ cho quản trị viên. Chạy lại không sao |
| `test_auth.py` (mới) | Đăng ký, đăng nhập, cookie, token giả mạo hoặc hết hạn, rotation, dùng lại token, đăng xuất, tài khoản bị khoá, chống CSRF |
| `test_db.py`, `test_api.py`, `conftest.py` | Chuyển từ `client_id` sang `user_id` và Bearer token. Test dùng `JWT_SECRET` giả, không đọc `.env` |
| `web/vite.config.js` | Proxy thêm `/auth` |
| `requirements.txt` | Thêm `pwdlib[argon2]`, `pyjwt` |
| `migrate_sqlite.py` | Thêm ghi chú: chỉ dùng được với schema trước khi có đăng nhập |
| `.env` | Thêm `JWT_SECRET` (sinh ngẫu nhiên), `ACCESS_TOKEN_MINUTES=15`, `REFRESH_TOKEN_DAYS=30` |

Biến tuỳ chọn trong `.env`: `ALLOW_REGISTRATION` (mặc định `true`), `COOKIE_SECURE` (mặc định `false`, đặt `true` khi chạy sau HTTPS).

### 5. Web (React)

| File | Thay đổi |
|---|---|
| `web/src/Root.jsx` (mới) | Cổng đăng nhập. Mở trang thì gọi `/auth/me` bằng cookie (access cookie hết hạn thì `/auth/refresh` rồi hỏi lại): còn phiên thì vào thẳng app, không thì hiện trang đăng nhập. Phiên hết hạn giữa chừng thì quay về đây với thông báo |
| `web/src/components/AuthPage.jsx` (mới) | Trang đăng nhập / đăng ký |
| `web/src/api.js` | Bỏ `X-Client-Id`. Không giữ token nào: mọi request gửi kèm cookie (`credentials: 'include'`) và header `X-Requested-With`. Gặp 401 thì làm mới một lần rồi gửi lại; nhiều request cùng gặp 401 chỉ gọi `/auth/refresh` một lần. Thêm `login`, `register`, `logout`, `restoreSession` |
| `web/src/App.jsx`, `Sidebar.jsx` | Cuối cột lịch sử hiện tài khoản (chữ cái đầu, tên, email) và nút đăng xuất. Đăng xuất thì dừng câu hỏi đang chờ và xoá luôn mục đang chờ hoàn tác |
| `web/src/history.js` | Nhớ cuộc trò chuyện đang mở riêng cho từng tài khoản |
| `web/src/main.jsx` | Render `Root` thay vì `App` |
| `web/src/index.css` | Style trang đăng nhập và khối tài khoản, cả sáng lẫn tối |

Thiết kế theo skill **ui-ux-pro-max** (cài bằng `npx uipro-cli init --ai claude`, nằm trong `.claude/`, đã có trong `.gitignore`):
- **Giao diện:** giữ màu, font và logo của web hiện có (quy tắc *consistency* của skill), không dùng bảng màu skill gợi ý.
  - Màn hình rộng: bên trái là bảng thương hiệu đỏ son có ngôi sao vàng, bên phải là thẻ đăng nhập.
  - Điện thoại: chỉ còn thẻ đăng nhập.
- **Ô nhập:**
  - mỗi ô có `<label>`;
  - `autocomplete` (`email`, `current-password`, `new-password`) để trình duyệt và trình quản lý mật khẩu tự điền;
  - `type="email"`;
  - không chặn dán.
- **Báo lỗi:**
  - báo khi rời ô hoặc sau lần gửi đầu, nằm ngay dưới ô, dùng `aria-invalid` và `aria-describedby`;
  - lỗi từ máy chủ dùng `role="alert"`;
  - bấm gửi khi có lỗi thì con trỏ nhảy về ô sai đầu tiên.
- **Tương tác:**
  - nút hiện/ẩn mật khẩu;
  - nút gửi có vòng xoay và bị khoá khi đang xử lý;
  - vùng bấm ≥ 40px;
  - chữ trong ô 16px để iOS không tự phóng to;
  - tôn trọng `prefers-reduced-motion`.

### 6. Đã kiểm tra

- `pytest -q`: **131 test qua** trên SQLite tạm (sau khi chuyển access token sang cookie).
- `npm run build` và `oxlint` qua.
- Thử API bằng curl trên server test (cổng 8010, database `hcm_chat_test`), đều đúng:
  - đăng ký → 201;
  - `/conversations` có token → 200, không token → 401;
  - refresh → 200;
  - đăng xuất → 204;
  - refresh sau khi đăng xuất → 401.
- `test_auth.py`, `test_db.py`, `test_api.py` trên Postgres thật (`hcm_chat_test`): **47 test qua**.
- `migrate_auth.py` trên một bản sao của `hcm_chat` (đã xoá sau khi thử):
  - lần 1 báo chưa có quản trị viên;
  - tạo admin;
  - lần 2 gán **5** cuộc trò chuyện cũ cho admin;
  - lần 3 không đổi gì;
  - `init_db()` chạy được với schema mới.

---

## Chưa làm

### Cần làm ngay để chạy được

1. **Nâng cấp database thật.** Chưa chạy, vì cần email và mật khẩu của bạn:
   ```bash
   python migrate_auth.py                          # bước 1, báo chưa có quản trị viên
   python create_user.py <email-của-bạn> --admin
   python migrate_auth.py                          # gán 5 cuộc trò chuyện cũ cho bạn
   ```
   Chưa chạy bước này thì `uvicorn api:app` dừng với lỗi "Bảng conversations chưa có cột user_id → chạy: python migrate_auth.py".

2. **Chạy thử web trên trình duyệt.** Bạn tự thử:
   - đăng ký, đăng nhập sai và đúng;
   - F5 vẫn còn đăng nhập;
   - đăng xuất;
   - giao diện điện thoại và chế độ tối;
   - để quá 15 phút rồi hỏi tiếp (token tự làm mới).

3. **Commit và push.** Mọi thay đổi ở trên, kể cả bảng `users` làm trước đó, chưa được commit. Nhánh `feature/postgres-ragas-guard` cũng chưa được push: lệnh push trong terminal của Claude không có quyền GitHub, cần push từ VS Code.

### Nên làm sau

| Việc | Vì sao |
|---|---|
| Giới hạn số lần đăng nhập sai (rate limit) | Hiện có thể thử mật khẩu liên tục. Argon2 chậm nên khó dò, nhưng vẫn nên chặn |
| Dùng `is_admin` | Cột đã có nhưng chưa có chức năng nào riêng cho quản trị viên. Ví dụ: chỉ admin được gọi `/search`, hoặc trang quản lý user |
| Đổi mật khẩu, quên mật khẩu, xác thực email | Chưa có. Quên mật khẩu cần gửi email |
| Thời gian ân hạn sau khi đăng xuất | Token bị thay chưa tới 30 giây trước lúc đăng xuất vẫn làm mới được trong phần ân hạn còn lại. Muốn chặn hẳn thì cần lưu chuỗi token (thêm cột `replaced_by` vào `refresh_tokens`) |
| Xoá cột `conversations.client_id` | Không còn dùng: `ALTER TABLE conversations DROP COLUMN client_id;` |
| Dùng Alembic cho migration | `migrate_auth.py` viết tay cho lần này. Nếu schema còn thay đổi nhiều thì Alembic dễ quản lý hơn |
| `COOKIE_SECURE=true` khi deploy HTTPS | Hiện tắt để chạy được qua `http://` trong mạng LAN |
| Cập nhật `README.md` | Chưa ghi các bước tạo tài khoản và migration |
