import { useEffect } from "react";
import { useInteractiveChat } from "../lib/chat";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";
import {
  Drawer,
  DrawerContentContainer,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
} from "@rs/ui-new/drawer";
import { Alert } from "@rs/ui-new/alert";
import { Scrollable } from "@rs/ui-new/scrollable";
import { VStack, HStack } from "@rs/ui-new/stack";
import { Button } from "@rs/ui-new/button";

interface InteractivePanelProps {
  isOpen: boolean;
  onClose: () => void;
  queryHash: string;
  analysisResults?: any;
}

export function InteractivePanel({
  isOpen,
  onClose,
  queryHash,
  analysisResults,
}: InteractivePanelProps) {
  const {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    isLoading,
    error,
    loadHistory,
    checkStatus,
    clearConversation,
    conversationStatus,
  } = useInteractiveChat(queryHash, analysisResults);

  useEffect(() => {
    if (isOpen) {
      checkStatus();
      loadHistory();
    }
  }, [isOpen, checkStatus, loadHistory]);

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      onClose();
    }
  };

  const handleClear = async () => {
    await clearConversation();
  };

  if (!isOpen) return null;

  const hasPreviousChat = conversationStatus?.exists && conversationStatus.totalExchanges > 0;

  return (
    <Drawer open={isOpen} onOpenChange={handleOpenChange} direction="right">
      <DrawerContentContainer>
        <DrawerContent size="large" direction="right" className="p-0" hideCloseButton>
          <DrawerHeader className="p-4 border-b border-border-layout-1">
            <HStack className="w-full justify-between">
              <DrawerTitle>
                {hasPreviousChat ? "Continue conversation" : "Chat with AI"}
              </DrawerTitle>
              <HStack className="gap-1">
                {hasPreviousChat && (
                  <Button
                    variant="primary"
                    label="Clear conversation"
                    modifier="ghost"
                    size="small"
                    icon="trash"
                    iconPosition="icon"
                    onClick={handleClear}
                    disabled={isLoading}
                  />
                )}
                <Button
                  variant="primary"
                  label="Close"
                  modifier="ghost"
                  size="small"
                  icon="close"
                  iconPosition="icon"
                  onClick={onClose}
                />
              </HStack>
            </HStack>
          </DrawerHeader>

          {/* Messages - scrollable area */}
          <Scrollable className="flex-1 p-4">
            <VStack className="h-full items-stretch">
              {error && (
                <Alert
                  variant="negative"
                  modifier="outline"
                  label={`Error: ${error.message}`}
                  icon="alert"
                  iconPosition="left"
                  className="mb-4"
                />
              )}
              <MessageList messages={messages} isStreaming={isLoading} />
            </VStack>
          </Scrollable>

          {/* Input - fixed at bottom */}
          <div className="p-4 border-t border-border-layout-1">
            <MessageInput
              value={input}
              onChange={handleInputChange}
              onSubmit={handleSubmit}
              isLoading={isLoading}
            />
          </div>
        </DrawerContent>
      </DrawerContentContainer>
    </Drawer>
  );
}
