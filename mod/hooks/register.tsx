import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Closure, Memory, Status, Thread, View } from '../types'

const PANE = 'remcycle'
const RECALL = 'mcp__remcycle__recall'
const CLOSE = 'mcp__remcycle__close_thread'
const MEMORY_FILE = /\/memory\/[^/]+\.md$/
const LATELY = 3
const PER_PAGE = 12
const BRIEF = 80

const status = atom({ plugin: 'remcycle', key: 'status' } as const, null)
// The project the pane shows when it is not the session's own.
const project = atom({ plugin: 'remcycle', key: 'project' } as const, null)
const view = atom({ plugin: 'remcycle', key: 'view' } as const, 'home')
const at = atom({ plugin: 'remcycle', key: 'at' } as const, 0)
const page = atom({ plugin: 'remcycle', key: 'page' } as const, 0)

/** How each kind of source reads to the person it came from. */
const SAID: Record<string, string> = {
  human: 'You said this',
  accepted: 'You agreed to this',
  inferred: 'Guessed from a session, not confirmed by you',
  observed: "Taken from a subagent's report",
}
const UNSOURCED = 'Written before remcycle'
const CLOSED_BY: Record<string, string> = {
  you: 'You closed it',
  session: 'A session closed it',
  dream: 'The dream closed it',
}

// The tiles are drawn as an image, which cannot see the surface's theme: each carries its own
// dark ground and light text so it reads the same on a light surface as on a dark one.
const TILE = { ground: '#2c2c2a', track: '#45443f', text: '#f5f4ef', quiet: '#a3a29b' }
const FINE = '#1D9E75'
const WATCH = '#EF9F27'
const OVER = '#E24B4A'
const SOURCES: [string | null, string, string][] = [
  ['human', FINE, 'you said'],
  ['accepted', '#7F77DD', 'you agreed'],
  ['inferred', WATCH, 'guessed'],
  ['observed', '#D85A30', 'from a subagent'],
  [null, '#888780', 'written before remcycle'],
]

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** The start of a statement, cut at a word. The first screen shows this, and the whole is a button away. */
function brief(text: string): string {
  if (text.length <= BRIEF) {
    return text
  }

  const cut = text.slice(0, BRIEF)

  return `${cut.slice(0, Math.max(cut.lastIndexOf(' '), 1)).replace(/[\s,;:.]+$/, '')}…`
}

function named(path: string): string {
  return path.split('/').filter(Boolean).at(-1) ?? path
}

/** A slot is a key such as `deploy-target`; this is how it reads as a heading. */
function heading(slot: string): string {
  return sentence(slot.replace(/[-_]+/g, ' ').trim())
}

/** Splits a reason such as "it looks dated: the date has passed" into a short tag and the rest. */
function tagged(reason: string): [string | null, string] {
  const cut = /^it ([^:]{1,40}): (.+)$/s.exec(reason)
  const [tag, rest] = [cut?.[1], cut?.[2]]

  return tag === undefined || rest === undefined ? [null, sentence(reason)] : [tag.replace(/`/g, ''), sentence(rest)]
}

/** How long ago `iso` was, as short as a column allows: `5m`, `6h`, `3d`. Empty when unknown. */
function ago(iso: string | null, now: number): string {
  const then = Date.parse(iso ?? '')

  if (Number.isNaN(then)) {
    return ''
  }

  const minutes = Math.max(0, Math.floor((now - then) / 60_000))
  const days = Math.floor(minutes / 1440)

  if (minutes < 60) {
    return minutes < 1 ? 'now' : `${minutes}m`
  }

  if (minutes < 2880) {
    return `${Math.floor(minutes / 60)}h`
  }

  return days < 60 ? `${days}d` : days < 730 ? `${Math.floor(days / 30)}mo` : `${Math.floor(days / 365)}y`
}

function newestFirst<T>(items: readonly T[], when: (item: T) => string | null): T[] {
  const time = (item: T) => Date.parse(when(item) ?? '') || 0

  return [...items].sort((a, b) => time(b) - time(a))
}

/** How full the index is against whichever of its two limits is nearer. */
function fullness(index: Status['index']): { percent: number; of: string } {
  const [lines, bytes] = [index.lines / index.line_limit, index.bytes / index.byte_limit]

  return {
    percent: Math.round(100 * Math.max(lines, bytes)),
    of:
      bytes > lines
        ? `${(index.bytes / 1000).toFixed(1)} of ${index.byte_limit / 1000} KB`
        : `${index.lines} of ${index.line_limit} lines`,
  }
}

/** The three figures at the top of the pane as one SVG image. Every value drawn is a number. */
function tiles(now: Status): string {
  const [width, height, tile, pad] = [440, 92, 140, 12]
  const bar = tile - 2 * pad
  const total = now.memories.length
  const learned = now.memories.filter(memory => memory.from !== null).length
  const full = fullness(now.index)
  const alarm = full.percent >= 100 ? OVER : full.percent >= 80 ? WATCH : FINE

  const draw = (n: number, label: string, figure: string, color: string, parts: [number, string][], note: string) => {
    const x = n * (tile + 10) + pad
    let from = x
    const filled = parts.map(([share, fill]) => {
      const piece = `<rect x="${from.toFixed(1)}" y="60" width="${(share * bar).toFixed(1)}" height="6" fill="${fill}"/>`
      from += share * bar

      return piece
    })

    return (
      `<rect x="${x - pad}" width="${tile}" height="${height}" rx="8" fill="${TILE.ground}"/>` +
      `<text x="${x}" y="22" font-size="12" fill="${TILE.quiet}">${label}</text>` +
      `<text x="${x}" y="50" font-size="22" font-weight="500" fill="${color}">${figure}</text>` +
      `<clipPath id="bar${n}"><rect x="${x}" y="60" width="${bar}" height="6" rx="3"/></clipPath>` +
      `<g clip-path="url(#bar${n})"><rect x="${x}" y="60" width="${bar}" height="6" fill="${TILE.track}"/>${filled.join('')}</g>` +
      `<text x="${x}" y="82" font-size="11" fill="${TILE.quiet}">${note}</text>`
    )
  }

  const bySource = SOURCES.map(
    ([source, fill]): [number, string] => [
      total === 0 ? 0 : now.memories.filter(memory => memory.from === source).length / total,
      fill,
    ],
  )

  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" ` +
    `font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif">` +
    draw(
      0,
      'Needs you',
      String(now.waiting.length),
      now.waiting.length > 0 ? WATCH : TILE.text,
      [[total === 0 ? 0 : Math.min(1, now.waiting.length / total), WATCH]],
      now.waiting.length === 0 ? 'nothing to rule on' : now.waiting.length === 1 ? 'question to rule on' : 'questions to rule on',
    ) +
    draw(1, 'Memories', String(total), TILE.text, bySource, `${learned} learned · ${total - learned} older`) +
    draw(2, 'Index used', `${full.percent}%`, full.percent >= 80 ? alarm : TILE.text, [[Math.min(1, full.percent / 100), alarm]], full.of) +
    `</svg>`
  )
}

/** The status as this version of the pane reads it, or null if `value` is not one. */
function readable(value: unknown): Status | null {
  const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null
  const isText = (v: unknown) => typeof v === 'string'
  const isTextOrNull = (v: unknown) => v === null || isText(v)
  const isListOf = (v: unknown, isOne: (item: Record<string, unknown>) => boolean) =>
    Array.isArray(v) && v.every(item => isRecord(item) && isOne(item))

  if (!isRecord(value)) {
    return null
  }

  const index = value.index
  const isStatus =
    isText(value.project) &&
    typeof value.withheld === 'number' &&
    isTextOrNull(value.last_dream) &&
    isRecord(index) &&
    ['lines', 'line_limit', 'bytes', 'byte_limit'].every(key => typeof index[key] === 'number') &&
    isListOf(value.memories, m => isText(m.slot) && isText(m.statement) && isTextOrNull(m.from) && isTextOrNull(m.said_at)) &&
    isListOf(
      value.waiting,
      w =>
        isText(w.slot) &&
        isTextOrNull(w.holds) &&
        isTextOrNull(w.suggests) &&
        isText(w.reason) &&
        isTextOrNull(w.from) &&
        typeof w.withheld === 'boolean' &&
        isTextOrNull(w.evidence),
    ) &&
    isListOf(value.open_threads, t => isText(t.slot) && isText(t.statement) && isText(t.seen_at)) &&
    isListOf(value.elsewhere, o => isText(o.project) && typeof o.waiting === 'number') &&
    isListOf(
      value.closed_lately,
      c => isText(c.slot) && isText(c.statement) && isText(c.at) && isText(c.by) && isText(c.why),
    )

  return isStatus ? (value as Status) : null
}

/** Runs remcycle's command line and returns what it printed, or null if it failed. */
async function dream($: EngineInterface, args: string[], timeoutMs = 30_000): Promise<string | null> {
  const ran = await $.process.run(['dream', ...args], { timeoutMs })

  return ran.exitCode === 0 ? ran.stdout : null
}

/** The arguments that point a command at the project the pane shows. */
async function scope($: EngineInterface): Promise<string[]> {
  const other = await read($, project)

  return other === null ? [] : ['--project', other]
}

async function refresh($: EngineInterface): Promise<void> {
  const out = await dream($, ['status', ...(await scope($))])
  let answer: unknown = null

  try {
    answer = out === null ? null : JSON.parse(out)
  } catch {
    answer = null
  }

  const now = readable(answer)
  await update($, status, () => now)
}

async function rule($: EngineInterface, slot: string, isAccepted: boolean): Promise<void> {
  await dream($, ['resolve', ...(await scope($)), slot, isAccepted ? '--accept' : '--keep'])
  await refresh($)
}

async function close($: EngineInterface, slot: string): Promise<void> {
  await dream($, ['close', ...(await scope($)), '--', slot])
  await refresh($)
}

async function reopen($: EngineInterface, slot: string): Promise<void> {
  await dream($, ['reopen', ...(await scope($)), '--', slot])
  await refresh($)
}

/** Points the pane at another project, or with null back at the session's own. */
async function look($: EngineInterface, other: string | null): Promise<void> {
  await update($, project, () => other)
  await update($, at, () => 0)
  await update($, page, () => 0)
  await update($, view, () => 'home')
  await refresh($)
}

async function show($: EngineInterface, next: View): Promise<void> {
  await update($, page, () => 0)
  await update($, view, () => next)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'recall',
      description:
        'Search memory and the archive of past Claude Code sessions for what was said or decided. ' +
        'Give `query` to get matching memories and the best-matching turns, each labelled session#turn. ' +
        'Give `also` with other ways of asking the same thing to widen the search. ' +
        'Give `session` with `first` and `last` to read those turns back word for word. ' +
        'Searches this project unless `everywhere` is true.',
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Words to search for' },
          also: { type: 'array', items: { type: 'string' }, description: 'Other phrasings of the same question' },
          everywhere: { type: 'boolean', description: 'Search every project' },
          reports: { type: 'boolean', description: "Also search subagents' final reports" },
          session: { type: 'string', description: 'A session id, or the start of one, to read back' },
          first: { type: 'integer', description: 'First turn to read back' },
          last: { type: 'integer', description: 'Last turn to read back' },
        },
      },
    })
    await $.tool.register({
      name: 'close_thread',
      description:
        'Close a thread of unfinished work that this conversation has now finished. The threads are listed at the ' +
        'start of the conversation under "Left open by earlier sessions", each under its name. Give `slot`, the ' +
        "thread's name as listed, and `reason`, one line on what finished it. Call it only once the work is done, " +
        'not when it was merely discussed. The person can reopen it.',
      inputSchema: {
        type: 'object',
        properties: {
          slot: { type: 'string', description: "The thread's name, as listed" },
          reason: { type: 'string', description: 'One line on what finished it' },
        },
        required: ['slot', 'reason'],
      },
    })
    await $.command.register({
      name: 'remcycle',
      description: 'Show what the dream holds for this project and what waits for your ruling',
    })

    return next(e)
  })

  // Asked once per conversation, so the session sees one stable answer and the prompt cache holds.
  on('prompt.context', async ($, e, next) => {
    const out = await dream($, ['context'])

    if (out === null) {
      return next(e)
    }

    type Named = { slot: string; statement: string }
    const given: {
      everywhere: string[]
      learned?: Named[]
      learned_in?: string
      learned_more?: number
      threads?: Named[]
    } = JSON.parse(out)
    const learned = given.learned ?? []
    const threads = given.threads ?? []
    const blocks = [...e.blocks]

    if (given.everywhere.length > 0) {
      blocks.push({
        name: 'remcycleEverywhere',
        text:
          'What this person has said applies to all their work:\n' +
          given.everywhere.map(line => `- ${line}`).join('\n'),
      })
    }

    if (learned.length > 0) {
      const more = given.learned_more ?? 0

      blocks.push({
        name: 'remcycleLearned',
        text:
          'What earlier sessions established about this project, each said or agreed to by the person, under its ' +
          'name:\n' +
          learned.map(memory => `- ${memory.slot}: ${memory.statement}`).join('\n') +
          (more > 0
            ? `\n${more} older ${more === 1 ? 'one is' : 'ones are'} not listed here. The recall tool finds them.`
            : '') +
          `\nEach is a file named after it in ${given.learned_in ?? ''}, holding the reason and the session it came from.`,
      })
    }

    if (threads.length > 0) {
      blocks.push({
        name: 'remcycleOpenThreads',
        text:
          'Left open by earlier sessions in this project, each under its name:\n' +
          threads.map(thread => `- ${thread.slot}: ${thread.statement}`).join('\n') +
          '\nWhen work in this conversation finishes one of them, call the close_thread tool with its name and one ' +
          'line on what finished it. Do not close one that was only discussed.',
      })
    }

    return next({ ...e, blocks })
  })

  on('tool.call', { tool: RECALL }, async ($, e) => {
    const asked = e as Record<string, unknown>
    const args =
      typeof asked.session === 'string'
        ? [
            'show',
            asked.session,
            '--first',
            String(asked.first ?? 0),
            ...(asked.last === undefined ? [] : ['--last', String(asked.last)]),
          ]
        : [
            'search',
            ...(asked.everywhere === true ? ['--all-projects'] : []),
            ...(asked.reports === true ? ['--reports'] : []),
            ...(Array.isArray(asked.also) ? asked.also.flatMap(phrasing => ['--also', String(phrasing)]) : []),
            '--',
            ...String(asked.query ?? '')
              .split(/\s+/)
              .filter(word => word !== ''),
          ]
    const out = await dream($, args)

    return { result: out ?? 'remcycle could not be reached.' }
  })

  on('tool.call', { tool: CLOSE }, async ($, e) => {
    const asked = e as Record<string, unknown>
    const slot = String(asked.slot ?? '').trim()
    const reason = String(asked.reason ?? '').trim()

    if (slot === '' || reason === '') {
      return { result: 'Not closed: give the thread\'s name as `slot` and what finished it as `reason`.' }
    }

    const out = await dream($, ['close', `--why=${reason}`, `--session=${await $.session.id()}`, '--', slot])

    return { result: out ?? `Not closed: no open thread is named ${slot}, or remcycle could not be reached.` }
  })

  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const ran = await next(e)

    if (ran.deny !== undefined || !MEMORY_FILE.test(e.file_path)) {
      return ran
    }

    const warning = (await dream($, ['note-read', e.file_path]))?.trim()

    return warning ? { ...ran, context: [...(ran.context ?? []), warning] } : ran
  })

  on('session.end', async ($, e, next) => {
    await dream($, ['ingest'], 120_000)

    return next(e)
  })

  on('command.run', { command: 'remcycle' }, async $ => {
    await refresh($)
    await $.ui.open({ id: PANE, title: 'remcycle' })

    return { text: 'remcycle pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    // The terminal has no Svg, and its table answers the name with an element that draws nothing.
    const Svg = e.surface === 'terminal' ? null : $.ui.resolve(e).Svg
    const now = readable(await read($, status))

    if (now === null) {
      return (
        <Box flexDirection="column" rowGap={1}>
          <Text dimColor>No readable answer from the `dream` command yet.</Text>
          <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
        </Box>
      )
    }

    const clock = await $.clock.now()
    const shown = await read($, view)
    const viewing = await read($, project)
    const learned = newestFirst(
      now.memories.filter(memory => memory.from !== null),
      memory => memory.said_at,
    )
    const unsourced = newestFirst(
      now.memories.filter(memory => memory.from === null),
      memory => memory.said_at,
    )
    const threads = newestFirst(now.open_threads, thread => thread.seen_at)
    const color = (memory: Memory) => SOURCES.find(([source]) => source === memory.from)?.[1] ?? TILE.quiet

    const title = (label: string, aside: RenderChildren) => (
      <Box justifyContent="space-between" alignItems="center">
        <Text dimColor>{label}</Text>
        {aside}
      </Box>
    )

    // A row's title shares its line with what sits at the right edge. The statement gets a line of
    // its own, so however long it is it cannot push the age or a button out of the pane.
    const memoryRow = (memory: Memory, isWhole: boolean) => (
      <Box flexDirection="column">
        <Box justifyContent="space-between" columnGap={1}>
          <Box columnGap={1}>
            <Text color={color(memory)}>●</Text>
            <Text bold>{heading(memory.slot)}</Text>
          </Box>
          <Box flexShrink={0}>
            <Text dimColor>{ago(memory.said_at, clock)}</Text>
          </Box>
        </Box>
        <Box flexDirection="column" paddingLeft={2}>
          {isWhole ? <Text>{memory.statement}</Text> : <Text dimColor>{brief(memory.statement)}</Text>}
          {isWhole && <Text dimColor>{SAID[memory.from ?? ''] ?? UNSOURCED}</Text>}
        </Box>
      </Box>
    )

    const closureRow = (closure: Closure, isWhole: boolean) => {
      const how = (CLOSED_BY[closure.by] ?? 'Closed') + (closure.why === '' ? '' : `: ${closure.why}`)

      return (
        <Box flexDirection="column">
          <Box justifyContent="space-between" alignItems="center" columnGap={1}>
            <Text bold>{heading(closure.slot)}</Text>
            <Box columnGap={1} alignItems="center" flexShrink={0}>
              <Text dimColor>{ago(closure.at, clock)}</Text>
              <Button key={`reopen-${closure.slot}`} label="Reopen" onPress={() => reopen($, closure.slot)} />
            </Box>
          </Box>
          <Text dimColor>{isWhole ? how : brief(how)}</Text>
        </Box>
      )
    }

    const threadRow = (thread: Thread, isWhole: boolean) => (
      <Box flexDirection="column">
        <Box justifyContent="space-between" alignItems="center" columnGap={1}>
          <Text bold>{heading(thread.slot)}</Text>
          <Box columnGap={1} alignItems="center" flexShrink={0}>
            <Text dimColor>{ago(thread.seen_at, clock)}</Text>
            <Button key={`close-${thread.slot}`} label="Done" onPress={() => close($, thread.slot)} />
          </Box>
        </Box>
        {isWhole ? <Text>{thread.statement}</Text> : <Text dimColor>{brief(thread.statement)}</Text>}
      </Box>
    )

    if (shown === 'memories' || shown === 'threads') {
      const rows =
        shown === 'memories'
          ? [...learned, ...unsourced].map(memory => memoryRow(memory, true))
          : [
              ...threads.map(thread => threadRow(thread, true)),
              ...(now.closed_lately.length > 0 ? [<Text dimColor>Closed lately</Text>] : []),
              ...now.closed_lately.map(closure => closureRow(closure, true)),
            ]
      const first = Math.min(await read($, page), Math.max(0, Math.ceil(rows.length / PER_PAGE) - 1)) * PER_PAGE
      const last = Math.min(rows.length, first + PER_PAGE)

      return (
        <Box flexDirection="column" rowGap={1}>
          <Box justifyContent="space-between" alignItems="center">
            <Text bold>{shown === 'memories' ? 'All memories' : 'Left open'}</Text>
            <Button key="show-home" label="Back" onPress={() => show($, 'home')} />
          </Box>
          <Text dimColor>
            {shown === 'memories'
              ? `${learned.length} learned from your sessions, ${unsourced.length} written before remcycle.`
              : 'Unfinished work earlier sessions left behind. New sessions here are told about it.'}
          </Text>
          {rows.slice(first, last)}
          {rows.length > PER_PAGE && (
            <Box columnGap={1} alignItems="center">
              {first > 0 && <Button key="sooner" label="Previous" onPress={() => update($, page, n => Math.max(0, n - 1))} />}
              <Text dimColor>{`${first + 1} to ${last} of ${rows.length}`}</Text>
              {last < rows.length && <Button key="later" label="Next" onPress={() => update($, page, n => n + 1)} />}
            </Box>
          )}
        </Box>
      )
    }

    const full = fullness(now.index)
    const position = now.waiting.length === 0 ? 0 : (await read($, at)) % now.waiting.length
    const question = now.waiting[position]
    const [tag, why] = question === undefined ? [null, ''] : tagged(question.reason)
    const changed = ago(now.last_dream, clock)

    return (
      <Box flexDirection="column" rowGap={1}>
        <Box justifyContent="space-between" alignItems="center">
          <Box flexDirection="column">
            <Text bold>{named(now.project)}</Text>
            <Text dimColor>
              {changed === ''
                ? 'No dream has changed this yet'
                : `Changed by a dream ${changed === 'now' ? 'just now' : `${changed} ago`}`}
            </Text>
          </Box>
          <Box columnGap={1} flexShrink={0}>
            {viewing !== null && <Button key="home-project" label="Back" onPress={() => look($, null)} />}
            <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
          </Box>
        </Box>
        {Svg === null ? (
          <Box columnGap={3} flexWrap="wrap">
            {now.waiting.length > 0 ? (
              <Text bold color="warning">{`${now.waiting.length} need you`}</Text>
            ) : (
              <Text>0 need you</Text>
            )}
            <Text>{`${now.memories.length} memories`}</Text>
            <Text>{`Index ${full.percent}% full`}</Text>
          </Box>
        ) : (
          <Svg
            source={tiles(now)}
            alt={
              `${now.waiting.length} need you. ${now.memories.length} memories in use, ` +
              `${learned.length} learned from sessions. The index is ${full.percent}% full: ${full.of}.`
            }
          />
        )}
        {title('Needs you', question !== undefined && <Text dimColor>{`${position + 1} of ${now.waiting.length}`}</Text>)}
        {question === undefined ? (
          <Text dimColor>Nothing needs you in this project.</Text>
        ) : (
          <Box flexDirection="column" rowGap={1} borderStyle="round" borderDimColor paddingX={1}>
            <Box columnGap={2}>
              <Text bold>{heading(question.slot)}</Text>
              <Text color="warning">{question.suggests === null ? (tag ?? 'in question') : 'a session disagrees'}</Text>
            </Box>
            {question.holds !== null && (
              <Box flexDirection="column">
                <Text dimColor>Memory says</Text>
                <Text>{question.holds}</Text>
              </Box>
            )}
            {question.suggests === null ? (
              <Box flexDirection="column">
                <Text dimColor>Why it is in question</Text>
                <Text>{why}</Text>
              </Box>
            ) : (
              <Box flexDirection="column">
                <Text dimColor>
                  Suggested instead · {(SAID[question.from ?? ''] ?? 'from a session').toLowerCase()}
                </Text>
                <Text>{question.suggests}</Text>
                {question.evidence !== null && <Text dimColor>To read the conversation: {question.evidence}</Text>}
              </Box>
            )}
            {question.withheld && <Text color="warning">Kept out of sessions until you rule.</Text>}
            <Box justifyContent="space-between">
              <Box columnGap={1}>
                <Button
                  key={`accept-${question.slot}`}
                  label={question.suggests === null ? 'Retire it' : 'Take it'}
                  onPress={() => rule($, question.slot, true)}
                />
                <Button
                  key={`keep-${question.slot}`}
                  label={question.suggests === null ? 'Keep it' : 'Keep mine'}
                  onPress={() => rule($, question.slot, false)}
                />
              </Box>
              {now.waiting.length > 1 && <Button key="next" label="Next" onPress={() => update($, at, n => n + 1)} />}
            </Box>
          </Box>
        )}
        {now.elsewhere.length > 0 && <Text dimColor>Waiting in other projects</Text>}
        {now.elsewhere.slice(0, 4).map((other, n) => (
          <Box justifyContent="space-between" alignItems="center" columnGap={1}>
            <Text>{`${named(other.project)}: ${other.waiting} waiting`}</Text>
            <Button key={`elsewhere-${n}`} label="Open" onPress={() => look($, other.project)} />
          </Box>
        ))}
        {title(
          'Learned lately',
          <Button key="show-memories" label={`All ${now.memories.length}`} onPress={() => show($, 'memories')} />,
        )}
        {learned.length === 0 && <Text dimColor>The dream has learned nothing here yet.</Text>}
        {learned.slice(0, LATELY).map(memory => memoryRow(memory, false))}
        {now.memories.length > 0 && (
          <Box columnGap={2} flexWrap="wrap">
            {SOURCES.filter(([source]) => now.memories.some(memory => memory.from === source)).map(([, fill, label]) => (
              <Box columnGap={1}>
                <Text color={fill}>●</Text>
                <Text dimColor>{label}</Text>
              </Box>
            ))}
          </Box>
        )}
        {title(
          'Left open',
          threads.length + now.closed_lately.length > 0 && (
            <Button key="show-threads" label={`All ${threads.length}`} onPress={() => show($, 'threads')} />
          ),
        )}
        {threads.length === 0 && <Text dimColor>Earlier sessions left nothing open.</Text>}
        {threads.slice(0, LATELY).map(thread => threadRow(thread, false))}
        {now.closed_lately.length > 0 && title('Closed lately', <Text dimColor>{String(now.closed_lately.length)}</Text>)}
        {now.closed_lately.slice(0, LATELY).map(closure => closureRow(closure, false))}
      </Box>
    )
  })
}
