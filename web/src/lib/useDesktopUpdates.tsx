import { useEffect, useRef } from "react";
import { Button } from "@rs/ui-new/button";
import { toast } from "@rs/ui-new/use-toast";

import { getDesktopUpdates, type DesktopUpdateState } from "./desktop";

/**
 * Surfaces desktop shell update notifications as toasts: a restart action
 * when the shell downloaded the update in place, or download links when
 * the install format requires a manual download.
 */
export function useDesktopUpdates(): void {
  const notifiedVersion = useRef<string | null>(null);

  useEffect(() => {
    const updates = getDesktopUpdates();
    if (!updates) return;

    const notify = (state: DesktopUpdateState | null) => {
      if (!state || state.version === notifiedVersion.current) return;
      notifiedVersion.current = state.version;

      if (state.status === "ready") {
        toast({
          title: `RDST Desktop ${state.version} is ready`,
          description: "Restart the app to apply the update.",
          duration: Infinity,
          action: (
            <Button
              size="small"
              label="Restart now"
              onClick={() => updates.install()}
            />
          ),
        });
        return;
      }

      toast({
        title: `RDST Desktop ${state.version} is available`,
        duration: Infinity,
        description: (
          <span>
            Download:{" "}
            {state.downloadLinks.map((link, index) => (
              <span key={link.url}>
                {index > 0 && ", "}
                <a
                  className="underline"
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {link.label}
                </a>
              </span>
            ))}
          </span>
        ),
      });
    };

    void updates.getState().then(notify);
    return updates.onStateChange(notify);
  }, []);
}
