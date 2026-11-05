export type Memory = { slot: string; statement: string; from: string | null; said_at: string | null }

export type Thread = { slot: string; statement: string; seen_at: string }

export type Closure = { slot: string; statement: string; at: string; by: string; why: string }

export type Elsewhere = { project: string; waiting: number }

export type Waiting = {
  slot: string
  holds: string | null
  suggests: string | null
  reasons: string[]
  from: string | null
  withheld: boolean
  evidence: string | null
  file: string
}

export type Daily = {
  on: boolean
  history: string | null
  limit: number | null
  waiting: { new: number; week: number; all: number }
  started: string | null
  finished: string | null
  failed: string | null
}

/** The Claude Code folder the dream signs in through, null for the session's own, and the folders found to choose from. */
export type SignIn = { folder: string | null; folders: string[] }

/** A Claude Code folder found in the home folder, and whether the sessions in it are read. */
export type SessionFolder = { folder: string; read: boolean }

export type Status = {
  daily?: Daily
  sign_in?: SignIn
  session_folders?: SessionFolder[]
  project: string
  withheld: number
  last_dream: string | null
  index: { lines: number; line_limit: number; bytes: number; byte_limit: number }
  memories: Memory[]
  waiting: Waiting[]
  open_threads: Thread[]
  elsewhere: Elsewhere[]
  closed_lately: Closure[]
}

export type View = 'home' | 'memories' | 'threads'

declare module 'claude-code' {
  interface PluginState {
    remcycle: {
      status: Status | null
      project: string | null
      view: View
      at: number
      page: number
      busy: string | null
      last: string | null
      choosing: boolean
      choosingFolders: boolean
    }
  }
}
