import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Status } from '../types'

const PANE = 'remcycle'
const RECALL = 'mcp__remcycle__recall'
const MEMORY_FILE = /\/memory\/[^/]+\.md$/

const status = atom({ plugin: 'remcycle', key: 'status' } as const, null)

/** Runs remcycle's command line and returns what it printed, or null if it failed. */
async function dream($: EngineInterface, args: string[], timeoutMs = 30_000): Promise<string | null> {
  const ran = await $.process.run(['dream', ...args], { timeoutMs })

  return ran.exitCode === 0 ? ran.stdout : null
}

async function refresh($: EngineInterface): Promise<void> {
  const out = await dream($, ['status'])

  if (out !== null) {
    const now: Status = JSON.parse(out)
    await update($, status, () => now)
  }
}

async function rule($: EngineInterface, slot: string, isAccepted: boolean): Promise<void> {
  await dream($, ['resolve', slot, isAccepted ? '--accept' : '--keep'])
  await refresh($)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.tool.register({
      name: 'recall',
      description:
        'Search the archive of past Claude Code sessions for what was said or decided. ' +
        'Give `query` to get the best-matching turns, each labelled session#turn. ' +
        'Give `session` with `first` and `last` to read those turns back word for word. ' +
        'Searches this project unless `everywhere` is true.',
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Words to search for' },
          everywhere: { type: 'boolean', description: 'Search every project' },
          reports: { type: 'boolean', description: "Also search subagents' final reports" },
          session: { type: 'string', description: 'A session id, or the start of one, to read back' },
          first: { type: 'integer', description: 'First turn to read back' },
          last: { type: 'integer', description: 'Last turn to read back' },
        },
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

    const given: { everywhere: string[]; open_threads: string[] } = JSON.parse(out)
    const blocks = [...e.blocks]

    if (given.everywhere.length > 0) {
      blocks.push({
        name: 'remcycleEverywhere',
        text:
          'What this person has said applies to all their work:\n' +
          given.everywhere.map(line => `- ${line}`).join('\n'),
      })
    }

    if (given.open_threads.length > 0) {
      blocks.push({
        name: 'remcycleOpenThreads',
        text:
          'Left open by earlier sessions in this project:\n' +
          given.open_threads.map(line => `- ${line}`).join('\n'),
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
            '--',
            ...String(asked.query ?? '')
              .split(/\s+/)
              .filter(word => word !== ''),
          ]
    const out = await dream($, args)

    return { result: out ?? 'remcycle could not be reached.' }
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
    const now = await read($, status)

    if (now === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>Nothing from the dream yet. Run `dream run` first.</Text>
          <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
        </Box>
      )
    }

    const room = Math.max(3, (e.viewport?.rows ?? 24) - 10 - now.waiting.length * 3)

    return (
      <Box flexDirection="column">
        <Text>
          {now.entries} in use, {now.withheld} withheld
        </Text>
        {now.waiting.length > 0 && <Text>Waiting for your ruling</Text>}
        {now.waiting.map(item => (
          <Box flexDirection="column">
            <Text>
              {item.slot}
              {item.withheld ? ' (withheld)' : ''}: a session suggests ({item.from}) {item.suggests}
            </Text>
            <Text dimColor>{item.evidence}</Text>
            <Box>
              <Button key={`accept-${item.slot}`} label="Take it" onPress={() => rule($, item.slot, true)} />
              <Button key={`keep-${item.slot}`} label="Keep mine" onPress={() => rule($, item.slot, false)} />
            </Box>
          </Box>
        ))}
        {now.open_threads.length > 0 && <Text>Left open</Text>}
        {now.open_threads.map(line => (
          <Text dimColor>{line}</Text>
        ))}
        {now.learned.length > 0 && <Text>Learned by the dream</Text>}
        {now.learned.slice(-room).map(item => (
          <Text dimColor>
            {item.slot} ({item.from}): {item.statement}
          </Text>
        ))}
        <Button key="refresh" label="Refresh" onPress={() => refresh($)} />
      </Box>
    )
  })
}
