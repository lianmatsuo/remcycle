import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const NOW = Date.UTC(2026, 9, 5, 15)

const STATUS = {
  project: '/work/shop',
  withheld: 1,
  last_dream: '2026-10-05T13:00:00+00:00',
  index: { lines: 18, line_limit: 200, bytes: 2400, byte_limit: 25000 },
  memories: [
    { slot: 'ci-runner', statement: 'CI is self-hosted.', from: null, said_at: null },
    {
      slot: 'package-manager',
      statement: 'Use pnpm for JS projects.',
      from: 'human',
      said_at: '2026-10-02T15:00:00+00:00',
    },
    {
      slot: 'review-style',
      statement: 'Reviews should name the file and line.',
      from: 'inferred',
      said_at: '2026-10-05T09:00:00+00:00',
    },
  ],
  waiting: [
    {
      slot: 'deploy-target',
      holds: 'Deploys go to the staging cluster first.',
      suggests: 'Deploys go straight to production.',
      reason: '',
      from: 'inferred',
      withheld: false,
      evidence: 'dream show 7f3a9c2e --first 1 --last 1',
    },
    {
      slot: 'cutover-status',
      holds: 'The database cutover is planned for 12 August.',
      suggests: null,
      reason: 'it looks dated: the cutover date has passed',
      from: null,
      withheld: true,
      evidence: null,
    },
  ],
  open_threads: [
    { slot: 'ci-cache', statement: 'CI cache for pnpm is not set up.', seen_at: '2026-10-03T15:00:00+00:00' },
  ],
}

const PANE = {
  plugin: 'remcycle',
  component: 'Pane',
  requestId: 'remcycle',
  props: {
    title: 'remcycle',
    isFocused: false,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 24 },
    view: {},
  },
} as const

/** Stands in for the `dream` command line: answers each subcommand from `answers` and records every call. */
function commandLine(on: On, answers: Record<string, string>): string[][] {
  const calls: string[][] = []

  on('process.run', async (_$, e) => {
    calls.push([...e.argv])
    const out = answers[e.argv[1] ?? '']

    return {
      value: {
        exitCode: out === undefined ? 1 : 0,
        stdout: out ?? '',
        stderr: '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })

  return calls
}

test('a new conversation is given what applies everywhere and what the project left open', async ($, on) => {
  commandLine(on, {
    context: JSON.stringify({
      everywhere: ['Use pnpm for JS projects.'],
      open_threads: ['CI cache for pnpm is not set up.'],
    }),
  })

  on('prompt.context', async (_$, e) => ({ blocks: e.blocks }))

  const { blocks } = await $.prompt.context({ blocks: [] })

  expect(blocks).toEqual([
    {
      name: 'remcycleEverywhere',
      text: 'What this person has said applies to all their work:\n- Use pnpm for JS projects.',
    },
    {
      name: 'remcycleOpenThreads',
      text: 'Left open by earlier sessions in this project:\n- CI cache for pnpm is not set up.',
    },
  ])
})

test('a conversation starts unchanged when the command line cannot be reached', async ($, on) => {
  commandLine(on, {})

  on('prompt.context', async (_$, e) => ({ blocks: e.blocks }))

  const { blocks } = await $.prompt.context({ blocks: [{ name: 'currentDate', text: '2026-10-05' }] })

  expect(blocks).toEqual([{ name: 'currentDate', text: '2026-10-05' }])
})

test('recall searches the archive with the words asked for', async ($, on) => {
  const calls = commandLine(on, { search: '2026-10-01  assistant  7f3a9c2e#1  Retention sweep\n' })

  const answer = await $.tool.call({
    tool: 'mcp__remcycle__recall',
    query: 'retention  sweep',
    also: ['old transcripts deleted'],
    everywhere: true,
  })

  expect(answer.result).toBe('2026-10-01  assistant  7f3a9c2e#1  Retention sweep\n')
  expect(calls).toEqual([
    ['dream', 'search', '--all-projects', '--also', 'old transcripts deleted', '--', 'retention', 'sweep'],
  ])
})

test('recall reads turns back when given a session', async ($, on) => {
  const calls = commandLine(on, { show: '#3 human\nalways use pnpm\n' })

  const answer = await $.tool.call({ tool: 'mcp__remcycle__recall', session: '7f3a9c2e', first: 3, last: 4 })

  expect(answer.result).toBe('#3 human\nalways use pnpm\n')
  expect(calls).toEqual([['dream', 'show', '7f3a9c2e', '--first', '3', '--last', '4']])
})

test('reading a memory whose subject has gone tells the model so', async ($, on) => {
  const warning = 'This memory is about infra/deploy.sh, which no longer exists in the repository.'
  const calls = commandLine(on, { 'note-read': `${warning}\n` })
  on('tool.call', { tool: 'Read' }, async () => ({ result: { type: 'text' } as never }))

  const memory = await $.tool.call({ tool: 'Read', file_path: '/home/me/.claude/projects/-work-shop/memory/deploy-script.md' })
  const source = await $.tool.call({ tool: 'Read', file_path: '/work/shop/src/app.ts' })

  expect(memory.context).toEqual([warning])
  expect(source.context).toBeUndefined()
  expect(calls).toEqual([['dream', 'note-read', '/home/me/.claude/projects/-work-shop/memory/deploy-script.md']])
})

test('the archive is brought up to date when a session ends', async ($, on) => {
  const calls = commandLine(on, { ingest: '1 added, 0 updated, 0 unchanged, 0 excluded\n' })

  on('session.end', async (_$, e) => ({ sessionId: e.sessionId }))

  const sessionId = '7f3a9c2e-1b4d-4e6f-8a90-123456789abc'

  await $.session.end({ reason: 'clear', sessionId, resume: { id: sessionId } })

  expect(calls).toEqual([['dream', 'ingest']])
})

test('the pane opens on a summary of what needs the person, what is remembered and how full the index is', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })

  const terminal = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await terminal.press({ key: 'refresh' })

  expect(await terminal.find({ type: 'Text', text: /^shop$/ })).toBeDefined()
  expect(await terminal.find({ type: 'Text', text: /^Changed by a dream 2h ago$/ })).toBeDefined()
  expect(await terminal.find({ type: 'Text', text: /^2 need you$/ })).toBeDefined()
  expect(await terminal.find({ type: 'Text', text: /^3 memories$/ })).toBeDefined()
  expect(await terminal.find({ type: 'Text', text: /^Index 10% full$/ })).toBeDefined()
  await terminal.unmount()

  const desktop = await $.ui.mount({ ...PANE, surface: 'desktop' })
  const tiles = await desktop.find({ type: 'Svg' })

  expect(tiles?.props.alt).toBe(
    '2 need you. 3 memories in use, 2 learned from sessions. The index is 10% full: 2.4 of 25 KB.',
  )
  expect(tiles?.props.source).toMatch(/^<svg [^>]*width="440"/)
  expect(await desktop.find({ type: 'Text', text: /^Changed by a dream 2h ago$/ })).toBeDefined()
  await desktop.unmount()
})

test('questions come one at a time with both sides, and a ruling is passed on', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), resolve: 'deploy-target: took the new claim\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^1 of 2$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploy target$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploys go to the staging cluster first\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploys go straight to production\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /guessed from a session/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /The database cutover/ })).toBeUndefined()

    await ui.press({ key: 'next' })

    expect(await ui.find({ type: 'Text', text: /^2 of 2$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Cutover status$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^looks dated$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^The database cutover is planned for 12 August\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^The cutover date has passed$/ })).toBeDefined()
    expect((await ui.find({ type: 'Text', text: /^Kept out of sessions until you rule\.$/ }))?.props.color).toBe('warning')
    expect((await ui.find({ key: 'accept-cutover-status' }))?.props.label).toBe('Retire it')
    expect(await ui.find({ type: 'Text', text: /Deploys go straight to production/ })).toBeUndefined()

    await ui.press({ key: 'keep-cutover-status' })
    await ui.press({ key: 'next' })
    await ui.press({ key: 'accept-deploy-target' })

    expect(calls).toContainEqual(['dream', 'resolve', 'cutover-status', '--keep'])
    expect(calls).toContainEqual(['dream', 'resolve', 'deploy-target', '--accept'])
    await ui.unmount()
  }
})

test('with nothing waiting the pane says so and offers no question', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify({ ...STATUS, waiting: [] }) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^Nothing needs you\.$/ })).toBeDefined()
    expect(await ui.find({ key: 'next' })).toBeUndefined()
    await ui.unmount()
  }
})

test('the newest things the dream learned are listed, and every memory is a button away', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^Review style$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^6h$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Package manager$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^3d$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /CI is self-hosted/ })).toBeUndefined()
    expect((await ui.find({ key: 'show-memories' }))?.props.label).toBe('All 3')

    await ui.press({ key: 'show-memories' })

    expect(await ui.find({ type: 'Text', text: /^CI is self-hosted\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Written before remcycle$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^You said this$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Guessed from a session, not confirmed by you$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /Deploys go straight to production/ })).toBeUndefined()

    await ui.press({ key: 'show-home' })

    expect(await ui.find({ type: 'Text', text: /^1 of 2$/ })).toBeDefined()
    await ui.unmount()
  }
})

test('a long list of memories is shown a page at a time', async ($, on) => {
  mock.clock(on, { now: NOW })
  const memories = Array.from({ length: 14 }, (_, i) => ({
    slot: `note-${String(i).padStart(2, '0')}`,
    statement: `Statement ${i}.`,
    from: null,
    said_at: null,
  }))
  commandLine(on, { status: JSON.stringify({ ...STATUS, memories }) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })
    await ui.press({ key: 'show-memories' })

    expect(await ui.find({ type: 'Text', text: /^1 to 12 of 14$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Statement 11\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Statement 12\.$/ })).toBeUndefined()

    await ui.press({ key: 'later' })

    expect(await ui.find({ type: 'Text', text: /^13 to 14 of 14$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Statement 13\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Statement 0\.$/ })).toBeUndefined()

    await ui.press({ key: 'show-home' })
    await ui.unmount()
  }
})

test('a thread left open shows its age and can be marked done', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), close: 'ci-cache: closed\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^Ci cache$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^CI cache for pnpm is not set up\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^2d$/ })).toBeDefined()

    await ui.press({ key: 'close-ci-cache' })

    expect(calls).toContainEqual(['dream', 'close', 'ci-cache'])
    await ui.unmount()
  }
})

test('an answer this version of the pane cannot read is treated as no answer', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, {
    status: JSON.stringify({ project: '/work/shop', entries: 2, withheld: 0, learned: [], waiting: [], open_threads: ['x'] }),
  })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /No readable answer from the `dream` command yet/ })).toBeDefined()
    await ui.unmount()
  }
})
