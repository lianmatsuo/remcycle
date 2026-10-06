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
      reasons: [],
      from: 'inferred',
      withheld: false,
      evidence: 'dream show 7f3a9c2e --first 1 --last 1',
      file: '/home/me/.claude/projects/-work-shop/memory/deploy-target.md',
    },
    {
      slot: 'cutover-status',
      holds: 'The database cutover is planned for 12 August.',
      suggests: null,
      reasons: ['it looks dated: the cutover date has passed'],
      from: null,
      withheld: true,
      evidence: null,
      file: '/home/me/.claude/projects/-work-shop/memory/cutover-status.md',
    },
  ],
  open_threads: [
    { slot: 'ci-cache', statement: 'CI cache for pnpm is not set up.', seen_at: '2026-10-03T15:00:00+00:00' },
  ],
  elsewhere: [{ project: '/work/site', waiting: 3 }],
  closed_lately: [
    {
      slot: 'lint-rules',
      statement: 'Lint rules are not agreed.',
      at: '2026-10-05T12:00:00+00:00',
      by: 'session',
      why: 'Agreed on the ruff defaults.',
    },
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
      learned: [{ slot: 'ci-runner', statement: 'CI is self-hosted.' }],
      learned_in: '/home/me/.local/share/remcycle/memory/-work-shop',
      learned_more: 2,
      threads: [{ slot: 'ci-cache', statement: 'CI cache for pnpm is not set up.' }],
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
      name: 'remcycleLearned',
      text:
        'What earlier sessions established about this project, each said or agreed to by the person, under its name:\n' +
        '- ci-runner: CI is self-hosted.\n' +
        '2 older ones are not listed here. The recall tool finds them.\n' +
        'Each is a file named after it in /home/me/.local/share/remcycle/memory/-work-shop, holding the reason and ' +
        'the session it came from.',
    },
    {
      name: 'remcycleOpenThreads',
      text:
        'Left open by earlier sessions in this project, each under its name:\n' +
        '- ci-cache: CI cache for pnpm is not set up.\n' +
        'When work in this conversation finishes one of them, call the close_thread tool with its name and one ' +
        'line on what finished it. Do not close one that was only discussed.',
    },
  ])
})

test('a conversation starts unchanged when the command line cannot be reached', async ($, on) => {
  commandLine(on, {})

  on('prompt.context', async (_$, e) => ({ blocks: e.blocks }))

  const { blocks } = await $.prompt.context({ blocks: [{ name: 'currentDate', text: '2026-10-05' }] })

  expect(blocks).toEqual([{ name: 'currentDate', text: '2026-10-05' }])
})

test('with the command line not installed, a conversation starts unchanged and recall says so', async ($, on) => {
  on('process.run', async () => {
    throw new Error('failed to start: ENOENT: Executable not found in $PATH: "dream"')
  })

  on('prompt.context', async (_$, e) => ({ blocks: e.blocks }))

  const { blocks } = await $.prompt.context({ blocks: [{ name: 'currentDate', text: '2026-10-05' }] })
  const answer = await $.tool.call({ tool: 'mcp__remcycle__recall', query: 'retention' })

  expect(blocks).toEqual([{ name: 'currentDate', text: '2026-10-05' }])
  expect(answer.result).toBe('remcycle could not be reached.')
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

test('closing a thread passes on its name, what finished it and which session said so', async ($, on) => {
  const calls = commandLine(on, { close: 'ci-cache: closed\n' })
  on('session.id', async () => ({ value: '7f3a9c2e-1b4d-4e6f-8a90-123456789abc' }))

  const answer = await $.tool.call({
    tool: 'mcp__remcycle__close_thread',
    slot: 'ci-cache',
    reason: 'The cache step is in the workflow now.',
  })

  expect(answer.result).toBe('ci-cache: closed\n')
  expect(calls).toEqual([
    [
      'dream',
      'close',
      '--why=The cache step is in the workflow now.',
      '--session=7f3a9c2e-1b4d-4e6f-8a90-123456789abc',
      '--',
      'ci-cache',
    ],
  ])
})

test('closing a thread that is not open, or without saying what finished it, is refused in words', async ($, on) => {
  const calls = commandLine(on, {})
  on('session.id', async () => ({ value: '7f3a9c2e-1b4d-4e6f-8a90-123456789abc' }))

  const unknown = await $.tool.call({ tool: 'mcp__remcycle__close_thread', slot: 'no-such-thread', reason: 'Done.' })
  const unexplained = await $.tool.call({ tool: 'mcp__remcycle__close_thread', slot: 'ci-cache', reason: ' ' })

  expect(unknown.result).toMatch(/no-such-thread/)
  expect(unexplained.result).toMatch(/what finished it/)
  expect(calls).toHaveLength(1)
})

test('recall gives a session in brief when asked for the session alone', async ($, on) => {
  const calls = commandLine(on, { show: '7f3a9c2e  2026-10-01  Refund path  42 turns\nAgreed that refunds post to the ledger first.\n' })

  const answer = await $.tool.call({ tool: 'mcp__remcycle__recall', session: '7f3a9c2e' })

  expect(answer.result).toBe('7f3a9c2e  2026-10-01  Refund path  42 turns\nAgreed that refunds post to the ledger first.\n')
  expect(calls).toEqual([['dream', 'show', '7f3a9c2e', '--summary']])
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

test('reading a memory in the copy the dream keeps is noted as well', async ($, on) => {
  const calls = commandLine(on, { 'note-read': '' })
  on('tool.call', { tool: 'Read' }, async () => ({ result: { type: 'text' } as never }))

  await $.tool.call({ tool: 'Read', file_path: '/home/me/.local/share/remcycle/memory/-work-shop/ci-runner.md' })

  expect(calls).toEqual([['dream', 'note-read', '/home/me/.local/share/remcycle/memory/-work-shop/ci-runner.md']])
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

test('a wide pane lays its content out in two columns, and the tiles keep their own size', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })
  const at = (bodyColumns: number) => $.ui.mount({ ...PANE, surface: 'desktop', props: { ...PANE.props, bodyColumns } })
  const widths = async (ui: Awaited<ReturnType<typeof at>>) =>
    Promise.all(['body', 'attention', 'record', 'tiles'].map(async key => (await ui.find({ key }))?.props.width))

  const narrow = await at(48)
  await narrow.press({ key: 'refresh' })
  expect(await widths(narrow)).toEqual([48, 48, 48, 48])
  expect((await narrow.find({ key: 'columns' }))?.props.flexDirection).toBe('column')
  await narrow.unmount()

  // Half of what is left once the gutter of 4 is taken out: (180 - 4) / 2.
  const wide = await at(180)
  expect(await widths(wide)).toEqual([180, 88, 88, 62])
  expect((await wide.find({ key: 'columns' }))?.props.flexDirection).toBe('row')
  await wide.unmount()

  // A column stops at 96 however wide the pane is, so the two and their gutter come to 196.
  const widest = await at(400)
  expect(await widths(widest)).toEqual([196, 96, 96, 62])
  await widest.unmount()
})

test("a question's buttons move onto a second row when the pane is too narrow for one", async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop', props: { ...PANE.props, bodyColumns: 48 } })
  await ui.press({ key: 'refresh' })
  const actions = await ui.find({ key: 'question-actions' })

  expect(actions?.props.flexWrap).toBe('wrap')
  expect(actions?.props.rowGap).toBe(1)
  expect(actions?.props.columnGap).toBe(1)
  expect(await ui.find({ key: 'discuss' })).toBeDefined()
  expect(await ui.find({ key: 'next' })).toBeDefined()
})

test('questions come one at a time with both sides, and a ruling is passed on', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), resolve: 'deploy-target: took the new claim\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^2 left$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploy target$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploys go to the staging cluster first\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Deploys go straight to production\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /guessed from a session/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /The database cutover/ })).toBeUndefined()
    expect((await ui.find({ key: 'accept-deploy-target' }))?.props.label).toBe('Take the suggestion')
    expect((await ui.find({ key: 'keep-deploy-target' }))?.props.label).toBe('Keep the memory')

    await ui.press({ key: 'next' })

    expect((await ui.find({ key: 'next' }))?.props.label).toBe('Skip')
    expect(await ui.find({ type: 'Text', text: /^Cutover status$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^looks dated$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^The database cutover is planned for 12 August\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^The cutover date has passed$/ })).toBeDefined()
    expect((await ui.find({ type: 'Text', text: /^Kept out of sessions until you rule\.$/ }))?.props.color).toBe('warning')
    expect((await ui.find({ key: 'accept-cutover-status' }))?.props.label).toBe('Retire the memory')
    expect((await ui.find({ key: 'keep-cutover-status' }))?.props.label).toBe('Keep the memory')
    expect(await ui.find({ type: 'Text', text: /Deploys go straight to production/ })).toBeUndefined()

    await ui.press({ key: 'keep-cutover-status' })
    await ui.press({ key: 'next' })
    await ui.press({ key: 'accept-deploy-target' })

    expect(calls).toContainEqual(['dream', 'resolve', 'cutover-status', '--keep'])
    expect(calls).toContainEqual(['dream', 'resolve', 'deploy-target', '--accept'])
    await ui.unmount()
  }
})

test('questions waiting in another project are named, and the pane can go there and come back', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), resolve: 'deploy-target: accepted\n', close: 'ci-cache: closed\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^site: 3 waiting$/ })).toBeDefined()
    expect(await ui.find({ key: 'home-project' })).toBeUndefined()
    calls.length = 0

    await ui.press({ key: 'elsewhere-0' })
    await ui.press({ key: 'accept-deploy-target' })
    await ui.press({ key: 'close-ci-cache' })

    expect(calls).toContainEqual(['dream', 'status', '--project', '/work/site'])
    expect(calls).toContainEqual(['dream', 'resolve', '--project', '/work/site', 'deploy-target', '--accept'])
    expect(calls).toContainEqual(['dream', 'close', '--project', '/work/site', '--', 'ci-cache'])
    calls.length = 0

    await ui.press({ key: 'home-project' })

    expect(calls).toEqual([['dream', 'status']])
    expect(await ui.find({ key: 'home-project' })).toBeUndefined()
    await ui.unmount()
  }
})

test('on the first screen a long statement is cut at a word, and shown whole behind the button', async ($, on) => {
  mock.clock(on, { now: NOW })
  const long =
    'The archive keeps what the person typed and what the assistant wrote word for word, and drops what tools ' +
    'printed, because tool output is most of a transcript and none of what a later session needs to recall.'
  const memories = [{ slot: 'archive-keeps-words', statement: long, from: 'human', said_at: '2026-10-05T09:00:00+00:00' }]
  commandLine(on, { status: JSON.stringify({ ...STATUS, memories }) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    const cut = (await ui.find({ type: 'Text', text: /^The archive keeps/ }))?.text ?? ''
    expect(cut).toMatch(/[a-z]…$/)
    expect(cut.length).toBeLessThanOrEqual(81)
    expect(long.startsWith(cut.slice(0, -1))).toBe(true)

    await ui.press({ key: 'show-memories' })

    expect((await ui.find({ type: 'Text', text: /^The archive keeps/ }))?.text).toBe(long)
    await ui.press({ key: 'show-home' })
    await ui.unmount()
  }
})

test('while a ruling is being saved the pane says so and offers no second press, then says what was done', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const calls: string[][] = []
  let release: () => void = () => {}
  const held = new Promise<void>(resolve => {
    release = resolve
  })

  on('process.run', async (_$, e) => {
    calls.push([...e.argv])

    if (e.argv[1] === 'resolve') {
      await held
    }

    const stdout = e.argv[1] === 'status' ? JSON.stringify(STATUS) : 'deploy-target: accepted\n'

    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'refresh' })

  const pressed = ui.press({ key: 'accept-deploy-target' })
  await clock.settle()

  expect(await ui.find({ type: 'Text', text: /^Saving: take the suggestion for Deploy target…$/ })).toBeDefined()
  expect(await ui.find({ key: 'accept-deploy-target' })).toBeUndefined()
  expect(await ui.find({ key: 'keep-deploy-target' })).toBeUndefined()

  release()
  await pressed

  expect(await ui.find({ type: 'Text', text: /^Done: took the suggestion for Deploy target$/ })).toBeDefined()
  expect(await ui.find({ key: 'accept-deploy-target' })).toBeDefined()
  expect(calls.filter(call => call[1] === 'resolve')).toHaveLength(1)
  await ui.unmount()
})

test('Refresh brings the buttons back if a change never finishes saving', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  let release: () => void = () => {}
  const held = new Promise<void>(resolve => {
    release = resolve
  })

  on('process.run', async (_$, e) => {
    if (e.argv[1] === 'resolve') {
      await held
    }

    const stdout = e.argv[1] === 'status' ? JSON.stringify(STATUS) : ''

    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'refresh' })
  const stuck = ui.press({ key: 'accept-deploy-target' })
  await clock.settle()
  expect(await ui.find({ key: 'accept-deploy-target' })).toBeUndefined()

  await ui.press({ key: 'refresh' })

  expect(await ui.find({ key: 'accept-deploy-target' })).toBeDefined()
  release()
  await stuck
  await ui.unmount()
})

test('two presses of the same button at once make the change once', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const calls: string[][] = []
  let release: () => void = () => {}
  const held = new Promise<void>(resolve => {
    release = resolve
  })

  on('process.run', async (_$, e) => {
    calls.push([...e.argv])

    if (e.argv[1] === 'close') {
      await held
    }

    const stdout = e.argv[1] === 'status' ? JSON.stringify(STATUS) : 'ci-cache: closed\n'

    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'refresh' })

  const first = ui.press({ key: 'close-ci-cache' })
  await clock.settle()
  const second = ui.press({ key: 'close-ci-cache' })
  await clock.settle()
  release()
  await Promise.all([first, second])

  expect(calls.filter(call => call[1] === 'close')).toHaveLength(1)
  expect(await ui.find({ type: 'Text', text: /^Done: closed Ci cache$/ })).toBeDefined()
  await ui.unmount()
})

test('a change that could not be saved is reported, not passed over', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'refresh' })
  await ui.press({ key: 'keep-deploy-target' })

  expect(await ui.find({ type: 'Text', text: /^Not saved: keep Deploy target\. Another dream command may be running\.$/ })).toBeDefined()
  await ui.unmount()
})

test('several findings on one memory are one question that lists them all', async ($, on) => {
  mock.clock(on, { now: NOW })
  const waiting = [
    {
      slot: 'cutover-status',
      holds: 'The database cutover is planned for 12 August.',
      suggests: null,
      reasons: ['it looks dated: the cutover is over', 'it repeats `cutover-done`: both give the date'],
      from: null,
      withheld: false,
      evidence: null,
      file: '/home/me/.claude/projects/-work-shop/memory/cutover-status.md',
    },
  ]
  commandLine(on, { status: JSON.stringify({ ...STATUS, waiting }) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^1 left$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^2 findings$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^It looks dated: the cutover is over$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^It repeats cutover-done: both give the date$/ })).toBeDefined()
    expect(await ui.find({ key: 'next' })).toBeUndefined()
    await ui.unmount()
  }
})

test('a question can be put in the prompt box to be talked through, with what the model needs to act on it', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify(STATUS) })
  const drafts: { text: string; mode: string }[] = []
  on('prompt.fill', async (_$, e) => {
    drafts.push({ text: e.text, mode: e.mode })

    return { isFilled: true, text: e.text, cursor: e.text.length }
  })

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'refresh' })
  await ui.press({ key: 'next' })
  await ui.press({ key: 'discuss' })

  expect(drafts).toEqual([
    {
      mode: 'append',
      text:
        'Help me settle a memory that is in question.\n' +
        'Memory: cutover-status, in /work/shop\n' +
        'It says: The database cutover is planned for 12 August.\n' +
        'In question because: it looks dated: the cutover date has passed\n' +
        'File: /home/me/.claude/projects/-work-shop/memory/cutover-status.md\n',
    },
  ])
  expect(await ui.find({ type: 'Text', text: /^Done: added Cutover status to your message$/ })).toBeDefined()
  await ui.unmount()
})

test('the model applies the ruling the person reached, and only the kind the question allows', async ($, on) => {
  const calls = commandLine(on, { status: JSON.stringify(STATUS), resolve: 'cutover-status: accepted\n' })

  const retired = await $.tool.call({
    tool: 'mcp__remcycle__settle_memory',
    slot: 'cutover-status',
    ruling: 'retire',
    project: '/work/shop',
  })
  const mismatched = await $.tool.call({
    tool: 'mcp__remcycle__settle_memory',
    slot: 'deploy-target',
    ruling: 'retire',
    project: '/work/shop',
  })
  const unknown = await $.tool.call({
    tool: 'mcp__remcycle__settle_memory',
    slot: 'no-such-memory',
    ruling: 'keep',
    project: '/work/shop',
  })

  expect(retired.result).toBe('cutover-status: accepted\n')
  expect(mismatched.result).toMatch(/has a suggestion waiting.*take.*keep/)
  expect(unknown.result).toMatch(/no question is waiting on no-such-memory/i)
  expect(calls.filter(call => call[1] === 'resolve')).toEqual([
    ['dream', 'resolve', '--project', '/work/shop', 'cutover-status', '--accept'],
  ])
})

test('on the desktop a click reported as the ring moving onto a button presses it, once', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), close: 'ci-cache: closed\n' })
  on('ui.focus', async () => ({}))
  const move = { component: 'Pane', requestId: 'remcycle', plugin: 'remcycle', origin: { kind: 'person' } } as const
  const closes = () => calls.filter(call => call[1] === 'close').length

  // The pane has no focus: the click arrives as the ring moving, and no press.
  const idle = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await idle.press({ key: 'refresh' })
  calls.length = 0
  await $.ui.focus({ ...move, element: 'close-ci-cache' })
  await clock.settle()
  expect(closes()).toBe(1)

  // The surface may send the press for that same click as well.
  await idle.press({ key: 'close-ci-cache' })
  expect(closes()).toBe(1)
  await idle.unmount()

  // The pane has focus but the button that held the ring is gone: again the ring moves and nothing is pressed.
  const held = await $.ui.mount({ ...PANE, props: { ...PANE.props, isFocused: true }, surface: 'desktop' })
  await clock.advance(5_000)
  calls.length = 0
  await $.ui.focus({ ...move, element: 'close-ci-cache' })
  await clock.settle()
  expect(closes()).toBe(1)

  // A press first and its ring move after are one click too.
  await clock.advance(5_000)
  calls.length = 0
  await held.press({ key: 'close-ci-cache' })
  await $.ui.focus({ ...move, element: 'close-ci-cache' })
  await clock.settle()
  expect(closes()).toBe(1)
  await held.unmount()
})

test('two presses in quick succession are two presses', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), close: 'ci-cache: closed\n' })

  const ui = await $.ui.mount({ ...PANE, props: { ...PANE.props, isFocused: true }, surface: 'desktop' })
  await ui.press({ key: 'refresh' })
  await ui.press({ key: 'close-ci-cache' })
  await ui.press({ key: 'close-ci-cache' })

  expect(calls.filter(call => call[1] === 'close')).toHaveLength(2)
  await ui.unmount()
})

test('in the terminal a ring move is only a ring move', async ($, on) => {
  const clock = mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), close: 'ci-cache: closed\n' })
  on('ui.focus', async () => ({}))

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'refresh' })
  await $.ui.focus({
    component: 'Pane',
    requestId: 'remcycle',
    plugin: 'remcycle',
    element: 'close-ci-cache',
    origin: { kind: 'person' },
  })
  await clock.settle()

  expect(calls.filter(call => call[1] === 'close')).toHaveLength(0)
  await ui.unmount()
})

test('with nothing waiting the pane says so and offers no question', async ($, on) => {
  mock.clock(on, { now: NOW })
  commandLine(on, { status: JSON.stringify({ ...STATUS, waiting: [] }) })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^Nothing needs you in this project\.$/ })).toBeDefined()
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

    expect(await ui.find({ type: 'Text', text: /^2 left$/ })).toBeDefined()
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

    expect(calls).toContainEqual(['dream', 'close', '--', 'ci-cache'])
    await ui.unmount()
  }
})

test('a thread closed lately shows who closed it and why, and can be reopened', async ($, on) => {
  mock.clock(on, { now: NOW })
  const calls = commandLine(on, { status: JSON.stringify(STATUS), reopen: 'lint-rules: open again\n' })

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    await ui.press({ key: 'refresh' })

    expect(await ui.find({ type: 'Text', text: /^Closed lately$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^Lint rules$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^A session closed it: Agreed on the ruff defaults\.$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^3h$/ })).toBeDefined()

    await ui.press({ key: 'reopen-lint-rules' })

    expect(calls).toContainEqual(['dream', 'reopen', '--', 'lint-rules'])
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
