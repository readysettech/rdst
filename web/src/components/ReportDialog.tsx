import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Text } from "@rs/ui-new/text";
import { Button } from "@rs/ui-new/button";
import { BaseInputTextarea } from "@rs/ui-new/base-input-textarea";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { BaseInputSwitch } from "@rs/ui-new/base-input-switch";
import { Dropdown } from "@rs/ui-new/dropdown";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { toast } from "@rs/ui-new/use-toast";
import { Modal, ModalContent, ModalContentContainer } from "@rs/ui-new/modal";
import { m, AnimatePresence } from "@rs/ui-new/motion";
import {
  fetchQueryRegistry,
  submitReport,
  type QueryRegistryEntry,
  type ReportRequest,
  type ReportResponse,
  type ReportSentiment,
} from "../lib/api";

interface ReportDialogProps {
  isOpen: boolean;
  onClose: () => void;
  initialQueryHash?: string;
}

type SentimentOption = {
  value: ReportSentiment;
  label: string;
  icon: string;
  color: string;
  bgColor: string;
  borderColor: string;
};

const sentimentOptions: SentimentOption[] = [
  {
    value: "positive",
    label: "Great",
    icon: "😊",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10",
    borderColor: "border-emerald-500/50",
  },
  {
    value: "neutral",
    label: "Okay",
    icon: "😐",
    color: "text-amber-400",
    bgColor: "bg-amber-500/10",
    borderColor: "border-amber-500/50",
  },
  {
    value: "negative",
    label: "Poor",
    icon: "😞",
    color: "text-rose-400",
    bgColor: "bg-rose-500/10",
    borderColor: "border-rose-500/50",
  },
];

export function ReportDialog({ isOpen, onClose, initialQueryHash }: ReportDialogProps) {
  const [sentiment, setSentiment] = useState<ReportSentiment>("neutral");
  const [selectedQueryHash, setSelectedQueryHash] = useState<string | null>(
    initialQueryHash || null,
  );
  const [includeQuery, setIncludeQuery] = useState(true);
  const [includePlan, setIncludePlan] = useState(true);
  const [reason, setReason] = useState("");
  const [email, setEmail] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);

  // Reset form when dialog opens
  useEffect(() => {
    if (isOpen) {
      setSentiment("neutral");
      setSelectedQueryHash(initialQueryHash || null);
      setIncludeQuery(true);
      setIncludePlan(true);
      setReason("");
      setEmail("");
    }
  }, [isOpen, initialQueryHash]);

  // Fetch recent queries for dropdown
  const { data: queries = [] } = useQuery<
    { queries: QueryRegistryEntry[] },
    Error,
    QueryRegistryEntry[]
  >({
    queryKey: ["queryRegistry", 10],
    queryFn: () => fetchQueryRegistry(10),
    select: (data) => data.queries,
    enabled: isOpen,
    staleTime: 30000,
  });

  // Find selected query details
  const selectedQuery = queries.find((q) => q.hash === selectedQueryHash);

  // Submit mutation
  const submitMutation = useMutation<ReportResponse, Error, ReportRequest>({
    mutationFn: (request: ReportRequest) =>
      submitReport(request).then((result) => {
        if (!result.success) {
          throw new Error(result.error || "An error occurred");
        }
        return result;
      }),
    onSuccess: () => {
      toast({
        title: "Thank you for your feedback!",
        description: "Your feedback helps us improve RDST.",
        variant: "positive",
      });
      onClose();
    },
    onError: (error) => {
      toast({
        title: "Failed to submit feedback",
        description: error instanceof Error ? error.message : "An error occurred",
        variant: "negative",
      });
    },
  });

  const handleSubmit = () => {
    if (!reason.trim()) {
      toast({
        title: "Please enter your feedback",
        variant: "warning",
      });
      return;
    }

    submitMutation.mutate({
      reason: reason.trim(),
      sentiment,
      query_hash: selectedQueryHash || undefined,
      email: email.trim() || undefined,
      include_query: selectedQueryHash ? includeQuery : undefined,
      include_plan: selectedQueryHash ? includePlan : undefined,
    });
  };

  const truncateSQL = (sql: string, maxLength = 50) => {
    const singleLine = sql.replace(/\s+/g, " ").trim();
    if (singleLine.length <= maxLength) return singleLine;
    return singleLine.slice(0, maxLength) + "...";
  };

  const currentSentiment = sentimentOptions.find((s) => s.value === sentiment);

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent
          size="base"
          className="gap-0 p-0 overflow-hidden"
          title="Share Your Feedback"
          description="Help us improve RDST for everyone"
        >
          {/* Header with gradient */}
          <m.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
            className="relative px-6 pt-6 pb-5 bg-gradient-to-br from-surface-layout-2 to-surface-layout-1"
          >
            <div className="absolute inset-0 bg-gradient-to-br from-surface-primary-soft/5 to-transparent" />
            <HStack className="gap-4 items-center relative">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center shadow-lg">
                <Icon
                  name="message-multiple"
                  label="Feedback"
                  className="w-6 h-6 text-content-primary-soft"
                />
              </div>
              <VStack className="gap-0.5 items-start">
                <Text level="headline-4" className="text-content-layout-1">
                  Share Your Feedback
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Help us improve RDST for everyone
                </Text>
              </VStack>
            </HStack>
          </m.div>

          {/* Content */}
          <div className="p-6 space-y-5">
            {/* Sentiment Selection */}
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.05 }}
            >
              <Text level="label-small" className="text-content-layout-2 mb-3">
                How was your experience?
              </Text>
              <div className="grid grid-cols-3 gap-3">
                {sentimentOptions.map((option, index) => {
                  const isSelected = sentiment === option.value;
                  return (
                    <m.button
                      key={option.value}
                      type="button"
                      onClick={() => setSentiment(option.value)}
                      initial={{ opacity: 0, scale: 0.9 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ duration: 0.2, delay: 0.1 + index * 0.05 }}
                      whileHover={{ scale: 1.02 }}
                      whileTap={{ scale: 0.98 }}
                      className={`relative flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all duration-200 cursor-pointer ${
                        isSelected
                          ? `${option.bgColor} ${option.borderColor}`
                          : "bg-surface-layout-2 border-transparent hover:border-border-layout-2"
                      }`}
                    >
                      <span className="text-2xl">{option.icon}</span>
                      <Text
                        level="label-small"
                        className={isSelected ? option.color : "text-content-layout-2"}
                      >
                        {option.label}
                      </Text>
                      {isSelected && (
                        <m.div
                          layoutId="sentiment-indicator"
                          className={`absolute -top-1 -right-1 w-5 h-5 rounded-full ${option.bgColor} border ${option.borderColor} flex items-center justify-center`}
                        >
                          <Icon
                            name="tick"
                            label="Selected"
                            className={`w-3 h-3 ${option.color}`}
                          />
                        </m.div>
                      )}
                    </m.button>
                  );
                })}
              </div>
            </m.div>

            {/* Query Selection Dropdown */}
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.1 }}
            >
              <Text level="label-small" className="text-content-layout-2 mb-2">
                Related query{" "}
                <span className="text-content-layout-3 font-normal">(optional)</span>
              </Text>
              <Dropdown open={dropdownOpen} onOpenChange={setDropdownOpen}>
                <Dropdown.Trigger asChild>
                  <div className="flex justify-between items-center bg-surface-layout-2 border border-border-layout-1 rounded-xl px-4 py-3 cursor-pointer hover:border-border-layout-2 hover:bg-surface-layout-3 transition-all duration-200 group">
                    <div className="flex-1 min-w-0">
                      {selectedQuery ? (
                        <HStack className="gap-3 items-center">
                          <div className="w-8 h-8 rounded-lg bg-surface-primary-soft/50 flex items-center justify-center shrink-0">
                            <Icon
                              name="querypilot"
                              label="Query"
                              className="w-4 h-4 text-content-primary-soft"
                            />
                          </div>
                          <VStack className="gap-0 items-start min-w-0">
                            <Text level="label-small" className="text-content-layout-1">
                              {selectedQuery.tag || `Query ${selectedQuery.hash.slice(0, 8)}`}
                            </Text>
                            <Text
                              level="mono-small"
                              className="text-content-layout-3 truncate block max-w-[280px]"
                            >
                              {truncateSQL(selectedQuery.sql)}
                            </Text>
                          </VStack>
                        </HStack>
                      ) : (
                        <HStack className="gap-3 items-center">
                          <div className="w-8 h-8 rounded-lg bg-surface-layout-3 flex items-center justify-center shrink-0">
                            <Icon
                              name="sparkles"
                              label="General"
                              className="w-4 h-4 text-content-layout-3"
                            />
                          </div>
                          <Text level="body-small" className="text-content-layout-3">
                            None — General feedback
                          </Text>
                        </HStack>
                      )}
                    </div>
                    <Icon
                      name="chevron-down"
                      label="Select query"
                      size="small"
                      className="text-content-layout-3 ml-2 shrink-0 transition-transform duration-200 group-hover:translate-y-0.5"
                    />
                  </div>
                </Dropdown.Trigger>
                <Dropdown.Content align="start" className="w-[calc(100%-2rem)] max-w-[468px]">
                  <Dropdown.Item
                    label="None - General feedback"
                    onClick={() => {
                      setSelectedQueryHash(null);
                      setDropdownOpen(false);
                    }}
                  />
                  {queries.length > 0 && <Dropdown.Separator />}
                  {queries.map((query: QueryRegistryEntry) => (
                    <Dropdown.ItemWithChildren
                      key={query.hash}
                      onClick={() => {
                        setSelectedQueryHash(query.hash);
                        setDropdownOpen(false);
                      }}
                    >
                      <div className="space-y-0.5 py-1">
                        <Text level="label-small" className="text-content-layout-1">
                          {query.tag || `Query ${query.hash.slice(0, 8)}`}
                        </Text>
                        <Text
                          level="mono-small"
                          className="text-content-layout-3 truncate block max-w-full"
                        >
                          {truncateSQL(query.sql, 60)}
                        </Text>
                      </div>
                    </Dropdown.ItemWithChildren>
                  ))}
                  {queries.length === 0 && (
                    <div className="px-3 py-2">
                      <Text level="body-small" className="text-content-layout-3">
                        No queries in registry
                      </Text>
                    </div>
                  )}
                </Dropdown.Content>
              </Dropdown>
            </m.div>

            {/* Include Options (shown when query selected) */}
            <AnimatePresence>
              {selectedQueryHash && (
                <m.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="bg-surface-layout-2 rounded-xl p-4 space-y-3 border border-border-layout-1">
                    <HStack className="gap-2 items-center mb-1">
                      <Icon
                        name="connect"
                        label="Attachments"
                        className="w-4 h-4 text-content-layout-3"
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Include with feedback
                      </Text>
                    </HStack>
                    <div className="flex items-center justify-between py-1">
                      <HStack className="gap-2 items-center">
                        <Icon
                          name="querypilot"
                          label="SQL"
                          className="w-4 h-4 text-content-layout-3"
                        />
                        <Text level="body-small" className="text-content-layout-2">
                          Query SQL
                        </Text>
                      </HStack>
                      <BaseInputSwitch
                        name="include-query"
                        checked={includeQuery}
                        onCheckedChange={setIncludeQuery}
                      />
                    </div>
                    <div className="h-px bg-border-layout-1" />
                    <div className="flex items-center justify-between py-1">
                      <HStack className="gap-2 items-center">
                        <Icon name="layers" label="Plan" className="w-4 h-4 text-content-layout-3" />
                        <Text level="body-small" className="text-content-layout-2">
                          Execution plan
                        </Text>
                      </HStack>
                      <BaseInputSwitch
                        name="include-plan"
                        checked={includePlan}
                        onCheckedChange={setIncludePlan}
                      />
                    </div>
                  </div>
                </m.div>
              )}
            </AnimatePresence>

            {/* Feedback Text */}
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.15 }}
            >
              <Text level="label-small" className="text-content-layout-2 mb-2">
                {sentiment === "positive"
                  ? "What did RDST do well?"
                  : sentiment === "negative"
                    ? "What went wrong? How can we improve?"
                    : "Tell us about your experience"}
              </Text>
              <BaseInputTextarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Share your thoughts..."
                rows={4}
                className="resize-none"
              />
            </m.div>

            {/* Email */}
            <m.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: 0.2 }}
            >
              <Text level="label-small" className="text-content-layout-2 mb-2">
                Email{" "}
                <span className="text-content-layout-3 font-normal">(optional)</span>
              </Text>
              <BaseInputText
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                type="email"
              />
              <Text level="caption" className="text-content-layout-3 mt-1.5">
                We&apos;ll only reach out if we have follow-up questions
              </Text>
            </m.div>
          </div>

          {/* Footer */}
          <m.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3, delay: 0.25 }}
            className="px-6 py-4 bg-surface-layout-2 border-t border-border-layout-1 flex justify-between items-center"
          >
            <Button
              modifier="ghost"
              label="Cancel"
              onClick={onClose}
              disabled={submitMutation.isPending}
            />
            <Button
              variant={currentSentiment?.value === "positive" ? "rising" : "primary"}
              label={submitMutation.isPending ? "Sending..." : "Send Feedback"}
              icon="arrow-up-right"
              iconPosition="right"
              onClick={handleSubmit}
              loading={submitMutation.isPending}
              disabled={submitMutation.isPending || !reason.trim()}
            />
          </m.div>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  );
}
