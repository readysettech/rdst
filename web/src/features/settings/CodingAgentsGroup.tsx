import { CopyButton } from '@rs/ui-new/copy-button'
import { Text } from '@rs/ui-new/text'
import { useQuery } from '@tanstack/react-query'
import { fetchMcpConfig } from '../../lib/agentConnect'
import { isContainerRuntime } from '../../lib/desktop'
import { SettingsGroup, SettingsRow } from './SettingsGroup'

const MCP_CONFIG_KEY = ['agent-connect', 'mcp-config'] as const

/** RDST's MCP server for coding agents: its address and the Claude Code command. */
export function CodingAgentsGroup() {
  if (isContainerRuntime()) return <ServerCodingAgentsGroup />
  return <LocalCodingAgentsGroup />
}

/** On a server, agents need an agent key, which each database's page hands out. */
function ServerCodingAgentsGroup() {
  return (
    <SettingsGroup id="agents" title="Use RDST from your coding agent">
      <Text as="p" level="caption" className="text-content-layout-3">
        Open a database and use Connect Claude or Codex to RDST. It gives your
        agent this server's address and an agent key.
      </Text>
    </SettingsGroup>
  )
}

function LocalCodingAgentsGroup() {
  const config = useQuery({
    queryKey: MCP_CONFIG_KEY,
    queryFn: fetchMcpConfig,
    staleTime: Number.POSITIVE_INFINITY,
  })
  const command = config.data?.command

  return (
    <SettingsGroup id="agents" title="Use RDST from your coding agent">
      <SettingsRow
        title="MCP server"
        line={
          <span data-testid="agent-mcp-url" className="font-mono">
            {config.data?.mcpUrl ??
              (config.isError
                ? "RDST couldn't read its MCP address."
                : 'Loading...')}
          </span>
        }
      >
        {command && (
          <CopyButton text={command} label="Copy Claude Code command" />
        )}
      </SettingsRow>
      <Text as="p" level="caption" className="mt-1.5 text-content-layout-3">
        Want Claude Code to read what RDST finds and fix it with you? Run the
        command once in a terminal. Other agents use the same URL.
      </Text>
    </SettingsGroup>
  )
}
