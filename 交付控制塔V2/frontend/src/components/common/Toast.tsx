import { useUI } from '../../store/uiStore'

/** Toast 通知堆栈（等价 dist De(message)） */
export function ToastStack() {
  const toasts = useUI((s) => s.toasts)
  const dismiss = useUI((s) => s.dismissToast)

  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className={`toast tone-${t.tone}`} onClick={() => dismiss(t.id)}>
          {t.text}
        </div>
      ))}
    </div>
  )
}
