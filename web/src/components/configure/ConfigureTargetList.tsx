/**
 * List of configured database connections.
 *
 * Each connection is an elevated row-card (surface-layout-2, lifting to
 * surface-rising-soft + shadow-small on hover/focus). The connection name is the
 * click-to-edit trigger; exactly one visible action (Test) sits beside the
 * status badges (Default + health) and the row's live connectivity; the
 * remaining actions (Edit / Set as default / Move to group / Delete) live behind
 * an always-visible overflow "⋯" menu. An unreachable connection keeps a notice
 * beneath its row until the next check clears it. [VIS-011/022/104/117,
 * USE-005/018/099]
 */

import { useState } from "react";
import { Button } from "@rs/ui-new/button";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { Tag } from "@rs/ui-new/tag";
import { Card } from "@rs/ui-new/card";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { Dropdown } from "@rs/ui-new/dropdown";
import { ConfirmDialog } from "@rs/ui-new/confirm-dialog";
import { m } from "@rs/ui-new/motion";
import type { ConfigureTarget } from "../../types/configure";
import type { FleetConnectivityEvent } from "../../types/fleet";
import type { TunnelStatus } from "../../lib/tunnels";
import {
  isUnreachable,
  TargetConnectivity,
  UnreachableNotice,
} from "./TargetConnectivity";

interface ConfigureTargetListProps {
  targets: ConfigureTarget[];
  onEdit?: (target: ConfigureTarget) => void;
  onTest?: (targetName: string) => void;
  onDelete?: (targetName: string) => void;
  onSetDefault?: (targetName: string) => void;
  onAdd?: () => void;
  /** Empty-state secondary action: open the discovery drawer. */
  onDiscover?: () => void;
  onMoveToGroup?: (target: ConfigureTarget) => void;
  isLoading?: boolean;
  /** Latest connectivity result per target name. */
  connectivity?: Record<string, FleetConnectivityEvent>;
  /** Names the connectivity check covers; others cannot be tested from here. */
  checkableTargets?: Set<string>;
  /** Name of the connection currently being tested (drives the inline spinner). */
  testingTargetName?: string | null;
  onSetPassword?: (target: ConfigureTarget) => void;
  onRetryConnection?: (target: ConfigureTarget) => Promise<boolean>;
  tunnelStatuses?: Record<string, TunnelStatus>;
  testingTunnelName?: string | null;
  onTestTunnel?: (targetName: string) => void;
}

/** `engine · host:port` — the quiet identity line under the name. */
function connectionMeta(target: ConfigureTarget): string {
  const hostPort = target.host
    ? `${target.host}${target.port != null ? `:${target.port}` : ""}`
    : "";
  return [target.engine, hostPort].filter(Boolean).join(" · ");
}

export function ConfigureTargetList({
  targets,
  onEdit,
  onTest,
  onDelete,
  onSetDefault,
  onAdd,
  onDiscover,
  onMoveToGroup,
  isLoading,
  connectivity,
  checkableTargets,
  testingTargetName,
  onSetPassword,
  onRetryConnection,
  tunnelStatuses,
  testingTunnelName,
  onTestTunnel,
}: ConfigureTargetListProps) {
  const [deleteTargetName, setDeleteTargetName] = useState<string | null>(null);

  if (targets.length === 0) {
    return (
      <Card className="w-full">
        <Card.Content className="py-16">
          <VStack className="gap-5 items-center text-center">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
              <Icon name="database" label="No connections" className="w-8 h-8 text-content-primary-soft" />
            </div>
            <VStack className="gap-1.5 items-center">
              <Text level="headline-5" className="text-content-layout-1">
                No databases connected yet
              </Text>
              <Text level="body-small" className="text-content-layout-3 max-w-xs">
                Add your first database so RDST has something to analyze. Paste a
                connection string to start.
              </Text>
            </VStack>
            <HStack className="gap-3 items-center flex-wrap justify-center">
              <Show when={!!onAdd}>
                <Button
                  variant="rising"
                  modifier="solid"
                  icon="add"
                  iconPosition="left"
                  label="Add your first connection"
                  onClick={onAdd}
                  disabled={isLoading}
                />
              </Show>
              {/* The zero-target lockout exempts Settings precisely because
                  discovery lives here, so the empty state has to offer it. */}
              <Show when={!!onDiscover}>
                <Button
                  variant="primary"
                  modifier="outline"
                  icon="search"
                  iconPosition="left"
                  label="Discover from your cloud provider"
                  onClick={onDiscover}
                  disabled={isLoading}
                />
              </Show>
            </HStack>
          </VStack>
        </Card.Content>
      </Card>
    );
  }

  return (
    <>
      <VStack className="gap-3 items-stretch w-full">
        {targets.map((target, index) => {
          const meta = connectionMeta(target);
          const isTesting = testingTargetName === target.name;
          const status = connectivity?.[target.name];
          const checkable = !checkableTargets || checkableTargets.has(target.name);
          const tunnel = tunnelStatuses?.[target.name];
          const tunnelActive = tunnel?.state === "active";

          return (
            <m.div
              key={target.name}
              data-testid="target-row"
              data-target-name={target.name}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              className="group rounded-2xl border border-border-layout-1 bg-surface-layout-2 px-5 py-4 transition-all duration-fast hover:bg-surface-rising-soft hover:shadow-small focus-within:bg-surface-rising-soft focus-within:shadow-small"
            >
              <div className="flex items-start justify-between gap-4">
                <VStack className="gap-2 items-start min-w-0 flex-1">
                  <HStack className="gap-2 items-center flex-wrap">
                    {/* Name = click-to-edit trigger [USE-018] */}
                    <button
                      type="button"
                      title="Edit connection"
                      aria-label={`Edit connection ${target.name}`}
                      onClick={() => onEdit?.(target)}
                      disabled={isLoading}
                      className="max-w-full text-left rounded-sm cursor-pointer hover:underline underline-offset-2 disabled:cursor-not-allowed disabled:no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-primary-soft"
                    >
                      <Text level="label-medium" className="text-content-layout-1">
                        {target.name}
                      </Text>
                    </button>

                    <Show when={target.is_default}>
                      {/* Default marker — the one place (besides Add/Save) the
                          brand accent is spent. [VIS-061, VIS-111] */}
                      <span className="inline-flex items-center gap-1 h-6 px-2 rounded-md text-label-extra-small text-content-rising-soft bg-surface-rising-soft">
                        <Icon name="star" label="" className="w-3 h-3" />
                        Default
                      </span>
                    </Show>
                    <Show when={!!target.ssh}>
                      <Tag
                        size="small"
                        variant={
                          tunnelActive
                            ? "positive"
                            : tunnel
                              ? "negative"
                              : "neutral"
                        }
                        modifier="ghost"
                        label={
                          tunnelActive
                            ? "Tunnel active"
                            : tunnel
                              ? "Tunnel down"
                              : "Tunnel closed"
                        }
                      />
                    </Show>
                  </HStack>

                  {/* The password chip opens the meta line in every row, so its
                      position never drifts with the length of the name above
                      it. Health reads as icon + text + color, never color
                      alone. [USE-005] */}
                  <HStack className="gap-2 min-w-0 w-full">
                    <span className="shrink-0">
                      {target.has_password ? (
                        <Tag
                          size="small"
                          variant="neutral"
                          modifier="outline"
                          label="Password stored"
                        />
                      ) : (
                        <Tag
                          size="small"
                          variant="warning"
                          modifier="ghost"
                          icon="alert"
                          iconPosition="left"
                          label="Password needed"
                        />
                      )}
                    </span>

                    <Show when={!!meta}>
                      <Text level="body-small" className="text-content-layout-2 truncate min-w-0">
                        {meta}
                      </Text>
                    </Show>
                  </HStack>
                </VStack>

                <HStack className="gap-2 items-center shrink-0">
                  <TargetConnectivity result={status} />

                  {/* The one visible per-row action [VIS-022] */}
                  <Button
                    variant="primary"
                    modifier="outline"
                    size="small"
                    icon="connect"
                    iconPosition="left"
                    label="Test"
                    title={
                      checkable
                        ? "Test connection"
                        : "Connectivity checks cover database targets only"
                    }
                    onClick={() => onTest?.(target.name)}
                    loading={isTesting}
                    disabled={isLoading || !checkable}
                  />

                  {/* Overflow — tertiary actions [VIS-114, USE-018] */}
                  <Dropdown>
                    <Dropdown.Trigger asChild>
                      <button
                        type="button"
                        title="More actions"
                        aria-label={`More actions for ${target.name}`}
                        disabled={isLoading}
                        className="flex items-center justify-center h-8 w-8 rounded-lg text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-1 transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Icon name="more" label="More actions" className="w-4 h-4" />
                      </button>
                    </Dropdown.Trigger>
                    <Dropdown.Content align="end">
                      <Dropdown.Item
                        leftIcon="edit"
                        label="Edit connection"
                        onClick={() => onEdit?.(target)}
                      />
                      <Show when={!target.is_default}>
                        <Dropdown.Item
                          leftIcon="star"
                          label="Set as default"
                          onClick={() => onSetDefault?.(target.name)}
                        />
                      </Show>
                      <Show when={!!onMoveToGroup}>
                        <Dropdown.Item
                          leftIcon="layers"
                          label="Move to group…"
                          onClick={() => onMoveToGroup?.(target)}
                        />
                      </Show>
                      <Show when={!!target.ssh && !!onTestTunnel}>
                        <Dropdown.Item
                          leftIcon="connect"
                          label={
                            testingTunnelName === target.name
                              ? "Testing tunnel…"
                              : "Test tunnel"
                          }
                          disabled={testingTunnelName === target.name}
                          onClick={() => onTestTunnel?.(target.name)}
                        />
                      </Show>
                      <Dropdown.Separator />
                      <Dropdown.Item
                        leftIcon="trash"
                        label="Delete…"
                        className="text-content-negative-soft hover:bg-surface-negative-soft hover:text-content-negative-soft focus:bg-surface-negative-soft focus:text-content-negative-soft"
                        onClick={() => setDeleteTargetName(target.name)}
                      />
                    </Dropdown.Content>
                  </Dropdown>
                </HStack>
              </div>

              {/* Unreachable stays stated next to the row it belongs to, with
                  the raw failure behind Details [USE-099] */}
              <Show when={isUnreachable(status)}>
                <div className="mt-3">
                  <UnreachableNotice
                    target={target}
                    result={status}
                    onSetPassword={() => onSetPassword?.(target)}
                    onRetry={onRetryConnection}
                  />
                </div>
              </Show>
            </m.div>
          );
        })}
      </VStack>

      {/* Styled destructive confirm (shared DS ConfirmDialog) [VIS-023] */}
      <ConfirmDialog
        isOpen={deleteTargetName !== null}
        onClose={() => setDeleteTargetName(null)}
        onConfirm={() => {
          if (deleteTargetName) {
            onDelete?.(deleteTargetName);
          }
          setDeleteTargetName(null);
        }}
        title={`Delete connection “${deleteTargetName ?? ""}”?`}
        description={`Delete the ${deleteTargetName ?? ""} connection from RDST.`}
        notice={{
          accent: "negative",
          message: `“${deleteTargetName ?? ""}” will be removed from RDST's connections.`,
        }}
        confirmLabel="Delete connection"
        confirmIcon="trash"
      />
    </>
  );
}
