import { useYourReminder } from './notifications'

/**
 * The member's own daily reminder, after the household's: "yours at 8:00 AM"
 * (the time in bold where the mockup has it), or that theirs are off.
 */
export function YourReminder({ bold = false }: { bold?: boolean }) {
  const { time, off } = useYourReminder()
  if (off) return <>yours are off</>
  if (time === null) return <>yours at your notification time</>
  return bold ? (
    <>
      yours at <b>{time}</b>
    </>
  ) : (
    <>yours at {time}</>
  )
}
