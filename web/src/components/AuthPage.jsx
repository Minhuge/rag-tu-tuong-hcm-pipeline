import {
  BookOpenText, ClockCounterClockwise, EnvelopeSimple, Eye, EyeSlash, LockSimple, ShieldCheck, SpinnerGap, Star,
  User, WarningCircle,
} from '@phosphor-icons/react'
import { useEffect, useId, useRef, useState } from 'react'
import { login, register } from '../api.js'

const MIN_PASSWORD = 8 // khớp auth.MIN_PASSWORD_CHARS
const MAX_PASSWORD = 128
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const FEATURES = [
  { icon: BookOpenText, text: 'Trả lời chỉ từ giáo trình, có trích dẫn chương và trang' },
  { icon: ShieldCheck, text: '6 lớp kiểm tra trước khi câu trả lời đến tay bạn' },
  { icon: ClockCounterClockwise, text: 'Lịch sử ôn tập gắn với tài khoản, mở được trên mọi thiết bị' },
]

const COPY = {
  login: {
    title: 'Đăng nhập',
    lead: 'Chào mừng trở lại. Tiếp tục ôn tập với giáo trình của bạn.',
    submit: 'Đăng nhập',
    busy: 'Đang đăng nhập…',
    switchText: 'Chưa có tài khoản?',
    switchAction: 'Tạo tài khoản',
  },
  register: {
    title: 'Tạo tài khoản',
    lead: 'Chỉ cần email và mật khẩu. Lịch sử trò chuyện sẽ được lưu theo tài khoản này.',
    submit: 'Tạo tài khoản',
    busy: 'Đang tạo tài khoản…',
    switchText: 'Đã có tài khoản?',
    switchAction: 'Đăng nhập',
  },
}

function validate(mode, { email, password }) {
  const errors = {}
  if (!email.trim()) errors.email = 'Nhập email của bạn.'
  else if (!EMAIL_RE.test(email.trim())) errors.email = 'Email chưa đúng dạng, ví dụ ban@example.com.'
  if (!password) errors.password = 'Nhập mật khẩu.'
  else if (mode === 'register' && password.length < MIN_PASSWORD)
    errors.password = `Mật khẩu cần ít nhất ${MIN_PASSWORD} ký tự.`
  else if (password.length > MAX_PASSWORD) errors.password = `Mật khẩu tối đa ${MAX_PASSWORD} ký tự.`
  return errors
}

function Field({ id, label, icon: Icon, error, hint, optional, children }) {
  const describedBy = [error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(' ') || undefined
  return (
    <div className={`field ${error ? 'invalid' : ''}`}>
      <label htmlFor={id}>
        {label}
        {optional && <span className="field-optional"> (không bắt buộc)</span>}
      </label>
      <div className="input-wrap">
        <Icon size={18} className="input-icon" aria-hidden="true" />
        {children({ 'aria-invalid': !!error, 'aria-describedby': describedBy })}
      </div>
      {error ? (
        <p id={`${id}-error`} className="field-error">
          <WarningCircle size={15} weight="fill" aria-hidden="true" />
          {error}
        </p>
      ) : (
        hint && <p id={`${id}-hint`} className="field-hint">{hint}</p>
      )}
    </div>
  )
}

/**
 * Màn hình đăng nhập / đăng ký. notice: thông báo từ App (vd. phiên hết hạn, mất kết nối lúc mở trang).
 */
export default function AuthPage({ onAuthed, notice }) {
  const [mode, setMode] = useState('login')
  const [values, setValues] = useState({ email: '', name: '', password: '' })
  const [touched, setTouched] = useState({})
  const [submitted, setSubmitted] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [serverError, setServerError] = useState(null)
  const ids = useId()
  const emailRef = useRef(null)
  const passwordRef = useRef(null)
  const headingRef = useRef(null)
  const copy = COPY[mode]

  const errors = validate(mode, values)
  // Báo lỗi khi rời ô (blur) hoặc sau lần bấm gửi đầu tiên — không báo khi người dùng mới gõ được vài chữ
  const shown = (k) => (touched[k] || submitted ? errors[k] : null)

  useEffect(() => {
    emailRef.current?.focus()
  }, [])

  const set = (k) => (e) => {
    setValues((v) => ({ ...v, [k]: e.target.value }))
    setServerError(null)
  }
  const blur = (k) => () => setTouched((t) => ({ ...t, [k]: true }))

  const switchMode = () => {
    setMode((m) => (m === 'login' ? 'register' : 'login'))
    setTouched({})
    setSubmitted(false)
    setServerError(null)
    headingRef.current?.focus()
  }

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    setSubmitted(true)
    if (errors.email || errors.password) {
      const firstInvalid = errors.email ? emailRef : passwordRef // đưa con trỏ tới ô sai đầu tiên
      firstInvalid.current?.focus()
      return
    }
    setBusy(true)
    setServerError(null)
    try {
      const user = mode === 'login'
        ? await login(values.email.trim(), values.password)
        : await register(values.email.trim(), values.password, values.name.trim())
      onAuthed(user)
    } catch (err) {
      setServerError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className="auth">
      <aside className="auth-brand" aria-hidden="true">
        <div className="auth-brand-inner">
          <span className="logo logo-lg"><Star size={26} weight="fill" /></span>
          <p className="auth-brand-title">Trợ lý Tư tưởng<br />Hồ Chí Minh</p>
          <ul className="auth-features">
            {FEATURES.map(({ icon: Icon, text }) => (
              <li key={text}>
                <span className="auth-feature-icon"><Icon size={18} /></span>
                {text}
              </li>
            ))}
          </ul>
          <blockquote className="auth-quote">
            “Không có gì quý hơn độc lập, tự do.”
            <cite>Hồ Chí Minh</cite>
          </blockquote>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-card">
          <div className="auth-mobile-brand">
            <span className="logo" aria-hidden="true"><Star size={20} weight="fill" /></span>
            <span>Trợ lý Tư tưởng Hồ Chí Minh</span>
          </div>

          <h1 ref={headingRef} tabIndex={-1}>{copy.title}</h1>
          <p className="auth-lead">{copy.lead}</p>

          {notice && !serverError && (
            <p className="auth-alert auth-alert-info" role="status">
              <WarningCircle size={18} weight="fill" aria-hidden="true" />
              {notice}
            </p>
          )}
          {serverError && (
            <p className="auth-alert" role="alert">
              <WarningCircle size={18} weight="fill" aria-hidden="true" />
              {serverError}
            </p>
          )}

          <form onSubmit={submit} noValidate>
            <Field id={`${ids}-email`} label="Email" icon={EnvelopeSimple} error={shown('email')}>
              {(aria) => (
                <input ref={emailRef} id={`${ids}-email`} type="email" name="email" autoComplete="email"
                  inputMode="email" autoCapitalize="none" spellCheck={false} placeholder="ban@example.com"
                  value={values.email} onChange={set('email')} onBlur={blur('email')} disabled={busy} {...aria} />
              )}
            </Field>

            {mode === 'register' && (
              <Field id={`${ids}-name`} label="Tên hiển thị" icon={User} optional>
                {(aria) => (
                  <input id={`${ids}-name`} type="text" name="name" autoComplete="nickname" maxLength={100}
                    placeholder="Ví dụ: Minh Hiếu" value={values.name} onChange={set('name')} disabled={busy}
                    {...aria} />
                )}
              </Field>
            )}

            <Field id={`${ids}-password`} label="Mật khẩu" icon={LockSimple} error={shown('password')}
              hint={mode === 'register' ? `Ít nhất ${MIN_PASSWORD} ký tự.` : null}>
              {(aria) => (
                <>
                  <input ref={passwordRef} id={`${ids}-password`} type={showPassword ? 'text' : 'password'}
                    name="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                    value={values.password} onChange={set('password')} onBlur={blur('password')} disabled={busy}
                    {...aria} />
                  <button type="button" className="reveal" onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'} aria-pressed={showPassword}
                    aria-controls={`${ids}-password`}>
                    {showPassword ? <EyeSlash size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
                  </button>
                </>
              )}
            </Field>

            <button type="submit" className="btn-primary" disabled={busy} aria-busy={busy}>
              {busy && <SpinnerGap size={18} className="spin" aria-hidden="true" />}
              {busy ? copy.busy : copy.submit}
            </button>
          </form>

          <p className="auth-switch">
            {copy.switchText}{' '}
            <button type="button" className="link-btn" onClick={switchMode} disabled={busy}>
              {copy.switchAction}
            </button>
          </p>
        </div>

        <p className="auth-foot">Mật khẩu được băm bằng Argon2 trước khi lưu — máy chủ không giữ mật khẩu gốc.</p>
      </main>
    </div>
  )
}
