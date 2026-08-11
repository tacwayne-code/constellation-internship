/**
 * 轻量 UI 状态：Toast
 */
import { create } from 'zustand'

export interface ToastMsg {
  id: number
  text: string
  tone: 'success' | 'warning' | 'danger' | 'info'
}

interface UIState {
  toasts: ToastMsg[]
  pushToast: (text: string, tone?: ToastMsg['tone']) => void
  dismissToast: (id: number) => void
}

let seq = 0

export const useUI = create<UIState>((set) => ({
  toasts: [],
  pushToast: (text, tone = 'info') => {
    const id = ++seq
    set((s) => ({ toasts: [...s.toasts, { id, text, tone }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 2600)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

/** 等价 dist 的 De(message) */
export const toast = (text: string, tone: ToastMsg['tone'] = 'info') => {
  useUI.getState().pushToast(text, tone)
}
