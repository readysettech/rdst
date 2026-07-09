import { useCallback, useEffect, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Text } from "@rs/ui-new/text";
import { Alert } from "@rs/ui-new/alert";
import { Icon } from "@rs/ui-new/icon";
import { Scrollable } from "@rs/ui-new/scrollable";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import { useOnboarding } from "../../lib/useOnboarding";
import { useConfigure } from "../../lib/useConfigure";
import type { OnboardingStep } from "../../types/onboarding";
import { WelcomeStep } from "./WelcomeStep";
import { TargetsStep } from "./TargetsStep";
import { ValidateStep } from "./ValidateStep";
import { CompleteStep } from "./CompleteStep";

const steps: Array<{
  id: OnboardingStep;
  label: string;
  description: string;
  icon: "sparkles" | "database" | "tick-double" | "play";
}> = [
  { id: "welcome", label: "Welcome", description: "Get started", icon: "sparkles" },
  { id: "targets", label: "Targets", description: "Connect database", icon: "database" },
  { id: "validate", label: "Validate", description: "Test connections", icon: "tick-double" },
  { id: "complete", label: "Complete", description: "Finish setup", icon: "play" },
];

export function OnboardingWizard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const {
    step,
    setStep,
    validationResults,
    error: onboardingError,
    loading: onboardingLoading,
    checkStatus,
    runValidation,
    completeInit,
  } = useOnboarding();

  const {
    listTargets,
    getTarget,
    addTarget,
    updateTarget,
    removeTarget,
    setDefaultTarget,
    testConnection,
    state: configureState,
    targets,
    defaultTarget,
    connectionTestResult,
    error: configureError,
    loading: configureLoading,
  } = useConfigure();

  const currentIndex = steps.findIndex((item) => item.id === step);
  const canGoBack = currentIndex > 0;
  const error = onboardingError || configureError;
  const loading = onboardingLoading || configureLoading;

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  // Allow users to run onboarding anytime - don't redirect if already initialized

  useEffect(() => {
    if (step === "targets") {
      listTargets();
    }
  }, [step, listTargets]);

  const handleNext = () => {
    const nextIndex = Math.min(currentIndex + 1, steps.length - 1);
    setStep(steps[nextIndex].id);
  };

  const handleBack = () => {
    if (!canGoBack) return;
    const prevIndex = Math.max(currentIndex - 1, 0);
    setStep(steps[prevIndex].id);
  };

  const targetNames = useMemo(() => targets.map((target) => target.name), [targets]);
  const handleRunValidation = useCallback(() => {
    void runValidation(targetNames);
  }, [runValidation, targetNames]);

  const handleComplete = async () => {
    const ok = await completeInit();
    if (ok) {
      queryClient.setQueryData(
        ["init-status"],
        (previous: { initialized?: boolean } | undefined) =>
          previous ? { ...previous, initialized: true } : previous
      );
      queryClient.invalidateQueries({ queryKey: ["init-status"] });
      navigate({ to: "/" });
    }
  };

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 h-dvh bg-surface-layout-2">
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-br from-surface-primary-soft/5 via-transparent to-surface-info-soft/5" />
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-gradient-radial from-surface-primary-soft/10 to-transparent blur-3xl" />

      <Scrollable className="relative">
        <div className="max-w-4xl mx-auto w-full px-6 py-10">
          {/* Header */}
          <m.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="mb-10"
          >
            <HStack className="gap-4 items-center mb-4">
              <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center shadow-lg shadow-surface-primary-soft/20">
                <Icon
                  name="speedometer"
                  label="RDST"
                  className="w-7 h-7 text-content-primary-soft"
                />
              </div>
              <VStack className="gap-0 items-start">
                <Text level="headline-1" className="text-content-layout-1">
                  Welcome to RDST
                </Text>
                <Text level="body-medium" className="text-content-layout-3">
                  Let&apos;s get you set up in just a few steps
                </Text>
              </VStack>
            </HStack>
          </m.div>

          {/* Step Indicator */}
          <m.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="mb-8"
          >
            <div className="flex items-center gap-2">
              {steps.map((item, index) => {
                const isActive = index === currentIndex;
                const isComplete = index < currentIndex;

                return (
                  <div key={item.id} className="flex items-center gap-2 flex-1">
                    <m.div
                      initial={false}
                      animate={{
                        scale: isActive ? 1 : 0.95,
                        opacity: isActive ? 1 : isComplete ? 0.9 : 0.5,
                      }}
                      className={`flex items-center gap-3 px-4 py-3 rounded-xl border transition-all duration-300 flex-1 ${
                        isActive
                          ? "bg-surface-layout-1 border-border-primary-soft shadow-lg shadow-surface-primary-soft/10"
                          : isComplete
                            ? "bg-surface-positive-soft/10 border-border-positive-soft/30"
                            : "bg-surface-layout-1/50 border-border-layout-1"
                      }`}
                    >
                      <div
                        className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-all duration-300 ${
                          isComplete
                            ? "bg-surface-positive-soft text-content-positive-soft"
                            : isActive
                              ? "bg-surface-primary-soft text-content-primary-soft"
                              : "bg-surface-layout-2 text-content-layout-3"
                        }`}
                      >
                        {isComplete ? (
                          <Icon name="tick" label="Complete" className="w-4 h-4" />
                        ) : (
                          <Icon name={item.icon} label={item.label} className="w-4 h-4" />
                        )}
                      </div>
                      <VStack className="gap-0 items-start min-w-0">
                        <Text
                          level="label-small"
                          className={
                            isActive
                              ? "text-content-layout-1"
                              : isComplete
                                ? "text-content-positive-soft"
                                : "text-content-layout-3"
                          }
                        >
                          {item.label}
                        </Text>
                        <Text level="caption" className="text-content-layout-3 truncate">
                          {item.description}
                        </Text>
                      </VStack>
                    </m.div>

                    {index < steps.length - 1 && (
                      <div
                        className={`w-6 h-0.5 rounded-full transition-colors duration-300 shrink-0 ${
                          index < currentIndex ? "bg-surface-positive-soft" : "bg-border-layout-1"
                        }`}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </m.div>

          {/* Progress bar */}
          <m.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="mb-8"
          >
            <div className="h-1 bg-surface-layout-1 rounded-full overflow-hidden">
              <m.div
                className="h-full bg-gradient-to-r from-surface-primary-soft to-surface-info-soft rounded-full"
                initial={{ width: "0%" }}
                animate={{ width: `${((currentIndex + 1) / steps.length) * 100}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
              />
            </div>
            <div className="flex justify-between mt-2">
              <Text level="caption" className="text-content-layout-3">
                Step {currentIndex + 1} of {steps.length}
              </Text>
              <Text level="caption" className="text-content-layout-3">
                {Math.round(((currentIndex + 1) / steps.length) * 100)}% complete
              </Text>
            </div>
          </m.div>

          {/* Error Display */}
          <AnimatePresence>
            {error && (
              <m.div
                initial={{ opacity: 0, y: -10, height: 0 }}
                animate={{ opacity: 1, y: 0, height: "auto" }}
                exit={{ opacity: 0, y: -10, height: 0 }}
                className="mb-6 overflow-hidden"
              >
                <Alert variant="negative" modifier="outline" label={`Error: ${error}`} />
              </m.div>
            )}
          </AnimatePresence>

          {/* Step Content */}
          <AnimatePresence mode="wait">
            <m.div
              key={step}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.3 }}
            >
              {step === "welcome" && <WelcomeStep onNext={handleNext} isLoading={loading} />}

              {step === "targets" && (
                <TargetsStep
                  targets={targets}
                  defaultTarget={defaultTarget}
                  onGetTarget={getTarget}
                  onAddTarget={addTarget}
                  onUpdateTarget={updateTarget}
                  onRemoveTarget={removeTarget}
                  onSetDefault={setDefaultTarget}
                  onTestConnection={testConnection}
                  onNext={handleNext}
                  onBack={handleBack}
                  connectionTestResult={connectionTestResult}
                  isLoading={loading}
                  state={configureState}
                />
              )}

              {step === "validate" && (
                <ValidateStep
                  targetNames={targetNames}
                  results={validationResults}
                  onRun={handleRunValidation}
                  onNext={handleNext}
                  onBack={handleBack}
                  isLoading={loading}
                />
              )}

              {step === "complete" && (
                <CompleteStep
                  defaultTarget={defaultTarget}
                  onComplete={handleComplete}
                  onBack={handleBack}
                  isLoading={loading}
                />
              )}
            </m.div>
          </AnimatePresence>
        </div>
      </Scrollable>
    </div>
  );
}
