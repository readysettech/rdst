import { useState } from "react";
import { tv } from "@rs/tailwind-base";
import { Icon } from "@rs/ui-new/icon";
import { Text } from "@rs/ui-new/text";
import { Link, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { TargetDropdown } from "../components/TargetDropdown";
import { ReportDialog } from "../components/ReportDialog";
import { useTarget } from "../hooks/useTarget";
import { fetchStatus } from "../lib/api";

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

const navItems = [
  { label: "Query Analyzer", icon: "speedometer" as const, to: "/" },
  { label: "Ask", icon: "sparkles" as const, to: "/ask" },
  { label: "Top Queries", icon: "observe" as const, to: "/top" },
  {
    label: "Query Registry",
    icon: "folder-file" as const,
    to: "/query-registry",
  },
  { label: "Benchmark", icon: "play" as const, to: "/benchmark" },
  { label: "Schema", icon: "layers" as const, to: "/schema" },
  { label: "Configure", icon: "settings" as const, to: "/configure" },
];

export function Sidebar() {
  const router = useRouterState();
  const currentPath = router.location.pathname;
  const { target: selectedTarget, setTarget: setSelectedTarget } = useTarget();
  const [reportOpen, setReportOpen] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    staleTime: 60000,
  });

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
        {navItems.map((item) => {
          const isActive =
            currentPath === item.to ||
            (item.to === "/" && currentPath === "/results");

          return (
            <Link
              key={item.to}
              to={item.to}
              className={navItemStyles({ active: isActive })}
            >
              <Icon
                name={item.icon}
                label={item.label}
                className={`w-4 h-4 transition-transform group-hover:scale-110 ${
                  isActive
                    ? "text-content-primary-soft"
                    : "text-content-layout-3"
                }`}
              />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="p-3 border-t border-border-layout-1 space-y-1">
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
