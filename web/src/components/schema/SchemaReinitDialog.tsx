import { Button } from "@rs/ui-new/button";
import { InlineNotice } from "@rs/ui-new/error-state";
import { Modal, ModalContent, ModalContentContainer, ModalDescription, ModalTitle } from "@rs/ui-new/modal";
import { HStack, VStack } from "@rs/ui-new/stack";
import { Text } from "@rs/ui-new/text";

interface SchemaReinitDialogProps {
  isOpen: boolean;
  target: string;
  isLoading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * Destructive-confirmation guard for Schema "Re-init" (B4/T4).
 *
 * Re-init re-introspects the database and overwrites the semantic layer with a
 * fresh one — it does NOT merge annotations the way Refresh does, so every
 * human- and AI-authored annotation is permanently lost. This dialog names that
 * consequence explicitly before the destructive `initSchema(force: true)` call
 * runs. Cancelling (or dismissing) never calls the backend, so annotations are
 * preserved. Composed from the shared Modal + InlineNotice conventions (C-01).
 */
export function SchemaReinitDialog({
  isOpen,
  target,
  isLoading,
  onConfirm,
  onClose,
}: SchemaReinitDialogProps) {
  const handleOpenChange = (open: boolean) => {
    // Never close mid-flight; a re-init in progress should run to completion.
    if (!open && !isLoading) {
      onClose();
    }
  };

  return (
    <Modal open={isOpen} onOpenChange={handleOpenChange}>
      <ModalContentContainer open={isOpen}>
        <ModalContent size="base">
          <VStack className="gap-5 p-6 items-stretch">
            <ModalTitle className="sr-only">Re-initialize semantic layer?</ModalTitle>
            <ModalDescription className="sr-only">
              Re-init overwrites the semantic layer for {target} and permanently deletes
              all annotations. This action cannot be undone.
            </ModalDescription>
            <VStack className="gap-1 items-start">
              <Text as="h2" level="headline-4" className="text-content-layout-1">
                Re-initialize semantic layer?
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Target: {target}
              </Text>
            </VStack>

            <InlineNotice
              accent="warning"
              icon="alert"
              title="This discards every annotation"
              message="Re-init rebuilds the layer from a fresh introspection and does not merge existing annotations. All table and column descriptions, enum meanings, business terms, metrics, and relationships — human and AI-authored — will be permanently deleted. This cannot be undone."
            />

            <Text level="body-small" className="text-content-layout-2">
              To pick up structural changes while keeping your annotations, cancel and
              use <span className="text-content-layout-1 font-medium">Refresh</span> instead.
            </Text>

            <HStack className="gap-3 justify-end">
              <Button
                variant="primary"
                modifier="ghost"
                label="Cancel"
                onClick={onClose}
                disabled={isLoading}
              />
              <Button
                variant="negative"
                modifier="solid"
                label="Discard & Re-init"
                onClick={onConfirm}
                loading={isLoading}
              />
            </HStack>
          </VStack>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}
