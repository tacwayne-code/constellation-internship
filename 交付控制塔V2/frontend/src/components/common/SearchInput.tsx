import { useState } from 'react'

interface SearchInputProps {
  value: string
  onChange: (val: string) => void
  placeholder?: string
}

/** 关键字搜索输入（带图标 + 一键清空） */
export function SearchInput({ value, onChange, placeholder = '搜索...' }: SearchInputProps) {
  const [focused, setFocused] = useState(false)
  return (
    <div className={`search-input ${focused ? 'focused' : ''}`}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
        <circle cx="11" cy="11" r="8" />
        <path d="m21 21-4.35-4.35" />
      </svg>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      />
      {value && (
        <button className="search-clear" onClick={() => onChange('')} type="button">
          ×
        </button>
      )}
    </div>
  )
}