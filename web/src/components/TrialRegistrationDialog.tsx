import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from "@rs/ui-new/modal";
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Alert } from "@rs/ui-new/alert";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Icon } from "@rs/ui-new/icon";
import { registerTrial, activateTrial } from "../lib/api";

type Step = "email" | "verify" | "success";
type RegisterMutationData =
  | { mode: "registered"; limitDisplay: string | null; emailTier: string | null }
  | { mode: "already-registered" };

type TrialMutationError = Error & {
  didYouMean?: string;
};

interface TrialRegistrationDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function TrialRegistrationDialog({
  isOpen,
  onClose,
  onSuccess,
}: TrialRegistrationDialogProps) {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [token, setToken] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const registerMutation = useMutation({
    mutationFn: async (registerEmail: string): Promise<RegisterMutationData> => {
      const result = await registerTrial(registerEmail);
      if (result.success) {
        return {
          mode: "registered",
          limitDisplay: result.limit_display ?? null,
          emailTier: result.email_tier ?? null,
        };
      }
      if (result.error_code === "ALREADY_REGISTERED") {
        return { mode: "already-registered" };
      }
      const error = new Error(result.detail ?? "Registration failed.") as TrialMutationError;
      error.didYouMean = result.did_you_mean ?? undefined;
      throw error;
    },
    onSuccess: () => {
      setStep("verify");
    },
  });
  const activateMutation = useMutation({
    mutationFn: ({
      token,
      email,
      emailTier,
    }: {
      token: string;
      email: string;
      emailTier?: string | null;
    }) =>
      activateTrial(token, email, emailTier ?? undefined).then((result) => {
        if (!result.success) {
          throw new Error(result.message ?? "Activation failed.");
        }
        return result;
      }),
    onSuccess: () => {
      setStep("success");
      onSuccess?.();
    },
  });
  const loading = registerMutation.isPending || activateMutation.isPending;
  const registerError =
    registerMutation.error instanceof Error ? registerMutation.error : null;
  const activateError =
    activateMutation.error instanceof Error ? activateMutation.error : null;
  const errorMessage = validationError ?? activateError?.message ?? registerError?.message ?? null;
  const didYouMean =
    registerError && "didYouMean" in registerError
      ? (registerError as TrialMutationError).didYouMean ?? null
      : null;
  const alreadyRegistered = registerMutation.data?.mode === "already-registered";
  const limitDisplay =
    registerMutation.data?.mode === "registered"
      ? registerMutation.data.limitDisplay
      : null;
  const emailTier =
    registerMutation.data?.mode === "registered"
      ? registerMutation.data.emailTier
      : null;

  const reset = () => {
    setStep("email");
    setEmail("");
    setToken("");
    setValidationError(null);
    registerMutation.reset();
    activateMutation.reset();
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleRegister = () => {
    if (!email || !email.includes("@")) {
      setValidationError("Please enter a valid email address.");
      return;
    }

    setValidationError(null);
    registerMutation.reset();
    activateMutation.reset();
    registerMutation.mutate(email);
  };

  const handleActivate = () => {
    if (!token || token.trim().length < 10) {
      setValidationError("Please paste a valid trial token (at least 10 characters).");
      return;
    }

    setValidationError(null);
    activateMutation.reset();
    activateMutation.mutate({
      token: token.trim(),
      email,
      emailTier,
    });
  };

  const handleDidYouMean = () => {
    if (didYouMean) {
      setEmail(didYouMean);
      setValidationError(null);
      registerMutation.reset();
    }
  };

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base" className="p-0 overflow-hidden">
          <ModalTitle className="sr-only">Start Free Trial</ModalTitle>
          <ModalDescription className="sr-only">
            Register for a free RDST trial to get AI analysis credits.
          </ModalDescription>

          {/* Header */}
          <div className="px-6 py-5 border-b border-border-layout-1 bg-surface-layout-2">
            <HStack className="gap-3 items-center">
              <div className="w-10 h-10 rounded-xl bg-surface-primary-soft flex items-center justify-center">
                <Icon name="sparkles" label="Trial" className="w-5 h-5 text-content-primary-soft" />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="headline-4" className="text-content-layout-1">
                  {step === "email" && "Start Free Trial"}
                  {step === "verify" && "Check Your Email"}
                  {step === "success" && "Trial Activated"}
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  {step === "email" && "Get free AI analysis credits — no credit card required."}
                  {step === "verify" && "Paste the trial token from the verification email."}
                  {step === "success" && "Your free trial is ready to use."}
                </Text>
              </VStack>
            </HStack>
          </div>

          {/* Content */}
          <div className="p-6 space-y-4">
            {errorMessage && <Alert variant="negative" modifier="outline" label={errorMessage} />}

            {step === "email" && (
              <>
                <div className="space-y-1">
                  <Text as="label" level="label-small" className="text-content-layout-2 block">
                    Email Address
                  </Text>
                  <BaseInputText
                    type="email"
                    name="trial-email"
                    autoComplete="email"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@company.com"
                    disabled={loading}
                    onKeyDown={(e) => e.key === "Enter" && handleRegister()}
                  />
                  <Text level="caption" className="text-content-layout-3">
                    Business emails get more credits. We only use this to send the trial token.
                  </Text>
                </div>
                {didYouMean && (
                  <button
                    type="button"
                    className="text-sm text-content-primary-soft hover:underline cursor-pointer"
                    onClick={handleDidYouMean}
                  >
                    Did you mean {didYouMean}?
                  </button>
                )}
              </>
            )}

            {step === "verify" && (
              <>
                {!alreadyRegistered && (
                  <div className="rounded-lg bg-surface-positive-soft/20 border border-border-positive-soft px-4 py-3">
                    <VStack className="gap-1 items-start">
                      <Text level="label-small" className="text-content-positive-soft">
                        Verification email sent to {email}
                      </Text>
                      {limitDisplay && (
                        <Text level="body-small" className="text-content-layout-2">
                          Your trial credit: {limitDisplay}
                          {emailTier === "business" ? " (business email)" : " (personal email)"}
                        </Text>
                      )}
                    </VStack>
                  </div>
                )}

                {alreadyRegistered && (
                  <Alert
                    variant="warning"
                    modifier="outline"
                    label="This email is already registered. Enter your trial token below."
                  />
                )}

                <div className="rounded-lg bg-surface-layout-2/60 border border-border-layout-1 px-4 py-3">
                  <VStack className="gap-1 items-start">
                    <Text level="label-small" className="text-content-layout-1">
                      Steps to get your token:
                    </Text>
                    <Text level="body-small" className="text-content-layout-3">
                      1. Check your email (including spam folder)
                    </Text>
                    <Text level="body-small" className="text-content-layout-3">
                      2. Click the verification link
                    </Text>
                    <Text level="body-small" className="text-content-layout-3">
                      3. Copy the trial token from the page
                    </Text>
                  </VStack>
                </div>

                <div className="space-y-1">
                  <Text as="label" level="label-small" className="text-content-layout-2 block">
                    Trial Token
                  </Text>
                  <BaseInputText
                    type="text"
                    name="trial-token"
                    autoComplete="off"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                    data-1p-ignore="true"
                    data-lpignore="true"
                    data-form-type="other"
                    style={{ WebkitTextSecurity: "disc" } as React.CSSProperties}
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="Paste your trial token here"
                    disabled={loading}
                    onKeyDown={(e) => e.key === "Enter" && handleActivate()}
                  />
                </div>
              </>
            )}

            {step === "success" && (
              <div className="py-4 flex flex-col items-center text-center gap-3">
                <div className="w-14 h-14 rounded-2xl bg-surface-positive-soft flex items-center justify-center">
                  <Icon
                    name="tick"
                    label="Success"
                    className="w-7 h-7 text-content-positive-soft"
                  />
                </div>
                <VStack className="gap-1 items-center">
                  <Text level="subtitle-2" className="text-content-layout-1">
                    Trial is active
                  </Text>
                  <Text level="body-small" className="text-content-layout-3">
                    You can now use AI analysis features. Your balance will appear in the sidebar.
                  </Text>
                </VStack>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="px-6 py-4 border-t border-border-layout-1 bg-surface-layout-2/40">
            <HStack className="justify-end gap-3 items-center w-full">
              {step !== "success" && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Cancel"
                  onClick={handleClose}
                  disabled={loading}
                />
              )}

              {step === "email" && (
                <Button
                  variant="rising"
                  label="Start Free Trial"
                  icon="arrow-right"
                  iconPosition="right"
                  onClick={handleRegister}
                  loading={loading}
                  disabled={!email || loading}
                />
              )}

              {step === "verify" && (
                <Button
                  variant="rising"
                  label="Activate"
                  icon="tick"
                  iconPosition="right"
                  onClick={handleActivate}
                  loading={loading}
                  disabled={!token || loading}
                />
              )}

              {step === "success" && (
                <Button variant="primary" label="Done" onClick={handleClose} />
              )}
            </HStack>
          </div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}
