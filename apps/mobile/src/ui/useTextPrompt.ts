import { useIonAlert } from '@ionic/react'
import { isRecord } from '../lib/isRecord'

/** A line of text the prompt asks for. */
export interface PromptField {
  /** The text it starts with, e.g. the current name. */
  value?: string
  /** Shown while it's empty: what to type. */
  placeholder?: string
  maxLength: number
  /** An email address (its keyboard, and no capitals); a name otherwise. */
  email?: boolean
}

interface TextPrompt {
  title: string
  /** A line under the title, e.g. what happens next. */
  message?: string
  /** The fields, in order. */
  fields: PromptField[]
  /** Its button: "Save" unless given. */
  action?: string
  /**
   * Called with each field's trimmed text, in order, when saved; one left empty
   * can't be saved, and nothing changed isn't saved.
   */
  onSave: (values: string[]) => void
}

/** A small dialog to fill in or change a line or two of text, such as a name. */
export function useTextPrompt(): (prompt: TextPrompt) => void {
  const [present] = useIonAlert()
  return ({ title, message, fields, action, onSave }) =>
    void present({
      header: title,
      message,
      inputs: fields.map((field, index) => ({
        name: String(index),
        type: field.email ? ('email' as const) : ('text' as const),
        value: field.value ?? '',
        placeholder: field.placeholder,
        attributes: {
          maxlength: field.maxLength,
          autocapitalize: field.email ? 'none' : 'words',
          ...(field.email ? { autocorrect: 'off', spellcheck: false } : {}),
        },
      })),
      buttons: [
        { text: 'Cancel', role: 'cancel' },
        {
          text: action ?? 'Save',
          // The alert's values arrive untyped, by each input's name (its place).
          handler: (values: unknown) => {
            const texts = fields.map((_, index) => {
              const text = isRecord(values) ? values[String(index)] : undefined
              return typeof text === 'string' ? text.trim() : ''
            })
            if (texts.some((text) => !text)) return false // keeps the dialog open
            if (texts.some((text, index) => text !== (fields[index].value ?? ''))) onSave(texts)
            return true
          },
        },
      ],
    })
}
