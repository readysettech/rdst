import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import type { IconStrokeName } from "@rs/ui-icons/icon-name";
import { Tag } from "@rs/ui-new/tag";
import { Button } from "@rs/ui-new/button";
import { Show } from "@rs/ui-new/show";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m } from "@rs/ui-new/motion";
import { useTarget } from "../hooks/useTarget";
import { useSystemStatus } from "../lib/useSystemStatus";
import { useTrialSource } from "../lib/trialQueries";
import { fetchAuditRuns } from "../lib/useAudit";
import { fetchQueryRegistry } from "../lib/api";
import { formatTimestamp } from "../lib/formatters";

export const Route = createFileRoute("/")({
  component: HomePage,
});

interface JobCardProps {
  to: string;
  icon: IconStrokeName;
  title: string;
  description: string;
  chip?: {
    label: string;
    variant: "positive" | "warning" | "informative" | "primary";
    modifier?: "solid" | "outline" | "ghost";
  };
}

function JobCard({ to, icon, title, description, chip }: JobCardProps) {
  return (
    <Link
      to={to}
      className="group rounded-xl border border-border-layout-1 bg-surface-layout-1 p-5 hover:border-border-layout-2 hover:bg-surface-layout-2/50 transition-colors"
    >
      <VStack className="gap-3 items-start h-full">
        <HStack className="gap-3 items-center w-full">
          <div className="w-10 h-10 rounded-xl bg-surface-primary-soft/40 flex items-center justify-center shrink-0">
            <Icon name={icon} label="" className="w-5 h-5 text-content-primary-soft" />
          </div>
          <Text level="label-large" className="text-content-layout-1 flex-1">
            {title}
          </Text>
          <Icon
            name="arrow-right"
            label=""
            className="w-4 h-4 text-content-layout-3 opacity-0 group-hover:opacity-100 transition-opacity"
          />
        </HStack>
        <Text level="body-small" className="text-content-layout-3">
          {description}
        </Text>
        <Show when={!!chip}>
          <Tag
            size="small"
            variant={chip!.variant}
            modifier={chip!.modifier ?? "ghost"}
            label={chip!.label}
          />
        </Show>
      </VStack>
    </Link>
  );
}

function HomePage() {
  const navigate = useNavigate();
  const { target } = useTarget();
  const { data: status } = useSystemStatus();
  const { anthropicRequirement } = useTrialSource();

  const hasTargets = (status?.targets?.length ?? 0) > 0;

  const { data: auditRuns } = useQuery({
    queryKey: ["home", "audit-runs"],
    queryFn: () => fetchAuditRuns(),
    staleTime: 60_000,
    enabled: hasTargets,
  });
  const { data: registry } = useQuery({
    queryKey: ["home", "registry-count"],
    queryFn: () => fetchQueryRegistry(1),
    staleTime: 60_000,
    enabled: hasTargets,
  });

  const lastAudit = auditRuns?.runs?.[0];
  const savedCount = registry?.total ?? 0;
  // The requirement descriptor is always present; `satisfied` says whether a
  // key actually exists (env var, keyring, or active trial).
  const needsApiKey = anthropicRequirement ? !anthropicRequirement.satisfied : false;

  return (
    <div className="space-y-8 w-full">
      {/* Hero Header */}
      <m.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon name="dashboard" label="Home" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Welcome to RDST
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Understand, diagnose, and speed up your database.
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {/* First-run: no targets configured yet. Requires a successful status
          fetch so a backend outage doesn't masquerade as a fresh install. */}
      <Show when={!!status && !hasTargets}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          <div className="rounded-xl border border-border-layout-1 bg-surface-layout-1 p-10">
            <VStack className="gap-4 items-center text-center">
              <div className="w-14 h-14 rounded-2xl bg-surface-layout-2 flex items-center justify-center">
                <Icon name="database" label="" className="w-7 h-7 text-content-layout-3" />
              </div>
              <VStack className="gap-2 items-center">
                <Text level="headline-5" className="text-content-layout-1">
                  Connect your first database
                </Text>
                <Text level="body-small" className="text-content-layout-3 max-w-md">
                  Everything in RDST works against a database target. The setup
                  wizard walks you through connecting one in about a minute.
                </Text>
              </VStack>
              <Button
                variant="primary"
                modifier="solid"
                label="Connect a database"
                icon="arrow-right"
                iconPosition="right"
                onClick={() => navigate({ to: "/onboarding" })}
              />
            </VStack>
          </div>
        </m.div>
      </Show>

      {/* Job launcher */}
      <Show when={hasTargets}>
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
        >
          <VStack className="gap-4 items-stretch">
            <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
              What do you want to do?
            </Text>
            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
              <JobCard
                to="/demo"
                icon="querypilot"
                title="See Readyset Platform in action"
                description="See QueryPilot cache your hottest queries in real time as Readyset and Postgres run the same workload side by side. Local containers, one-click cleanup."
                chip={{ label: "Try it", variant: "positive", modifier: "solid" }}
              />
              <JobCard
                to="/ask"
                icon="sparkles"
                title="Ask a question"
                description="Ask in plain English and get answers straight from your data."
                chip={
                  needsApiKey
                    ? { label: "Needs an Anthropic API key", variant: "warning" }
                    : { label: "Ready", variant: "positive" }
                }
              />
              <JobCard
                to="/audit"
                icon="document-validation"
                title="Run a health check"
                description="A full audit of your database: sizing, slow spots, and cache opportunities."
                chip={
                  lastAudit
                    ? { label: `Last run ${formatTimestamp(lastAudit.started_at)}`, variant: "informative" }
                    : { label: "Never run - start here", variant: "primary" }
                }
              />
              <JobCard
                to="/top"
                icon="observe"
                title="Find slow queries"
                description="See which queries are eating your database time right now."
                chip={
                  target
                    ? { label: `Against ${target}`, variant: "informative" }
                    : { label: "Select a target first", variant: "warning" }
                }
              />
              <JobCard
                to="/analyze"
                icon="speedometer"
                title="Analyze a query"
                description="Paste a SQL query for an execution plan and AI optimization advice."
                chip={
                  savedCount > 0
                    ? { label: `${savedCount} saved queries`, variant: "informative" }
                    : { label: "No saved queries yet", variant: "primary" }
                }
              />
            </div>
            <Text level="caption" className="text-content-layout-3">
              Looking for Agents, Guards, Fleet, or Benchmark? They live under
              Advanced in the sidebar.
            </Text>
          </VStack>
        </m.div>
      </Show>

    </div>
  );
}
