export type Learned = { slot: string; statement: string; from: string; evidence: string }

export type Waiting = {
  slot: string
  suggests: string | null
  reason: string
  from: string | null
  withheld: boolean
  evidence: string | null
}

export type Status = {
  project: string
  entries: number
  withheld: number
  learned: Learned[]
  waiting: Waiting[]
  open_threads: string[]
}

declare module 'claude-code' {
  interface PluginState {
    remcycle: { status: Status | null }
  }
}
