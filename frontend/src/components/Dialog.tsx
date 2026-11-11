import { useEffect, useId, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
export function Dialog({
  title,
  onClose,
  busy,
  children,
}: {
  title: string
  onClose: () => void
  busy: boolean
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const id = useId()
  useEffect(() => {
    const node = ref.current
    node?.showModal()
    return () => node?.close()
  }, [])
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onCancel={(e) => {
        e.preventDefault()
        if (!busy) onClose()
      }}
    >
      <div className="dialog-heading">
        <h2 id={id}>{title}</h2>
        <button
          type="button"
          className="icon"
          aria-label="Fechar"
          disabled={busy}
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  )
}
