import { useIonAlert } from '@ionic/react'
import { isRecord } from '../lib/isRecord'

interface TextPrompt {
  title: string
  /** The text it starts with, e.g. the current name. */
  value: string
  maxLength: number
  /** Called with the trimmed text when saved; an empty one can't be saved. */
  onSave: (text: string) => void
}

/** A small dialog to change one line of text, such as a name. */
export function useTextPrompt(): (prompt: TextPrompt) => void {
  const [present] = useIonAlert()
  return ({ title, value, maxLength, onSave }) =>
    void present({
      header: title,
      inputs: [{ name: 'text', type: 'text', value, attributes: { maxlength: maxLength, autocapitalize: 'words' } }],
      buttons: [
        { text: 'Cancel', role: 'cancel' },
        {
          text: 'Save',
          // The alert's values arrive untyped.
          handler: (values: unknown) => {
            const text = isRecord(values) && typeof values.text === 'string' ? values.text.trim() : ''
            if (!text) return false // keeps the dialog open
            if (text !== value) onSave(text)
            return true
          },
        },
      ],
    })
}
