import { Button } from '@rs/ui-new/button'
import {
  Drawer,
  DrawerContent,
  DrawerContentContainer,
  DrawerHeader,
  DrawerTitle,
} from '@rs/ui-new/drawer'
import { HStack } from '@rs/ui-new/stack'
import {
  AnalysisConversation,
  type AnalysisConversationContext,
  useAnalysisConversation,
} from './AnalysisConversation'

interface InteractivePanelProps {
  isOpen: boolean
  onClose: () => void
  queryHash: string
  analysisResults?: AnalysisConversationContext
}

/**
 * The follow-up conversation as `/results` shows it: a drawer over the page.
 * The conversation inside it is `AnalysisConversation`, the same one the Query
 * Library's analyze drawer hosts on its Follow-up tab.
 */
export function InteractivePanel({
  isOpen,
  onClose,
  queryHash,
  analysisResults,
}: InteractivePanelProps) {
  const conversation = useAnalysisConversation(
    queryHash,
    analysisResults,
    isOpen
  )

  if (!isOpen) return null

  return (
    <Drawer
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      direction="right"
    >
      <DrawerContentContainer>
        <DrawerContent
          size="large"
          direction="right"
          className="p-0"
          hideCloseButton
        >
          <DrawerHeader className="border-b border-border-layout-1 p-4">
            <HStack className="w-full justify-between">
              <DrawerTitle>
                {conversation.hasPreviousChat
                  ? 'Continue conversation'
                  : 'Chat with AI'}
              </DrawerTitle>
              <HStack className="gap-1">
                {conversation.hasPreviousChat ? (
                  <Button
                    variant="primary"
                    label="Clear conversation"
                    modifier="ghost"
                    size="small"
                    icon="trash"
                    iconPosition="icon"
                    onClick={() => void conversation.clearConversation()}
                    disabled={conversation.isLoading}
                  />
                ) : null}
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

          <AnalysisConversation conversation={conversation} layout="panel" />
        </DrawerContent>
      </DrawerContentContainer>
    </Drawer>
  )
}
