import { useState } from "react";
import { tv } from "@rs/tailwind-base";
import { Icon } from "@rs/ui-new/icon";
import type { IconStrokeName } from "@rs/ui-icons/icon-name";
import { Text } from "@rs/ui-new/text";
import { Link, useRouterState } from "@tanstack/react-router";
import { TargetDropdown } from "../components/TargetDropdown";
import { ReportDialog } from "../components/ReportDialog";
import { useTarget } from "../hooks/useTarget";
import { useSystemStatus } from "../lib/useSystemStatus";
import { TrialBalanceBadge } from "../components/TrialBalanceBadge";

const sidebarStyles = tv({
  base: [
    "bg-surface-layout-1",
    "h-dvh",
    "w-64",
    "flex",
    "flex-col",
    "fixed",
    "border-r border-border-layout-1",
    "left-0",
    "top-0",
    "z-30",
  ],
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

const primaryItems: NavItem[] = [
  { label: "Home", icon: "dashboard", to: "/" },
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
  { label: "Configure", icon: "settings", to: "/configure" },
];

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

export function Sidebar() {
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

  return (
    <aside className={sidebarStyles()}>
      {/* Target selector */}
      <div className="h-14 border-b border-border-layout-1">
        <TargetDropdown
          selectedTarget={selectedTarget}
          onSelectTarget={setSelectedTarget}
        />
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-1 p-3 flex-1 overflow-y-auto">
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

      {/* Footer */}
      <div className="p-3 border-t border-border-layout-1 space-y-2">
        <TrialBalanceBadge />
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
