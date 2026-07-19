import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn, tv } from "@rs/tailwind-base";
import { Icon } from "@rs/ui-new/icon";
import type { IconStrokeName } from "@rs/ui-icons/icon-name";
import { Scrollable } from "@rs/ui-new/scrollable";
import { Text } from "@rs/ui-new/text";
import { Link, useRouterState } from "@tanstack/react-router";
import { TargetDropdown } from "../components/TargetDropdown";
import { ReportDialog } from "../components/ReportDialog";
import { useTarget } from "../hooks/useTarget";
import { useSystemStatus } from "../lib/useSystemStatus";
import { TrialBalanceBadge } from "../components/TrialBalanceBadge";

// Plain-text acknowledgement of who is signed in; deliberately not a control.
function SidebarIdentity() {
  const { data } = useQuery({
    queryKey: ["settings", "email"],
    queryFn: async () => {
      const response = await fetch("/api/settings/email");
      if (!response.ok) return null;
      return (await response.json()) as {
        email: string | null;
        first_name: string | null;
        last_name: string | null;
      };
    },
    staleTime: 60_000,
  });
  if (!data?.email) return null;
  const name = [data.first_name, data.last_name].filter(Boolean).join(" ");
  return (
    <div className="px-3 py-1">
      {name && (
        <Text as="div" level="caption" className="truncate font-medium text-content-layout-2">
          {name}
        </Text>
      )}
      <Text as="div" level="caption" className="truncate text-content-layout-3">
        {data.email}
      </Text>
    </div>
  );
}

const sidebarStyles = tv({
  base: [
    "w-64",
    "flex",
    "flex-col",
    "border-r border-border-layout-1",
    "left-0",
    "z-30",
  ],
  variants: {
    isElectronMac: {
      true: [
        "absolute",
        "inset-y-0",
        "h-full",
        "bg-surface-layout-1/10",
        "backdrop-blur-2xl",
        "backdrop-saturate-150",
        "border-border-layout-1/45",
        "shadow-[inset_-1px_0_0_rgba(255,255,255,0.06)]",
      ],
      false: ["fixed", "top-0", "h-dvh", "bg-surface-layout-1"],
    },
  },
});

const navItemStyles = tv({
  base: [
    "flex",
    "items-center",
    "gap-3",
    "px-3",
    "py-2.5",
    "rounded-lg",
    "text-sm",
    "font-medium",
    "text-content-layout-2",
    "transition-all",
    "duration-150",
    "w-full",
    "hover:bg-surface-layout-2",
    "hover:text-content-layout-1",
    "group",
  ],
  variants: {
    active: {
      true: [
        "bg-surface-primary-soft",
        "text-content-primary-soft",
        "hover:bg-surface-primary-soft-hover",
        "hover:text-content-primary-soft",
      ],
    },
  },
});

interface NavItem {
  label: string;
  icon: IconStrokeName;
  to: string;
}

const homeItem: NavItem = { label: "Home", icon: "dashboard", to: "/" };

// Demo sits in its own section directly under Home, above Diagnose/Optimize.
const tryItSection: { title: string; items: NavItem[] } = {
  title: "Try it",
  items: [{ label: "Demo", icon: "querypilot", to: "/demo" }],
};

const primaryItems: NavItem[] = [
  { label: "Ask", icon: "sparkles", to: "/ask" },
];

const sections: Array<{ title: string; items: NavItem[] }> = [
  {
    title: "Diagnose",
    items: [
      { label: "Slow Queries", icon: "observe", to: "/top" },
      { label: "Health Check", icon: "document-validation", to: "/audit" },
      { label: "Analyze Query", icon: "speedometer", to: "/analyze" },
    ],
  },
  {
    title: "Optimize",
    items: [
      { label: "Caching", icon: "database-settings", to: "/cache" },
      { label: "Saved Queries", icon: "folder-file", to: "/query-registry" },
      { label: "Code Scan", icon: "search", to: "/scan" },
    ],
  },
];

const advancedItems: NavItem[] = [
  { label: "Agents", icon: "message-multiple", to: "/agents" },
  { label: "Guards", icon: "user-shield", to: "/guards" },
  { label: "Fleet", icon: "building", to: "/fleet" },
  { label: "Benchmark", icon: "play", to: "/benchmark" },
  { label: "Schema", icon: "layers", to: "/schema" },
];

// Configuration recedes off the daily nav: after first connect the only global
// config control is the target switcher (top) plus a quiet footer "Settings"
// utility, never a top-level or Advanced nav item (configure-and-identity
// step 2 / T17). [USE-030, USE-034]
const settingsItem: NavItem = { label: "Settings", icon: "settings", to: "/configure" };

const ADVANCED_STORAGE_KEY = "rdst-sidebar-advanced";

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  return (
    <Link to={item.to} className={navItemStyles({ active })}>
      <Icon
        name={item.icon}
        label={item.label}
        className={`w-4 h-4 transition-transform group-hover:scale-110 ${
          active ? "text-content-primary-soft" : "text-content-layout-3"
        }`}
      />
      <span>{item.label}</span>
    </Link>
  );
}

function SectionTitle({ title }: { title: string }) {
  return (
    <Text
      level="caption"
      className="text-content-layout-3 uppercase tracking-wider px-3 pt-3 pb-1"
    >
      {title}
    </Text>
  );
}

interface SidebarProps {
  isElectronMac?: boolean;
}

export function Sidebar({ isElectronMac = false }: SidebarProps) {
  const router = useRouterState();
  const currentPath = router.location.pathname;
  const { target: selectedTarget, setTarget: setSelectedTarget } = useTarget();
  const [reportOpen, setReportOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(
    () => localStorage.getItem(ADVANCED_STORAGE_KEY) === "open",
  );

  const { data: status } = useSystemStatus();

  const isActive = (item: NavItem) =>
    currentPath === item.to || (item.to === "/analyze" && currentPath === "/results");

  // Keep the active item visible when landing directly on an advanced route.
  const advancedActive = advancedItems.some(isActive);
  const showAdvanced = advancedOpen || advancedActive;

  const toggleAdvanced = () => {
    const next = !advancedOpen;
    setAdvancedOpen(next);
    localStorage.setItem(ADVANCED_STORAGE_KEY, next ? "open" : "closed");
  };

  // Match the sidebar surface at 10% over the transparent window so the
  // native glass tint reads through it.
  const macGlassStyle = isElectronMac
    ? {
        background:
          "color-mix(in oklab, var(--color-surface-layout-1) 10%, transparent)",
      }
    : undefined;

  return (
    <aside className={sidebarStyles({ isElectronMac })} style={macGlassStyle}>
      {/* On desktop mac the window traffic lights get their own draggable
          strip above the target selector. */}
      {isElectronMac && <div className="draggable-region h-8 shrink-0" />}

      {/* Target selector */}
      <div
        className={cn(
          "draggable-region h-14 border-b",
          isElectronMac ? "border-border-layout-1/45" : "border-border-layout-1",
        )}
      >
        <div className="no-drag h-full">
          <TargetDropdown
            selectedTarget={selectedTarget}
            onSelectTarget={setSelectedTarget}
          />
        </div>
      </div>

      {/* Navigation — persistent scrollbar so the Advanced group is
          discoverable/reachable below the fold at 1280×720. [QW2] */}
      <Scrollable className="flex-1" type="auto">
        <nav className="flex flex-col gap-1 p-3">
          <NavLink key={homeItem.to} item={homeItem} active={isActive(homeItem)} />

          <div key={tryItSection.title} className="flex flex-col gap-1">
            <SectionTitle title={tryItSection.title} />
            {tryItSection.items.map((item) => (
              <NavLink key={item.to} item={item} active={isActive(item)} />
            ))}
          </div>

          {primaryItems.map((item) => (
            <NavLink key={item.to} item={item} active={isActive(item)} />
          ))}

          {sections.map((section) => (
            <div key={section.title} className="flex flex-col gap-1">
              <SectionTitle title={section.title} />
              {section.items.map((item) => (
                <NavLink key={item.to} item={item} active={isActive(item)} />
              ))}
            </div>
          ))}

          <button
            type="button"
            onClick={toggleAdvanced}
            className="flex items-center gap-1.5 px-3 pt-3 pb-1 cursor-pointer text-content-layout-3 hover:text-content-layout-2 transition-colors"
          >
            <Text level="caption" className="uppercase tracking-wider inherit">
              Advanced
            </Text>
            <Icon
              name={showAdvanced ? "chevron-down" : "chevron-right"}
              label=""
              className="w-3 h-3"
            />
          </button>
          {showAdvanced && (
            <>
              {advancedItems.map((item) => (
                <NavLink key={item.to} item={item} active={isActive(item)} />
              ))}
              {import.meta.env.DEV && (
                <NavLink
                  item={{ label: "Dev Settings", icon: "test-tube", to: "/dev-settings" }}
                  active={currentPath === "/dev-settings"}
                />
              )}
            </>
          )}
        </nav>
      </Scrollable>

      {/* Footer */}
      <div className="p-3 border-t border-border-layout-1 space-y-2">
        <SidebarIdentity />
        <TrialBalanceBadge />
        {/* Settings recedes here as a quiet utility, out of the daily nav. */}
        <NavLink item={settingsItem} active={isActive(settingsItem)} />
        <button
          type="button"
          onClick={() => setReportOpen(true)}
          className={navItemStyles({ className: "cursor-pointer" })}
        >
          <Icon
            name="message-multiple"
            label="Give Feedback"
            className="w-4 h-4 text-content-layout-3 group-hover:scale-110 transition-transform"
          />
          <span>Give Feedback</span>
        </button>

        {status?.version && (
          <div className="px-3 py-2">
            <Text level="caption" className="text-content-layout-3">
              v{status.version}
            </Text>
          </div>
        )}
      </div>

      <ReportDialog isOpen={reportOpen} onClose={() => setReportOpen(false)} />
    </aside>
  );
}
