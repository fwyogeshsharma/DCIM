import { useState, useEffect, useRef } from 'react'

/**
 * Controlled numeric input that behaves for humans.
 *
 * The naive `value={n} onChange={parseInt(e.target.value) || fallback}` pattern
 * forces the box to a number the instant it's cleared — so it can never be empty
 * mid-edit, and typing after a clear prepends the forced value (e.g. clear 15000,
 * type 8 → "08000"). This keeps a local STRING so the box may be empty while the
 * user is typing, emits the parsed number to the parent as they go, and only
 * coerces an empty/invalid box to `fallback` on blur.
 */
interface NumberInputProps {
  value: number
  onChange: (n: number) => void
  fallback?: number                 // applied when the box is left empty/invalid on blur
  int?: boolean                     // parse with parseInt (ports, counts) vs parseFloat
  min?: number
  max?: number
  step?: number
  style?: React.CSSProperties
  className?: string
  placeholder?: string
  title?: string
  disabled?: boolean
  onFocus?: () => void
  onBlur?: () => void               // fired after the internal empty→fallback coerce
}

export default function NumberInput({
  value, onChange, fallback = 0, int = false,
  min, max, step, style, className, placeholder, title, disabled,
  onFocus, onBlur,
}: NumberInputProps) {
  const [str, setStr] = useState<string>(String(value))
  // Track the last numeric value WE emitted, so a parent re-render triggered by
  // our own onChange doesn't clobber the string the user is typing — but an
  // external change (refresh/reset) still syncs the box.
  const last = useRef<number>(value)
  useEffect(() => {
    if (value !== last.current) {
      setStr(String(value))
      last.current = value
    }
  }, [value])

  const handle = (raw: string) => {
    setStr(raw)
    if (raw === '' || raw === '-') return          // allow an empty/partial box mid-edit
    const n = int ? parseInt(raw, 10) : parseFloat(raw)
    if (!Number.isNaN(n)) {
      last.current = n
      onChange(n)
    }
  }
  const blur = () => {
    const n = int ? parseInt(str, 10) : parseFloat(str)
    if (str === '' || Number.isNaN(n)) {
      setStr(String(fallback))
      last.current = fallback
      onChange(fallback)
    } else if (String(n) !== str) {
      setStr(String(n))                            // normalise "08" -> "8" on blur
    }
    onBlur?.()
  }

  return (
    <input
      type="number"
      value={str}
      min={min} max={max} step={step}
      placeholder={placeholder} title={title} disabled={disabled}
      style={style} className={className}
      onChange={e => handle(e.target.value)}
      onFocus={onFocus}
      onBlur={blur}
    />
  )
}
