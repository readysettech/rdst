import { Button } from "@rs/ui-new/button";
import { BaseInputSwitch } from "@rs/ui-new/base-input-switch";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@rs/ui-new/tooltip";
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
            {/* Fast mode toggle */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div>
                    <HStack className="gap-3 items-center">
                      <BaseInputSwitch
                        name="fast-mode"
                        checked={fast}
                        onCheckedChange={onFastChange}
                        disabled={disabled}
                      />
                      <VStack className="gap-0 items-start">
                        <HStack className="gap-1.5 items-center">
                          <Icon
                            name="speedometer"
                            label="Fast mode"
                            className="w-3.5 h-3.5 text-content-layout-2"
                          />
                          <Text
                            level="label-small"
                            className="text-content-layout-2"
                          >
                            Fast mode
                          </Text>
                        </HStack>
                        <Text level="caption" className="text-content-layout-3">
                          Skip EXPLAIN ANALYZE
                        </Text>
                      </VStack>
                    </HStack>
                  </div>
                </TooltipTrigger>
                <TooltipContent label="Fast mode skips running EXPLAIN ANALYZE on the database, providing quicker but less detailed results." />
              </Tooltip>
            </TooltipProvider>

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
