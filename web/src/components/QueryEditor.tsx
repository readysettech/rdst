import { Button } from "@rs/ui-new/button";
import { BaseInputSwitch } from "@rs/ui-new/base-input-switch";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import { Popover, PopoverTrigger, PopoverContent } from "@rs/ui-new/popover";
import { useDisclosure } from "@rs/ui-new/use-disclosure";
import { SQLInput } from "./SQLInput";

interface QueryEditorProps {
  value: string;
  onChange: (v: string) => void;
  onAnalyze: () => void;
  disabled?: boolean;
  target?: string | null;
  fast?: boolean;
  onFastChange?: (fast: boolean) => void;
}

export function QueryEditor({
  value,
  onChange,
  onAnalyze,
  disabled,
  target,
  fast = false,
  onFastChange,
}: QueryEditorProps) {
  // Fast mode is deferred out of the default view: the one job here is
  // paste → Analyze, so the secondary toggle lives behind a quiet Options
  // disclosure rather than competing with the primary CTA. [VIS-016, USE-017]
  const [optionsOpen, setOptionsOpen] = useDisclosure({});
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        {/* Editor header */}
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="justify-between items-center">
            <HStack className="gap-2 items-center">
              <Icon
                name="querypilot"
                label="SQL Editor"
                className="w-4 h-4 text-content-layout-3"
              />
              <Text
                level="overline"
                className="text-content-layout-3 uppercase tracking-wider"
              >
                SQL Query
              </Text>
            </HStack>
          </HStack>
        </div>

        {/* Editor area */}
        <div className="p-4">
          <SQLInput
            value={value}
            onChange={onChange}
            onSubmit={disabled ? undefined : onAnalyze}
            target={target}
            disabled={disabled}
            showPrettify={!disabled}
            minHeight="10rem"
          />
        </div>

        {/* Footer with actions */}
        <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
          <HStack className="justify-between items-center">
            {/* Options disclosure — Fast mode lives here, pushed back so the
                primary Analyze CTA stays the only high-contrast control. */}
            <Popover open={optionsOpen} onOpenChange={setOptionsOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  disabled={disabled}
                  className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-content-layout-3 hover:text-content-layout-2 hover:bg-surface-layout-2 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Text as="span" level="label-small">
                    Options
                  </Text>
                  <Icon
                    name="chevron-down"
                    label="Options"
                    className={`w-3.5 h-3.5 transition-transform ${optionsOpen ? "rotate-180" : ""}`}
                  />
                </button>
              </PopoverTrigger>
              <PopoverContent
                side="top"
                align="start"
                variant="layout"
                className="w-80 flex-col p-0"
              >
                <div className="p-4">
                  <HStack className="gap-3 items-start">
                    <BaseInputSwitch
                      name="fast-mode"
                      checked={fast}
                      onCheckedChange={onFastChange}
                      disabled={disabled}
                    />
                    <VStack className="gap-0.5 items-start">
                      <Text level="label-small" className="text-content-layout-1">
                        Fast mode
                      </Text>
                      <Text level="caption" className="text-content-layout-3">
                        Skip EXPLAIN ANALYZE for a quicker, less detailed pass.
                      </Text>
                    </VStack>
                  </HStack>
                </div>
              </PopoverContent>
            </Popover>

            {/* Analyze button */}
            <Button
              onClick={onAnalyze}
              disabled={disabled || !value.trim()}
              loading={disabled}
              variant="rising"
              modifier="solid"
              label="Analyze Query"
              icon="sparkles"
              iconPosition="left"
            />
          </HStack>
        </div>
      </Card.Content>
    </Card>
  );
}
