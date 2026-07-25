export const CONTROL_OP_ROOM_JOIN = 'room.join' as const

export const SESSION_INTENT_FIELD = 'session_intent' as const
export const SESSION_INTENT_USER_INITIATED = 'user_initiated' as const
export const SESSION_INTENT_PROACTIVE = 'proactive_initiated' as const

export type SessionIntent =
  | typeof SESSION_INTENT_USER_INITIATED
  | typeof SESSION_INTENT_PROACTIVE

export interface RoomJoinPayload {
  [SESSION_INTENT_FIELD]: SessionIntent
}
