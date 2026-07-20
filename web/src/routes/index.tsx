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
    variant: "positive" | "warning" | "informative" | "primary" | "neutral";
    modifier?: "solid" | "outline" | "ghost";
  };
  /** The single "start here" action: full-width, raised (elevation-1), one
   *  primary accent — the page's visual anchor [VIS-012, VIS-097; home target]. */
  featured?: boolean;
}

function JobCard({ to, icon, title, description, chip, featured }: JobCardProps) {
  if (featured) {
    return (
      <Link
        to={to}
        className="group block rounded-2xl border border-border-primary-soft bg-surface-raised p-6 shadow-elevation-1 transition-shadow hover:shadow-elevation-2"
      >
        <VStack className="gap-3 items-start">
          <HStack className="gap-3 items-center w-full">
            <div className="w-11 h-11 rounded-xl bg-surface-primary-soft flex items-center justify-center shrink-0">
              <Icon name={icon} label="" className="w-6 h-6 text-content-primary-soft" />
            </div>
            <Text level="headline-5" className="text-content-layout-1 flex-1">
              {title}
            </Text>
            <Show when={!!chip}>
              <Tag
                size="small"
                variant={chip!.variant}
                modifier={chip!.modifier ?? "ghost"}
                label={chip!.label}
              />
            </Show>
          </HStack>
          <HStack className="gap-3 items-center w-full">
            <Text level="body-medium" className="text-content-layout-2 flex-1">
              {description}
            </Text>
            <Icon
              name="arrow-right"
              label=""
              className="w-5 h-5 text-content-primary-soft shrink-0 transition-transform group-hover:translate-x-0.5"
            />
          </HStack>
        </VStack>
      </Link>
    );
  }
  return (
    <Link
      to={to}
      className="group rounded-xl bg-surface-layout-1 p-5 transition-all hover:bg-surface-raised hover:shadow-elevation-1"
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
  // The Ask readiness chip: while the requirement is still resolving, show a
  // neutral "Checking…" — never an optimistic green "Ready" before data
  // confirms a key exists [USE-014, USE-063; home target].
  const askChip =
    anthropicRequirement === undefined
      ? { label: "Checking…", variant: "neutral" as const }
      : anthropicRequirement.satisfied
        ? { label: "Ready", variant: "positive" as const }
        : { label: "Needs an Anthropic API key", variant: "warning" as const };

  return (
    <div className="space-y-8 w-full">
      {/* Hero Header — render instantly; the entrance fade read as latency on
          a launcher you navigate constantly. [QW21] */}
      <m.div initial={false}>
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon name="dashboard" label="Home" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Understand, diagnose, and speed up your database
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              For Postgres and MySQL.
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

      {/* Job launcher — render at full opacity (no entrance fade on a hub you
          revisit constantly) [USE-008]. Hierarchy: 1 primary → 3 secondary →
          tertiary, so the eye lands on the one "start here" action. */}
      <Show when={hasTargets}>
        <m.div initial={false}>
          <VStack className="gap-6 items-stretch">
            {/* Region B — the single primary action: the broadest diagnostic */}
            <JobCard
              featured
              to="/audit"
              icon="document-validation"
              title="Run a health check"
              description="A full audit of your database: sizing, slow spots, and cache opportunities."
              chip={
                lastAudit
                  ? { label: `Last run ${formatTimestamp(lastAudit.started_at)}`, variant: "informative" }
                  : { label: "Never run — start here", variant: "primary" }
              }
            />

            {/* Region C — the rest of the work-on-my-database loop: three equal,
                quieter cards so the primary stays primary [VIS-016, VIS-111]. */}
            <div className="grid grid-cols-1 tablet:grid-cols-3 gap-4">
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
              <JobCard
                to="/ask"
                icon="sparkles"
                title="Ask a question"
                description="Ask in plain English and get answers straight from your data."
                chip={askChip}
              />
            </div>

            {/* Region D — tertiary: evaluate-Readyset + advanced-tools pointer,
                separated by space not a divider, kept the quietest [VIS-114]. */}
            <VStack className="gap-2 items-start pt-2">
              <Link
                to="/demo"
                className="group inline-flex items-center gap-1.5 text-content-layout-2 transition-colors hover:text-content-layout-1"
              >
                <Text as="span" level="body-small">
                  Evaluating Readyset?{" "}
                  <span className="text-content-layout-1 group-hover:underline">
                    Try the live demo
                  </span>
                </Text>
                <Icon name="arrow-right" label="" className="w-4 h-4 shrink-0" />
              </Link>
              <Text level="caption" className="text-content-layout-3">
                More tools (Agents, Guards, Fleet, Benchmark…) live under Advanced
                in the sidebar.
              </Text>
            </VStack>
          </VStack>
        </m.div>
      </Show>

    </div>
  );
}
