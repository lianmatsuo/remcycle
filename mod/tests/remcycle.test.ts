import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const STATUS = {
  project: '/work/shop',
  entries: 2,
  withheld: 0,
  learned: [
    {
      slot: 'package-manager',
      statement: 'Use pnpm for JS projects.',
      from: 'human',
      evidence: 'dream show 7f3a9c2e --first 2 --last 2',
    },
  ],
  waiting: [
    {
      slot: 'deploy-target',
      suggests: 'Deploys go straight to production.',
      reason: '',
      from: 'inferred',
      withheld: false,
      evidence: 'dream show 7f3a9c2e --first 1 --last 1',
    },
    {
      slot: 'cutover-status',
      suggests: null,
      reason: 'it looks dated: the cutover date has passed',
      from: null,
      withheld: false,
      evidence: null,
    },
  ],
  open_threads: ['CI cache for pnpm is not set up.'],
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

test('the pane shows what waits for a ruling and passes the ruling on', async ($, on) => {
  const calls = commandLine(on, { status: JSON.stringify(STATUS), resolve: 'deploy-target: took the new claim\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /2 in use, 0 withheld/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /deploy-target: a session suggests \(inferred\) Deploys go straight/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /CI cache for pnpm is not set up/ })).toBeDefined()

    expect(await ui.find({ type: 'Text', text: /cutover-status: it looks dated: the cutover date has passed/ })).toBeDefined()
    expect((await ui.find({ key: 'accept-cutover-status' }))?.props.label).toBe('Retire it')

    await ui.press({ key: 'accept-deploy-target' })
    await ui.press({ key: 'keep-cutover-status' })

    expect(calls).toContainEqual(['dream', 'resolve', 'deploy-target', '--accept'])
    expect(calls).toContainEqual(['dream', 'resolve', 'cutover-status', '--keep'])
    await ui.unmount()
  }
})
