import { cn } from "@rs/tailwind-base";
import { Icon } from "@rs/ui-new/icon";
import { Text } from "@rs/ui-new/text";
import { HStack } from "@rs/ui-new/stack";
import { Link, useRouterState } from "@tanstack/react-router";

interface RouteConfig {
  label: string;
  icon: "speedometer" | "sparkles" | "observe" | "folder-file" | "play" | "layers" | "test-tube" | "settings";
  parent?: string;
}

const routeConfig: Record<string, RouteConfig> = {
  "/": { label: "Query Analyzer", icon: "speedometer" },
  "/results": { label: "Results", icon: "speedometer", parent: "/" },
  "/ask": { label: "Ask", icon: "sparkles" },
  "/top": { label: "Top Queries", icon: "observe" },
  "/query-registry": { label: "Query Registry", icon: "folder-file" },
  "/benchmark": { label: "Benchmark", icon: "play" },
  "/schema": { label: "Schema", icon: "layers" },
  "/readyset": { label: "Readyset Testing", icon: "test-tube" },
  "/configure": { label: "Configure", icon: "settings" },
};

export function Header() {
  const router = useRouterState();
  const currentPath = router.location.pathname;
  const config = routeConfig[currentPath];
  const currentLabel = config?.label || "RDST";
  const currentIcon = config?.icon || "speedometer";
  const parentPath = config?.parent;
  const parentConfig = parentPath ? routeConfig[parentPath] : null;

  return (
    <header
      className={cn(
        "h-14",
        "bg-surface-layout-1/80",
        "backdrop-blur-md",
        "border-b border-border-layout-1",
        "pl-64",
        "sticky top-0 z-20",
      )}
    >
      <HStack className="px-6 h-full items-center justify-between">
        {/* Breadcrumb */}
        <HStack className="items-center gap-2">
          <Link
            to="/"
            className="text-content-layout-3 hover:text-content-layout-1 transition-colors"
          >
            <Text level="label-small" className="font-semibold tracking-wide">
              RDST
            </Text>
          </Link>

          <Icon name="chevron-right" label="separator" className="w-3.5 h-3.5 text-content-layout-3" />

          {parentConfig && parentPath && (
            <>
              <Link
                to={parentPath}
                className="text-content-layout-3 hover:text-content-layout-1 transition-colors"
              >
                <Text level="label-small">
                  {parentConfig.label}
                </Text>
              </Link>
              <Icon name="chevron-right" label="separator" className="w-3.5 h-3.5 text-content-layout-3" />
            </>
          )}

          <HStack className="items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-surface-primary-soft flex items-center justify-center">
              <Icon name={currentIcon} label={currentLabel} className="w-3.5 h-3.5 text-content-primary-soft" />
            </div>
            <Text level="label-small" className="text-content-layout-1 font-medium">
              {currentLabel}
            </Text>
          </HStack>
        </HStack>
      </HStack>
    </header>
  );
}
