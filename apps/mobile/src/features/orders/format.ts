import type { Member } from '../../lib/api/types'

/** "1 meal", "3 meals" */
export function plural(count: number, word: string): string {
  return `${count} ${count === 1 ? word : `${word}s`}`
}

/** "1:05" for 65 seconds. */
export function elapsed(from: Date, to: Date): string {
  const seconds = Math.max(0, Math.floor((to.getTime() - from.getTime()) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/** Who did something, from the signed-in member's point of view: "You", their name, or "Someone in your household". */
export function memberName(member: Member, userId: string | undefined): string {
  if (member.user_id === userId) return 'You'
  return member.name ?? 'Someone in your household'
}
