import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import { m } from "@rs/ui-new/motion";

interface CompleteStepProps {
  defaultTarget: string | null;
  onComplete: () => void;
  onBack: () => void;
  isLoading?: boolean;
}

const nextSteps = [
  {
    icon: "querypilot" as const,
    title: "Analyze a Query",
    description: "Paste SQL on the home page to get execution plans and suggestions.",
  },
  {
    icon: "observe" as const,
    title: "Monitor Top Queries",
    description: "Find slow queries that need optimization.",
  },
  {
    icon: "sparkles" as const,
    title: "Ask AI for Help",
    description: "Get intelligent suggestions from Claude.",
  },
];

export function CompleteStep({ defaultTarget, onComplete, onBack, isLoading }: CompleteStepProps) {
  return (
    <Card>
      {/* Success Hero */}
      <div className="p-8 text-center border-b border-border-layout-1">
        <m.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 200, damping: 15 }}
          className="w-20 h-20 mx-auto rounded-3xl bg-surface-positive-soft flex items-center justify-center mb-5"
        >
          <m.div
            initial={{ scale: 0, rotate: -180 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ delay: 0.2, type: "spring", stiffness: 200 }}
          >
            <Icon name="tick-double" label="Complete" className="w-10 h-10 text-content-positive-soft" />
          </m.div>
        </m.div>

        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <Text level="headline-2" className="text-content-layout-1">
            You&apos;re All Set!
          </Text>
          <Text level="body-medium" className="text-content-layout-3 mt-2 max-w-md mx-auto">
            Your RDST setup is complete. Start analyzing queries and optimizing performance.
          </Text>
        </m.div>

        {defaultTarget && (
          <m.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-primary-soft/10 border border-border-primary-soft/30"
          >
            <Icon name="database" label="Target" className="w-4 h-4 text-content-primary-soft" />
            <Text level="label-small" className="text-content-primary-soft">
              Default: {defaultTarget}
            </Text>
          </m.div>
        )}
      </div>

      {/* Next Steps */}
      <div className="p-6">
        <Text level="subtitle-2" className="text-content-layout-2 mb-4">
          What you can do next
        </Text>
        <div className="space-y-3">
          {nextSteps.map((step, index) => (
            <m.div
              key={step.title}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.5 + index * 0.1 }}
              className="flex items-start gap-4 p-4 rounded-xl bg-surface-layout-2/50 border border-border-layout-1"
            >
              <div className="w-10 h-10 rounded-xl bg-surface-layout-3 flex items-center justify-center shrink-0">
                <Icon
                  name={step.icon}
                  label={step.title}
                  className="w-5 h-5 text-content-layout-2"
                />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="subtitle-2" className="text-content-layout-1">
                  {step.title}
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {step.description}
                </Text>
              </VStack>
            </m.div>
          ))}
        </div>

        {/* CLI Tip */}
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
          className="mt-4 p-4 rounded-xl bg-surface-layout-2/30 border border-border-layout-1"
        >
          <HStack className="gap-3 items-center">
            <Icon name="folder-file" label="CLI" className="w-5 h-5 text-content-layout-3" />
            <Text level="body-small" className="text-content-layout-3">
              <span className="text-content-layout-2">Pro tip:</span> Run{" "}
              <code className="px-1.5 py-0.5 rounded bg-surface-layout-3 text-content-layout-2 font-mono text-xs">
                rdst top
              </code>{" "}
              in your terminal to monitor slow queries.
            </Text>
          </HStack>
        </m.div>
      </div>

      {/* Footer with navigation */}
      <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
        <HStack className="justify-between w-full items-center">
          <Button
            variant="primary"
            modifier="ghost"
            icon="chevron-left"
            iconPosition="left"
            label="Back"
            onClick={onBack}
            disabled={isLoading}
          />
          <Button
            variant="rising"
            icon="play"
            iconPosition="right"
            label="Start Using RDST"
            onClick={onComplete}
            disabled={isLoading}
            loading={isLoading}
          />
        </HStack>
      </div>
    </Card>
  );
}
