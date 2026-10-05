export type Memory = { slot: string; statement: string; from: string | null; said_at: string | null }

export type Thread = { slot: string; statement: string; seen_at: string }

export type Waiting = {
  slot: string
  holds: string | null
  suggests: string | null
  reason: string
  from: string | null
  withheld: boolean
  evidence: string | null
}

export type Status = {
  project: string
  withheld: number
  last_dream: string | null
  index: { lines: number; line_limit: number; bytes: number; byte_limit: number }
  memories: Memory[]
  waiting: Waiting[]
  open_threads: Thread[]
}

export type View = 'home' | 'memories' | 'threads'

declare module 'claude-code' {
  interface PluginState {
    remcycle: { status: Status | null; view: View; at: number; page: number }
  }
}
