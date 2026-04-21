/**
 * List component for displaying configured database targets
 */

import { Button } from "@rs/ui-new/button";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { Tag } from "@rs/ui-new/tag";
import { Card } from "@rs/ui-new/card";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Show } from "@rs/ui-new/show";
import { m } from "@rs/ui-new/motion";
import type { ConfigureTarget } from "../../types/configure";

interface ConfigureTargetListProps {
  targets: ConfigureTarget[];
  onEdit?: (target: ConfigureTarget) => void;
  onTest?: (targetName: string) => void;
  onDelete?: (targetName: string) => void;
  onSetDefault?: (targetName: string) => void;
  isLoading?: boolean;
}

export function ConfigureTargetList({
  targets,
  onEdit,
  onTest,
  onDelete,
  onSetDefault,
  isLoading,
}: ConfigureTargetListProps) {
  if (targets.length === 0) {
    return (
      <Card className="w-full">
        <Card.Content className="py-16">
          <VStack className="gap-4 items-center">
            <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
              <Icon name="database" label="No targets" className="w-7 h-7 text-content-layout-3" />
            </div>
            <VStack className="gap-1 items-center">
              <Text level="headline-5" className="text-content-layout-2">
                No database targets configured
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Add a target to get started with RDST
              </Text>
            </VStack>
          </VStack>
        </Card.Content>
      </Card>
    );
  }

  return (
    <Card className="w-full overflow-hidden">
      <Card.Header>
        <HStack className="gap-2 items-center">
          <Icon name="database" label="Targets" className="w-4 h-4 text-content-layout-3" />
          <Text level="label-medium" className="text-content-layout-1">
            Configured Targets
          </Text>
          <Tag
            size="small"
            variant="informative"
            modifier="ghost"
            label={String(targets.length)}
          />
        </HStack>
      </Card.Header>
      <Card.Content className="p-0">
        <div className="divide-y divide-border-layout-1">
          {targets.map((target, index) => (
            <m.div
              key={target.name}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              className="px-5 py-4 hover:bg-surface-layout-2/30 transition-colors"
            >
              <div className="flex items-start justify-between gap-4">
                <HStack className="gap-4 items-start flex-1">
                  <div className="w-10 h-10 rounded-xl bg-surface-layout-2 flex items-center justify-center shrink-0">
                    <Icon
                      name="database"
                      label={target.engine ?? ''}
                      className="w-5 h-5 text-content-layout-2"
                    />
                  </div>
                  <VStack className="gap-2 items-start flex-1">
                    <HStack className="gap-2 items-center flex-wrap">
                      <Text level="label-medium" className="text-content-layout-1">
                        {target.name}
                      </Text>
                      <Show when={target.is_default}>
                        <Tag
                          size="small"
                          variant="positive"
                          modifier="solid"
                          label="Default"
                        />
                      </Show>
                      <Tag
                        size="small"
                        variant="informative"
                        modifier="ghost"
                        label={target.engine ?? ''}
                      />
                    </HStack>
                    <HStack className="gap-4 items-center">
                      <HStack className="gap-1.5 items-center">
                        <Icon
                          name={target.has_password ? "tick" : "close"}
                          label="Password"
                          className={`w-3.5 h-3.5 ${target.has_password ? 'text-content-positive-soft' : 'text-content-negative-soft'}`}
                        />
                        <Text level="caption" className="text-content-layout-3">
                          {target.has_password ? "Password configured" : "No password"}
                        </Text>
                      </HStack>
                    </HStack>
                  </VStack>
                </HStack>

                <HStack className="gap-1 shrink-0">
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    icon="edit"
                    iconPosition="icon"
                    label="Edit"
                    onClick={() => onEdit?.(target)}
                    disabled={isLoading}
                  />
                  <Button
                    variant="primary"
                    modifier="ghost"
                    size="small"
                    icon="connect"
                    iconPosition="icon"
                    label="Test"
                    onClick={() => onTest?.(target.name)}
                    disabled={isLoading}
                  />
                  <Show when={!target.is_default}>
                    <Button
                      variant="primary"
                      modifier="ghost"
                      size="small"
                      icon="star"
                      iconPosition="icon"
                      label="Set Default"
                      onClick={() => onSetDefault?.(target.name)}
                      disabled={isLoading}
                    />
                  </Show>
                  <Button
                    variant="negative"
                    modifier="ghost"
                    size="small"
                    icon="trash"
                    iconPosition="icon"
                    label="Delete"
                    onClick={() => onDelete?.(target.name)}
                    disabled={isLoading}
                  />
                </HStack>
              </div>
            </m.div>
          ))}
        </div>
      </Card.Content>
    </Card>
  );
}
