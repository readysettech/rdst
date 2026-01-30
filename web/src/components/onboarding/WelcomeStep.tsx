import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Card } from "@rs/ui-new/card";
import { m } from "@rs/ui-new/motion";

interface WelcomeStepProps {
  onNext: () => void;
  isLoading?: boolean;
}

const features = [
  {
    icon: "database" as const,
    title: "Connect Databases",
    description: "Add PostgreSQL or MySQL targets for query analysis",
    bgColor: "bg-surface-info-soft",
    iconColor: "text-content-info-soft",
  },
  {
    icon: "tick-double" as const,
    title: "Validate Access",
    description: "Test connectivity and verify permissions automatically",
    bgColor: "bg-surface-positive-soft",
    iconColor: "text-content-positive-soft",
  },
  {
    icon: "sparkles" as const,
    title: "AI-Powered Analysis",
    description: "Get intelligent suggestions for query optimization",
    bgColor: "bg-surface-primary-soft",
    iconColor: "text-content-primary-soft",
  },
  {
    icon: "speedometer" as const,
    title: "Performance Insights",
    description: "Understand query execution plans and bottlenecks",
    bgColor: "bg-surface-warning-soft",
    iconColor: "text-content-warning-soft",
  },
];

export function WelcomeStep({ onNext, isLoading }: WelcomeStepProps) {
  return (
    <Card>
      <Card.Content className="p-8 space-y-8">
        {/* Hero Section */}
        <div className="text-center space-y-4">
          <m.div
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.5 }}
            className="w-20 h-20 mx-auto rounded-3xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center shadow-xl shadow-surface-primary-soft/20"
          >
            <Icon name="querypilot" label="RDST" className="w-10 h-10 text-content-primary-soft" />
          </m.div>
          <m.div
            initial={{ y: 10, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            <Text level="headline-2" className="text-content-layout-1">
              Welcome to RDST
            </Text>
            <Text level="body-medium" className="text-content-layout-3 mt-2 max-w-lg mx-auto">
              The Readyset Diagnostic Toolkit helps you analyze SQL queries, understand execution
              plans, and optimize database performance.
            </Text>
          </m.div>
        </div>

        {/* Features Grid */}
        <div className="grid gap-4 sm:grid-cols-2">
          {features.map((feature, index) => (
            <m.div
              key={feature.title}
              initial={{ y: 20, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ duration: 0.4, delay: 0.2 + index * 0.1 }}
            >
              <div className="group relative rounded-xl border border-border-layout-1 bg-surface-layout-2 p-5 hover:border-border-layout-2 transition-all duration-300 hover:shadow-lg">
                <div className="relative">
                  <HStack className="gap-4 items-start">
                    <div
                      className={`w-10 h-10 rounded-xl ${feature.bgColor} flex items-center justify-center shrink-0 group-hover:scale-110 transition-transform duration-300`}
                    >
                      <Icon
                        name={feature.icon}
                        label={feature.title}
                        className={`w-5 h-5 ${feature.iconColor}`}
                      />
                    </div>
                    <VStack className="gap-1 items-start">
                      <Text level="subtitle-2" className="text-content-layout-1">
                        {feature.title}
                      </Text>
                      <Text level="body-small" className="text-content-layout-3">
                        {feature.description}
                      </Text>
                    </VStack>
                  </HStack>
                </div>
              </div>
            </m.div>
          ))}
        </div>

        {/* What's Next */}
        <m.div
          initial={{ y: 10, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 0.4, delay: 0.6 }}
          className="rounded-xl bg-gradient-to-r from-surface-layout-2 to-surface-layout-1 border border-border-layout-1 p-5"
        >
          <HStack className="gap-4 items-center">
            <div className="w-10 h-10 rounded-xl bg-surface-primary-soft/20 flex items-center justify-center shrink-0">
              <Icon name="arrow-right" label="Next" className="w-5 h-5 text-content-primary-soft" />
            </div>
            <VStack className="gap-0.5 items-start flex-1">
              <Text level="subtitle-2" className="text-content-layout-1">
                What&apos;s next?
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                We&apos;ll help you connect a database target and validate your setup. This only
                takes a couple of minutes.
              </Text>
            </VStack>
          </HStack>
        </m.div>

        {/* Actions */}
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4, delay: 0.7 }}
          className="flex items-center justify-between flex-wrap gap-4 pt-2"
        >
          <Text level="caption" className="text-content-layout-3">
            You can re-run this wizard anytime at{" "}
            <span className="text-content-layout-2 font-mono">/onboarding</span>
          </Text>
          <Button
            variant="rising"
            label="Get Started"
            icon="arrow-right"
            iconPosition="right"
            onClick={onNext}
            disabled={isLoading}
          />
        </m.div>
      </Card.Content>
    </Card>
  );
}
